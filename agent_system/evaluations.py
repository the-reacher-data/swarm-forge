"""Load and evaluate shared behavioral contracts for native agent definitions."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from typing import Any

import yaml

from agent_system.validation import Finding


CORE_AGENT_NAMES = (
    "lead-engineer",
    "proposal-critic",
    "implementer",
    "verifier",
    "architecture-reviewer",
    "security-reviewer",
    "simplicity-reviewer",
)

_CASE_ROOT_FIELDS = frozenset({"version", "cases"})
_CASE_FIELDS = frozenset({"id", "target", "kind", "prompt", "expected", "forbidden"})
_CASE_KINDS = frozenset(
    {
        "trigger",
        "contract",
        "baseline",
        "discovery",
        "application",
        "variation",
        "negative",
    }
)
_EXPECTED_VALUES = frozenset(
    {
        "invoke",
        "do-not-invoke",
        "present",
        "absent",
        "observe-gap",
        "discover",
        "apply",
        "adapt",
    }
)
_MAPPING_ROOT_FIELDS = frozenset({"version", "mappings"})
_MAPPING_FIELDS = frozenset(
    {
        "role",
        "claude",
        "codex",
        "read_only",
        "responsibilities",
        "required_output_fields",
    }
)
_WRITE_TOOLS = frozenset({"edit", "write", "notebookedit", "*"})


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One runtime-neutral evaluation prompt and its expected routing result."""

    id: str
    target: str
    kind: str
    prompt: str
    expected: str
    forbidden: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _NativeMapping:
    role: str
    claude: Path
    codex: Path
    read_only: bool
    responsibilities: tuple[str, ...]
    required_output_fields: tuple[str, ...]


