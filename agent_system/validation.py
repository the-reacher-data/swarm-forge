"""Validate native agent and skill definitions without modifying their sources."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Any

import yaml

from agent_system.inventory import Catalog, _load_catalog


AGENT_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,48}[a-z0-9])$")
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
READ_ONLY_ROLE_PATTERN = re.compile(r"(?:^|-)(?:critic|reviewer|verifier)(?:-|$)")
CLAUDE_MODELS = frozenset({"inherit", "sonnet", "opus", "haiku"})
CLAUDE_COLORS = frozenset({"blue", "cyan", "green", "yellow", "magenta", "red"})
WRITE_CAPABLE_TOOLS = frozenset({"edit", "write", "notebookedit", "*"})
WORD_PATTERN = re.compile(r"\b[^\W_]+(?:[-'][^\W_]+)*\b", re.UNICODE)
CLAUDE_DESCRIPTION_PREFIX = "Use this agent when "
CLAUDE_TYPICAL_MARKER = "Typical triggers include "
CLAUDE_WHEN_POINTER_PATTERN = re.compile(
    r'See "When to invoke" in the (?:agent )?body(?: for worked scenarios)?\.$'
)


@dataclass(frozen=True, slots=True, order=True)
class Finding:
    """One deterministic validation failure."""

    code: str
    path: Path
    message: str


def _finding(code: str, path: Path, message: str) -> Finding:
    return Finding(code=code, path=path, message=message)


def _read_text(path: Path) -> tuple[str | None, list[Finding]]:
    try:
        return path.read_text(encoding="utf-8"), []
    except (OSError, UnicodeError) as error:
        return None, [_finding("unreadable-artifact", path, str(error))]


def _markdown_parts(path: Path) -> tuple[dict[str, Any] | None, str, list[Finding]]:
    text, findings = _read_text(path)
    if text is None:
        return None, "", findings
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return (
            None,
            normalized,
            [
                _finding(
                    "missing-frontmatter",
                    path,
                    "Markdown must start with YAML frontmatter",
                )
            ],
        )
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return (
            None,
            normalized,
            [
                _finding(
                    "unterminated-frontmatter", path, "YAML frontmatter is not closed"
                )
            ],
        )
    raw_frontmatter = normalized[4:end]
    body = normalized[end + 5 :]
    try:
        parsed = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as error:
        return (
            None,
            body,
            [
                _finding(
                    "invalid-frontmatter", path, f"invalid YAML frontmatter: {error}"
                )
            ],
        )
    if not isinstance(parsed, dict):
        return (
            None,
            body,
            [
                _finding(
                    "invalid-frontmatter", path, "YAML frontmatter must be a mapping"
                )
            ],
        )
    return parsed, body, []


def _required_string(
    metadata: Mapping[str, Any], field: str, path: Path, findings: list[Finding]
) -> str | None:
    value = metadata.get(field)
    if not isinstance(value, str) or not value.strip():
        findings.append(
            _finding(f"missing-{field.replace('_', '-')}", path, f"{field} is required")
        )
        return None
    return value.strip()


def _is_read_only_role(name: str | None) -> bool:
    return bool(name and READ_ONLY_ROLE_PATTERN.search(name))


def _tool_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.strip("[]").split(",") if part.strip()]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [item.strip() for item in value if item.strip()]
    return []


def _has_heading(body: str, alternatives: Sequence[str]) -> bool:
    headings = {
        re.sub(r"[*_:]+$", "", match.group(1).strip()).casefold()
        for match in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", body)
    }
    headings.update(
        re.sub(r"[*_:]+$", "", match.group(1).strip()).casefold()
        for match in re.finditer(r"(?m)^\*\*(.+?)\*\*:?\s*$", body)
    )
    return any(alternative.casefold() in headings for alternative in alternatives)


def _has_substantive_text(value: str, *, minimum_words: int = 2) -> bool:
    return len(WORD_PATTERN.findall(value)) >= minimum_words


def _typical_trigger_scenarios(description: str, pointer_start: int) -> list[str]:
    marker_start = description.find(CLAUDE_TYPICAL_MARKER)
    if marker_start < 0 or pointer_start <= marker_start:
        return []
    segment = description[
        marker_start + len(CLAUDE_TYPICAL_MARKER) : pointer_start
    ].strip()
    segment = segment.removesuffix(".").strip()
    if ";" in segment:
        scenarios = [part.strip() for part in segment.split(";")]
    elif re.search(r",\s+(?:and|or)\s+", segment):
        match = re.fullmatch(
            r"(?P<head>[^,]+(?:,\s*[^,]+){1,2}),\s+(?:and|or)\s+(?P<last>[^,]+)",
            segment,
        )
        if match is None:
            return []
        scenarios = [
            *(part.strip() for part in match.group("head").split(",")),
            match.group("last").strip(),
        ]
    else:
        scenarios = [part.strip() for part in re.split(r"\s+(?:and|or)\s+", segment)]
        if len(scenarios) != 2:
            return []
    if not 2 <= len(scenarios) <= 4:
        return []
    return scenarios if all(_has_substantive_text(item) for item in scenarios) else []


def validate_claude_agent(path: Path) -> list[Finding]:
    """Validate one Claude Markdown agent against the official authoring rubric."""
    path = Path(path)
    metadata, body, parse_findings = _markdown_parts(path)
    if metadata is None:
        return sorted(parse_findings)

    findings: list[Finding] = list(parse_findings)
    name = _required_string(metadata, "name", path, findings)
    description = _required_string(metadata, "description", path, findings)
    model = _required_string(metadata, "model", path, findings)
    color = _required_string(metadata, "color", path, findings)

    if name is not None:
        if not AGENT_NAME_PATTERN.fullmatch(name):
            findings.append(
                _finding(
                    "invalid-agent-name",
                    path,
                    "agent name must be 3-50 lowercase letters, digits, or hyphens",
                )
            )
        if path.stem != name:
            findings.append(
                _finding(
                    "agent-name-path-mismatch",
                    path,
                    f"frontmatter name {name!r} does not match filename {path.stem!r}",
                )
            )

    if description is not None:
        if len(description) < 10 or len(description) > 5_000:
            findings.append(
                _finding(
                    "invalid-description-length",
                    path,
                    "description must contain 10-5000 characters",
                )
            )
        has_trigger_prefix = description.startswith(CLAUDE_DESCRIPTION_PREFIX)
        typical_start = description.find(CLAUDE_TYPICAL_MARKER)
        loose_trigger_start = description.find("Use this agent when")
        loose_typical_start = description.find("Typical triggers include")
        pointer = CLAUDE_WHEN_POINTER_PATTERN.search(description)
        if not has_trigger_prefix:
            findings.append(
                _finding(
                    "description-missing-trigger",
                    path,
                    'description must state "Use this agent when"',
                )
            )
            if loose_trigger_start == 0 and loose_typical_start > 0:
                conditions = description[
                    len("Use this agent when") : loose_typical_start
                ]
                if not _has_substantive_text(conditions):
                    findings.append(
                        _finding(
                            "description-missing-trigger-conditions",
                            path,
                            'description must state conditions after "Use this agent when"',
                        )
                    )
        elif typical_start >= len(CLAUDE_DESCRIPTION_PREFIX):
            conditions = description[len(CLAUDE_DESCRIPTION_PREFIX) : typical_start]
            if not _has_substantive_text(conditions):
                findings.append(
                    _finding(
                        "description-missing-trigger-conditions",
                        path,
                        'description must state conditions after "Use this agent when"',
                    )
                )
        else:
            conditions = description[len(CLAUDE_DESCRIPTION_PREFIX) :]
            if not _has_substantive_text(conditions):
                findings.append(
                    _finding(
                        "description-missing-trigger-conditions",
                        path,
                        'description must state conditions after "Use this agent when"',
                    )
                )
        if typical_start < len(CLAUDE_DESCRIPTION_PREFIX) and loose_typical_start < 0:
            findings.append(
                _finding(
                    "description-missing-typical-triggers",
                    path,
                    'description must summarize scenarios after "Typical triggers include"',
                )
            )
        elif (
            typical_start < len(CLAUDE_DESCRIPTION_PREFIX)
            or pointer is None
            or not _typical_trigger_scenarios(description, pointer.start())
        ):
            findings.append(
                _finding(
                    "description-insufficient-typical-triggers",
                    path,
                    'description must provide at least two scenarios after "Typical triggers include"',
                )
            )
        if pointer is None:
            findings.append(
                _finding(
                    "description-missing-when-pointer",
                    path,
                    'description must point to the body section "When to invoke"',
                )
            )

    if model is not None and model not in CLAUDE_MODELS:
        findings.append(
            _finding("invalid-model", path, f"unsupported Claude model {model!r}")
        )
    if color is not None and color not in CLAUDE_COLORS:
        findings.append(
            _finding("invalid-color", path, f"unsupported Claude color {color!r}")
        )

    if _is_read_only_role(name) and "tools" not in metadata:
        findings.append(
            _finding(
                "read-only-agent-missing-tool-restriction",
                path,
                "read-only agent must explicitly restrict its tools",
            )
        )
    if "tools" in metadata:
        tools = _tool_names(metadata["tools"])
        if not tools:
            findings.append(
                _finding(
                    "invalid-tools", path, "tools must be a non-empty list of names"
                )
            )
        if _is_read_only_role(name):
            write_tools = sorted(
                tool for tool in tools if tool.casefold() in WRITE_CAPABLE_TOOLS
            )
            if write_tools:
                findings.append(
                    _finding(
                        "read-only-agent-has-write-tool",
                        path,
                        "read-only agent grants write-capable tools: "
                        + ", ".join(write_tools),
                    )
                )

    if len(body.strip()) < 20:
        findings.append(
            _finding(
                "agent-body-too-short",
                path,
                "agent body must contain at least 20 characters",
            )
        )
    if len(body) > 10_000:
        findings.append(
            _finding(
                "agent-body-too-long",
                path,
                "agent body must not exceed 10000 characters",
            )
        )
    if not re.search(
        r"\b(?:you|your|yours|eres|debes|deberás|usted|tu|tus)\b",
        body,
        flags=re.IGNORECASE,
    ):
        findings.append(
            _finding(
                "body-not-second-person",
                path,
                "agent body must address the agent in second person",
            )
        )

    required_sections = (
        ("missing-when-to-invoke", ("When to invoke", "Cuándo invocarlo")),
        (
            "missing-core-responsibilities",
            (
                "Core responsibilities",
                "Your core responsibilities",
                "Responsabilidades",
            ),
        ),
        ("missing-process", ("Process", "Analysis process", "Proceso")),
        (
            "missing-quality-standards",
            ("Quality standards", "Estándares de calidad", "Criterios de calidad"),
        ),
        ("missing-output-format", ("Output format", "Formato de salida")),
        ("missing-edge-cases", ("Edge cases", "Casos límite", "Casos de borde")),
    )
    for code, alternatives in required_sections:
        if not _has_heading(body, alternatives):
            findings.append(
                _finding(
                    code,
                    path,
                    f"agent body requires a {alternatives[0]!r} section",
                )
            )
    return sorted(findings)


def validate_codex_agent(path: Path) -> list[Finding]:
    """Validate one native Codex TOML agent."""
    path = Path(path)
    try:
        with path.open("rb") as source:
            metadata = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as error:
        return [_finding("invalid-codex-toml", path, str(error))]

    findings: list[Finding] = []
    name = _required_string(metadata, "name", path, findings)
    description = _required_string(metadata, "description", path, findings)
    instructions = _required_string(metadata, "developer_instructions", path, findings)

    if name is not None:
        if not AGENT_NAME_PATTERN.fullmatch(name):
            findings.append(
                _finding(
                    "invalid-agent-name",
                    path,
                    "agent name must be 3-50 lowercase letters, digits, or hyphens",
                )
            )
        if path.stem != name:
            findings.append(
                _finding(
                    "agent-name-path-mismatch",
                    path,
                    f"TOML name {name!r} does not match filename {path.stem!r}",
                )
            )
    if description is not None and not 10 <= len(description) <= 5_000:
        findings.append(
            _finding(
                "invalid-description-length",
                path,
                "description must contain 10-5000 characters",
            )
        )
    if instructions is not None and not 20 <= len(instructions) <= 10_000:
        findings.append(
            _finding(
                "invalid-developer-instructions-length",
                path,
                "developer_instructions must contain 20-10000 characters",
            )
        )
    if _is_read_only_role(name) and metadata.get("sandbox_mode") != "read-only":
        findings.append(
            _finding(
                "missing-read-only-sandbox",
                path,
                'critics, reviewers, and verifiers require sandbox_mode = "read-only"',
            )
        )
    return sorted(findings)


def _find_quick_validate() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return (
            Path(codex_home) / "skills/.system/skill-creator/scripts/quick_validate.py"
        )
    return Path.home() / ".codex/skills/.system/skill-creator/scripts/quick_validate.py"


def validate_skill(
    path: Path, *, quick_validate_path: Path | None = None
) -> list[Finding]:
    """Validate a skill directory or SKILL.md without changing it."""
    supplied_path = Path(path)
    skill_file = supplied_path / "SKILL.md" if supplied_path.is_dir() else supplied_path
    skill_dir = skill_file.parent
    if skill_file.name != "SKILL.md":
        return [
            _finding(
                "invalid-skill-path",
                supplied_path,
                "skill path must be a SKILL.md file or directory",
            )
        ]

    metadata, body, parse_findings = _markdown_parts(skill_file)
    if metadata is None:
        return sorted(parse_findings)
    findings: list[Finding] = list(parse_findings)
    name = _required_string(metadata, "name", skill_file, findings)
    description = _required_string(metadata, "description", skill_file, findings)

    if name is not None:
        if len(name) > 64 or not SKILL_NAME_PATTERN.fullmatch(name):
            findings.append(
                _finding(
                    "invalid-skill-name",
                    skill_file,
                    "skill name must be at most 64 lowercase letters, digits, or hyphens",
                )
            )
        if skill_dir.name != name:
            findings.append(
                _finding(
                    "skill-name-folder-mismatch",
                    skill_file,
                    f"skill name {name!r} does not match folder {skill_dir.name!r}",
                )
            )
    if description is not None:
        if len(description) > 1_024:
            findings.append(
                _finding(
                    "skill-description-too-long",
                    skill_file,
                    "skill description must not exceed 1024 characters",
                )
            )
        if not description.startswith("Use when "):
            findings.append(
                _finding(
                    "skill-description-missing-discovery-trigger",
                    skill_file,
                    'skill description must start exactly with "Use when"',
                )
            )
        if re.search(r"\b(?:I|me|my|you|your|we|our|us)\b", description, re.IGNORECASE):
            findings.append(
                _finding(
                    "skill-description-not-third-person",
                    skill_file,
                    "skill description must express conditions in third person",
                )
            )

    instructional_body = re.sub(r"(?m)^\s*#{1,6}\s+.*$", "", body)
    instructional_body = re.sub(
        r"(?m)^\s*\*\*[^*\n]+\*\*:?\s*$", "", instructional_body
    )
    if not _has_substantive_text(instructional_body, minimum_words=3):
        findings.append(
            _finding(
                "skill-body-missing-instructions",
                skill_file,
                "skill body must contain substantive instructions beyond headings",
            )
        )

    validator = quick_validate_path or _find_quick_validate()
    if not validator.is_file() or not os.access(validator, os.R_OK):
        status = "does not exist" if not validator.is_file() else "is not readable"
        findings.append(
            _finding(
                "official-skill-validator-unavailable",
                skill_file,
                f"required official validator {validator} {status}",
            )
        )
    else:
        completed = subprocess.run(
            [sys.executable, str(validator), str(skill_dir)],
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            detail = (completed.stdout + "\n" + completed.stderr).strip()
            findings.append(
                _finding(
                    "official-skill-validation-failed",
                    skill_file,
                    detail or f"quick_validate.py exited {completed.returncode}",
                )
            )
    return sorted(findings)


def _portable_bases(catalog: Catalog) -> dict[str, Path]:
    bases = {"home": catalog.home, "catalog": catalog.catalog_dir}
    for root in catalog:
        bases.setdefault(f"root-{root.owner}", root.path.expanduser().resolve())
    return bases


def _portable_message(message: str, catalog: Catalog) -> str:
    portable = message
    ordered_bases = sorted(
        _portable_bases(catalog).items(),
        key=lambda item: len(item[1].as_posix()),
        reverse=True,
    )
    for token, base in ordered_bases:
        absolute = base.as_posix().rstrip("/")
        portable = portable.replace(f"{absolute}/", f"{token}:")
        portable = portable.replace(absolute, token)

    def replace_unknown_path(match: re.Match[str]) -> str:
        raw = match.group(0)
        suffix = ""
        while raw and raw[-1] in ".,;)]}":
            suffix = raw[-1] + suffix
            raw = raw[:-1]
        return f"absolute:{Path(raw).name}{suffix}"

    return re.sub(r"(?<![\w:])/(?:[^\s]+)", replace_unknown_path, portable)


def _resolve_portable(raw_path: str, catalog: Catalog) -> Path | None:
    token, separator, relative = raw_path.partition(":")
    if not separator or token not in _portable_bases(catalog):
        return None
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    return _portable_bases(catalog)[token] / relative_path


def _select_artifact_path(record: Mapping[str, Any], catalog: Catalog) -> Path | None:
    candidates = [record.get("source")]
    candidates.extend(
        installation.get("path")
        for installation in record.get("installations", [])
        if isinstance(installation, dict)
    )
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        resolved = _resolve_portable(candidate, catalog)
        if resolved is not None and resolved.exists():
            return resolved
    return None


def _artifact_runtime(
    record: Mapping[str, Any], path: Path, catalog: Catalog
) -> str | None:
    runtimes: list[str] = []
    for installation in record.get("installations", []):
        if not isinstance(installation, dict):
            continue
        raw_path = installation.get("path")
        runtime = installation.get("runtime")
        if not isinstance(raw_path, str) or not isinstance(runtime, str):
            continue
        resolved = _resolve_portable(raw_path, catalog)
        if resolved is not None and resolved == path:
            return runtime
        runtimes.append(runtime)
    distinct = set(runtimes)
    return next(iter(distinct)) if len(distinct) == 1 else None


def _validate_path(
    path: Path, *, kind: str, runtime: str | None = None
) -> list[Finding]:
    if kind == "skill":
        return validate_skill(path)
    if runtime == "codex" and path.suffix != ".toml":
        return [
            _finding(
                "invalid-codex-agent-extension",
                path,
                "Codex agents must use native TOML definitions",
            )
        ]
    if runtime == "claude" and path.suffix != ".md":
        return [
            _finding(
                "invalid-claude-agent-extension",
                path,
                "Claude agents must use native Markdown definitions",
            )
        ]
    if path.suffix == ".toml":
        return validate_codex_agent(path)
    return validate_claude_agent(path)


def _inventory_findings(
    inventory_path: Path, catalog_path: Path, *, first_party_only: bool
) -> tuple[list[Finding], int]:
    with inventory_path.open(encoding="utf-8") as source:
        manifest = yaml.safe_load(source)
    records = manifest.get("artifacts", []) if isinstance(manifest, dict) else []
    catalog = _load_catalog(catalog_path)
    findings: list[Finding] = []
    validated = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        if first_party_only and record.get("first_party") is not True:
            continue
        validated += 1
        portable_path = str(record.get("source", "<missing-source>"))
        path = _select_artifact_path(record, catalog)
        if path is None:
            findings.append(
                _finding(
                    "artifact-path-not-found",
                    Path(portable_path),
                    "source and installation paths do not resolve to an existing artifact",
                )
            )
            continue
        runtime = _artifact_runtime(record, path, catalog)
        for finding in _validate_path(
            path, kind=str(record.get("kind", "")), runtime=runtime
        ):
            findings.append(
                Finding(
                    finding.code,
                    Path(portable_path),
                    _portable_message(finding.message, catalog),
                )
            )
    return sorted(findings), validated


def _paths_findings(paths: Sequence[Path]) -> tuple[list[Finding], int]:
    artifacts: list[tuple[Path, str]] = []
    for supplied in paths:
        if supplied.is_dir() and (supplied / "SKILL.md").is_file():
            artifacts.append((supplied / "SKILL.md", "skill"))
        elif supplied.is_dir():
            for candidate in sorted(supplied.rglob("*")):
                if candidate.name == "SKILL.md":
                    artifacts.append((candidate, "skill"))
                elif candidate.is_file() and candidate.suffix in {".md", ".toml"}:
                    artifacts.append((candidate, "agent"))
        else:
            artifacts.append(
                (supplied, "skill" if supplied.name == "SKILL.md" else "agent")
            )
    findings: list[Finding] = []
    for path, kind in artifacts:
        findings.extend(_validate_path(path, kind=kind))
    return sorted(findings), len(artifacts)


def _report(findings: Iterable[Finding], artifact_count: int) -> dict[str, Any]:
    ordered = sorted(findings)
    return {
        "findings": [
            {
                "code": finding.code,
                "message": finding.message,
                "path": finding.path.as_posix(),
            }
            for finding in ordered
        ],
        "summary": {"artifacts": artifact_count, "findings": len(ordered)},
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--inventory", type=Path)
    source.add_argument("--paths", nargs="+", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--output", "--report", dest="output", type=Path)
    parser.add_argument("--first-party-only", action="store_true")
    args = parser.parse_args(argv)

    if args.inventory is not None:
        if args.catalog is None:
            parser.error("--catalog is required with --inventory")
        findings, artifact_count = _inventory_findings(
            args.inventory,
            args.catalog,
            first_party_only=args.first_party_only,
        )
    else:
        findings, artifact_count = _paths_findings(args.paths)

    rendered = yaml.safe_dump(
        _report(findings, artifact_count), allow_unicode=True, sort_keys=True
    )
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