def _load_yaml_mapping(path: Path) -> Mapping[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ValueError(f"cannot load {path}: {error}") from error
    if not isinstance(document, Mapping):
        raise ValueError(f"{path}: document must be a mapping")
    return document


def _reject_unknown_fields(
    value: Mapping[str, Any], allowed: frozenset[str], context: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{context}: unknown fields: {', '.join(unknown)}")


def _required_text(value: Mapping[str, Any], field: str, context: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{context}: {field} must be a non-empty string")
    return item.strip()


def _string_list(
    value: Mapping[str, Any], field: str, context: str, *, allow_empty: bool
) -> tuple[str, ...]:
    items = value.get(field)
    if not isinstance(items, list) or not all(
        isinstance(item, str) and item.strip() for item in items
    ):
        raise ValueError(f"{context}: {field} must be a list of non-empty strings")
    normalized = tuple(item.strip() for item in items)
    if not allow_empty and not normalized:
        raise ValueError(f"{context}: {field} must not be empty")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{context}: {field} must not contain duplicates")
    return normalized


def load_cases(path: Path) -> list[EvaluationCase]:
    """Load versioned evaluation cases, rejecting incomplete or extended schemas."""
    path = Path(path)
    document = _load_yaml_mapping(path)
    _reject_unknown_fields(document, _CASE_ROOT_FIELDS, str(path))
    if document.get("version") != 1:
        raise ValueError(f"{path}: version must be 1")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError(f"{path}: cases must be a list")

    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        context = f"{path}: case {index}"
        if not isinstance(raw_case, Mapping):
            raise ValueError(f"{context} must be a mapping")
        _reject_unknown_fields(raw_case, _CASE_FIELDS, context)
        missing = sorted(_CASE_FIELDS - set(raw_case))
        if missing:
            raise ValueError(f"{context}: missing fields: {', '.join(missing)}")
        case_id = _required_text(raw_case, "id", context)
        if case_id in seen_ids:
            raise ValueError(f"{path}: duplicate case id: {case_id}")
        seen_ids.add(case_id)
        kind = _required_text(raw_case, "kind", context)
        expected = _required_text(raw_case, "expected", context)
        if kind not in _CASE_KINDS:
            raise ValueError(f"{context}: unsupported kind: {kind}")
        if expected not in _EXPECTED_VALUES:
            raise ValueError(f"{context}: unsupported expected value: {expected}")
        cases.append(
            EvaluationCase(
                id=case_id,
                target=_required_text(raw_case, "target", context),
                kind=kind,
                prompt=_required_text(raw_case, "prompt", context),
                expected=expected,
                forbidden=_string_list(
                    raw_case, "forbidden", context, allow_empty=True
                ),
            )
        )
    return cases


def _markdown_metadata(text: str) -> Mapping[str, Any]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}
    try:
        metadata = yaml.safe_load(normalized[4:end])
    except yaml.YAMLError:
        return {}
    return metadata if isinstance(metadata, Mapping) else {}


def _artifact_name(path: Path, text: str) -> str | None:
    if path.suffix == ".toml":
        try:
            document = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return None
        name = document.get("name")
    elif path.name == "SKILL.md" or path.suffix == ".md":
        name = _markdown_metadata(text).get("name")
    else:
        return None
    return name.strip() if isinstance(name, str) and name.strip() else None


def evaluate_static_contract(case: EvaluationCase, artifact: Path) -> list[Finding]:
    """Check deterministic identity and prohibited ownership claims for one case."""
    artifact = Path(artifact)
    try:
        text = artifact.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return [Finding("unreadable-evaluation-artifact", artifact, str(error))]

    findings: list[Finding] = []
    name = _artifact_name(artifact, text)
    if name != case.target:
        findings.append(
            Finding(
                "evaluation-target-mismatch",
                artifact,
                f"case {case.id!r} targets {case.target!r}, artifact declares {name!r}",
            )
        )
    folded_text = text.casefold()
    for forbidden in case.forbidden:
        if forbidden.casefold() in folded_text:
            findings.append(
                Finding(
                    "forbidden-contract-text",
                    artifact,
                    f"case {case.id!r} forbids contract text {forbidden!r}",
                )
            )
    return sorted(findings)


def _load_mappings(path: Path) -> list[_NativeMapping]:
    document = _load_yaml_mapping(path)
    _reject_unknown_fields(document, _MAPPING_ROOT_FIELDS, str(path))
    if document.get("version") != 1:
        raise ValueError(f"{path}: version must be 1")
    raw_mappings = document.get("mappings")
    if not isinstance(raw_mappings, list):
        raise ValueError(f"{path}: mappings must be a list")

    mappings: list[_NativeMapping] = []
    seen_roles: set[str] = set()
    for index, raw_mapping in enumerate(raw_mappings):
        context = f"{path}: mapping {index}"
        if not isinstance(raw_mapping, Mapping):
            raise ValueError(f"{context} must be a mapping")
        _reject_unknown_fields(raw_mapping, _MAPPING_FIELDS, context)
        missing = sorted(_MAPPING_FIELDS - set(raw_mapping))
        if missing:
            raise ValueError(f"{context}: missing fields: {', '.join(missing)}")
        role = _required_text(raw_mapping, "role", context)
        if role in seen_roles:
            raise ValueError(f"{path}: duplicate role mapping: {role}")
        seen_roles.add(role)
        read_only = raw_mapping.get("read_only")
        if not isinstance(read_only, bool):
            raise ValueError(f"{context}: read_only must be a boolean")
        claude = Path(_required_text(raw_mapping, "claude", context))
        codex = Path(_required_text(raw_mapping, "codex", context))
        if (
            claude.is_absolute()
            or codex.is_absolute()
            or ".." in (*claude.parts, *codex.parts)
        ):
            raise ValueError(f"{context}: native paths must be relative and contained")
        mappings.append(
            _NativeMapping(
                role=role,
                claude=claude,
                codex=codex,
                read_only=read_only,
                responsibilities=_string_list(
                    raw_mapping, "responsibilities", context, allow_empty=False
                ),
                required_output_fields=_string_list(
                    raw_mapping, "required_output_fields", context, allow_empty=False
                ),
            )
        )
    return mappings


def _normalized_contract_text(text: str) -> str:
    return re.sub(r"[-_\s]+", " ", text.casefold())


def _contains_contract_term(text: str, term: str) -> bool:
    return _normalized_contract_text(term) in _normalized_contract_text(text)


def _claude_tools(metadata: Mapping[str, Any]) -> set[str]:
    raw_tools = metadata.get("tools", [])
    if isinstance(raw_tools, str):
        raw_tools = raw_tools.strip("[]").split(",")
    if not isinstance(raw_tools, list):
        return set()
    return {item.strip().casefold() for item in raw_tools if isinstance(item, str)}


def compare_native_variants(
    claude_dir: Path, codex_dir: Path, mapping: Path
) -> list[Finding]:
    """Compare native definitions against shared role and output invariants."""
    findings: list[Finding] = []
    for item in _load_mappings(Path(mapping)):
        claude_path = Path(claude_dir) / item.claude
        codex_path = Path(codex_dir) / item.codex
        native: list[tuple[str, Path, str, Mapping[str, Any]]] = []
        for runtime, path in (("claude", claude_path), ("codex", codex_path)):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                findings.append(Finding(f"missing-{runtime}-variant", path, str(error)))
                continue
            if runtime == "claude":
                metadata = _markdown_metadata(text)
            else:
                try:
                    parsed = tomllib.loads(text)
                except tomllib.TOMLDecodeError as error:
                    findings.append(Finding("invalid-codex-variant", path, str(error)))
                    continue
                metadata = parsed
            name = metadata.get("name")
            if name != item.role:
                findings.append(
                    Finding(
                        f"{runtime}-role-name-mismatch",
                        path,
                        f"expected role {item.role!r}, found {name!r}",
                    )
                )
            native.append((runtime, path, text, metadata))

        for runtime, path, text, metadata in native:
            for responsibility in item.responsibilities:
                if not _contains_contract_term(text, responsibility):
                    findings.append(
                        Finding(
                            f"missing-{runtime}-responsibility",
                            path,
                            f"missing shared responsibility: {responsibility}",
                        )
                    )
            for field in item.required_output_fields:
                if not _contains_contract_term(text, field):
                    findings.append(
                        Finding(
                            f"missing-{runtime}-output-field",
                            path,
                            f"missing required output field: {field}",
                        )
                    )
            if item.read_only and runtime == "claude":
                if _claude_tools(metadata) & _WRITE_TOOLS:
                    findings.append(
                        Finding(
                            "claude-agent-has-write-tool",
                            path,
                            "read-only Claude role declares a write-capable tool",
                        )
                    )
            if item.read_only and runtime == "codex":
                if metadata.get("sandbox_mode") != "read-only":
                    findings.append(
                        Finding(
                            "codex-agent-not-read-only",
                            path,
                            "read-only Codex role must set sandbox_mode = 'read-only'",
                        )
                    )
    return sorted(findings)


def _artifact_for_case(directory: Path, target: str) -> Path | None:
    candidates = (
        directory / f"{target}.md",
        directory / f"{target}.toml",
        directory / target / "SKILL.md",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--static", action="store_true", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    findings: list[Finding] = []
    for case in load_cases(args.cases):
        artifact = _artifact_for_case(args.artifacts, case.target)
        if artifact is None:
            findings.append(
                Finding(
                    "missing-evaluation-target",
                    args.artifacts,
                    f"no native artifact found for {case.target!r}",
                )
            )
            continue
        findings.extend(evaluate_static_contract(case, artifact))
    for finding in sorted(set(findings)):
        print(f"{finding.path}: {finding.code}: {finding.message}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
