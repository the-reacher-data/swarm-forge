#!/usr/bin/env python3
"""Build deterministic, privacy-bounded gate risk manifests."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 8_192
METRICS_TAIL_BYTES = 65_536
MAX_ADDED_LINES = 10_000
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
DEFAULT_SENSITIVE_PATTERNS = (
    "*.env*",
    "*secret*",
    "*credential*",
    "*key*",
    "*.pem",
    "*token*",
)
GATE_MODES = {"fast", "stop", "pre-handoff", "pre-complete"}
GATE_RESULTS = {"pass", "fail", "setup_failed", "config_failed", "timeout"}
CONCURRENCY_PATTERN = re.compile(
    r"async def|threading|multiprocessing|asyncio|Lock\(|Semaphore\("
)


@dataclass(frozen=True)
class Manifest:
    """Public document plus private, in-memory-only routing inputs."""

    document: dict[str, object]
    all_paths: tuple[str, ...]
    concurrency_signal: bool


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def _head(root: Path) -> str:
    result = _git(root, "rev-parse", "HEAD")
    if result.returncode != 0:
        raise ValueError("cannot resolve HEAD")
    return result.stdout.strip()


def _base(root: Path) -> str | None:
    result = _git(root, "merge-base", "HEAD", "main")
    return result.stdout.strip() if result.returncode == 0 else None


def _parse_numstat(output: str) -> list[tuple[str, int, int]]:
    parsed: list[tuple[str, int, int]] = []
    for line in output.splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3:
            continue
        added, deleted, path = fields
        parsed.append(
            (
                path,
                int(added) if added.isdigit() else 0,
                int(deleted) if deleted.isdigit() else 0,
            )
        )
    return parsed


def _untracked_lines(path: Path) -> int:
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if b"\0" in data or not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _changes(root: Path, base: str | None) -> dict[str, tuple[int, int]]:
    changes: dict[str, list[int]] = {}

    def add(path: str, insertions: int, deletions: int) -> None:
        counts = changes.setdefault(path, [0, 0])
        counts[0] += insertions
        counts[1] += deletions

    baseline = base or EMPTY_TREE
    committed = _git(
        root, "diff", "--numstat", "--no-renames", f"{baseline}..HEAD", "--"
    )
    if committed.returncode == 0:
        for path, insertions, deletions in _parse_numstat(committed.stdout):
            add(path, insertions, deletions)

    working = _git(root, "diff", "--numstat", "--no-renames", "HEAD", "--")
    if working.returncode == 0:
        for path, insertions, deletions in _parse_numstat(working.stdout):
            add(path, insertions, deletions)

    untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    if untracked.returncode == 0:
        for path in untracked.stdout.splitlines():
            add(path, _untracked_lines(root / path), 0)

    return {path: (counts[0], counts[1]) for path, counts in changes.items()}


def _added_lines(root: Path, baseline: str, untracked: Sequence[str]) -> list[str]:
    lines: list[str] = []
    result = _git(root, "diff", "--no-renames", "--unified=0", baseline, "--")
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                lines.append(line[1:])
                if len(lines) >= MAX_ADDED_LINES:
                    return lines
    for path in untracked:
        try:
            with (root / path).open(errors="replace") as stream:
                for line in stream:
                    lines.append(line.rstrip("\n"))
                    if len(lines) >= MAX_ADDED_LINES:
                        return lines
        except (OSError, UnicodeError):
            continue
    return lines


def _matches(path: str, patterns: Sequence[str]) -> bool:
    lowered = path.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in patterns)


def _latest_gates(root: Path) -> dict[str, str]:
    path = root / ".swarmforge" / "metrics" / "events.jsonl"
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            start = max(0, size - METRICS_TAIL_BYTES)
            stream.seek(start)
            if start:
                stream.readline()
            payload = stream.read(METRICS_TAIL_BYTES)
    except OSError:
        return {}
    latest: dict[str, str] = {}
    for raw_line in payload.splitlines():
        try:
            event = json.loads(raw_line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        mode = event.get("gate_mode")
        result = event.get("result")
        if event.get("event") == "gate" and mode in GATE_MODES and result in GATE_RESULTS:
            latest[mode] = result
    return dict(sorted(latest.items()))


def _valid_test_path(root: Path, path: str) -> bool:
    candidate = Path(path) if Path(path).is_absolute() else root / path
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    name = candidate.name
    return candidate.is_file() and (
        (name.startswith("test") and name.endswith(".py"))
        or name.endswith("_test.py")
    )


def codegraph_impact(
    root: Path,
    changed_python_files: Sequence[str],
    *,
    timeout_seconds: int = 30,
    depth: int | None = None,
) -> dict[str, int] | None:
    if (
        not changed_python_files
        or not (root / ".codegraph").exists()
        or (executable := shutil.which("codegraph")) is None
    ):
        return None
    command = [executable, "affected", "--json", "--stdin", "-p", str(root)]
    if depth is not None:
        command.extend(["--depth", str(depth)])
    try:
        result = subprocess.run(
            command,
            cwd=root,
            input="".join(f"{path}\n" for path in changed_python_files),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        paths = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    if (
        not isinstance(paths, list)
        or not paths
        or not all(isinstance(path, str) and _valid_test_path(root, path) for path in paths)
    ):
        return None
    return {"affected_test_count": len(paths)}


def _bounded_document(document: dict[str, object]) -> dict[str, object]:
    candidate = dict(document)
    candidate["paths"] = list(document["paths"])
    if len(_serialize_document(candidate)) <= MAX_MANIFEST_BYTES:
        return candidate
    candidate["truncated"] = True
    paths = candidate["paths"]
    assert isinstance(paths, list)
    while paths and len(_serialize_document(candidate)) > MAX_MANIFEST_BYTES:
        paths.pop()
    if len(_serialize_document(candidate)) > MAX_MANIFEST_BYTES:
        candidate["paths"] = []
    if len(_serialize_document(candidate)) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest cannot fit hard cap")
    return candidate


def _serialize_document(document: dict[str, object]) -> bytes:
    return json.dumps(document, sort_keys=False, separators=(",", ":")).encode()


def build_manifest(
    root: Path,
    *,
    gate_mode: str,
    gate_result: str,
    max_paths: int = 50,
    sensitive_patterns: Sequence[str] = DEFAULT_SENSITIVE_PATTERNS,
    affected_enabled: bool = True,
    affected_timeout_seconds: int = 30,
    affected_depth: int | None = None,
) -> Manifest:
    root = root.resolve()
    commit = _head(root)
    base = _base(root)
    changes = _changes(root, base)
    all_paths = tuple(sorted(changes))
    public_paths = [
        path for path in all_paths if not _matches(path, sensitive_patterns)
    ][:max_paths]
    gates = _latest_gates(root)
    gates[gate_mode] = gate_result
    gates = dict(sorted(gates.items()))
    insertions = sum(counts[0] for counts in changes.values())
    deletions = sum(counts[1] for counts in changes.values())
    changed_python = [
        path for path in all_paths if path.endswith(".py") and (root / path).is_file()
    ]
    impact = (
        codegraph_impact(
            root,
            changed_python,
            timeout_seconds=affected_timeout_seconds,
            depth=affected_depth,
        )
        if affected_enabled
        else None
    )
    document: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "commit": commit,
        "base": base,
        "diff": {
            "files_changed": len(all_paths),
            "insertions": insertions,
            "deletions": deletions,
        },
        "paths": public_paths,
        "gates": gates,
        "codegraph_impact": impact,
        "truncated": False,
    }
    untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    untracked_paths = untracked.stdout.splitlines() if untracked.returncode == 0 else []
    added_lines = _added_lines(root, base or EMPTY_TREE, untracked_paths)
    return Manifest(
        document=_bounded_document(document),
        all_paths=all_paths,
        concurrency_signal=any(CONCURRENCY_PATTERN.search(line) for line in added_lines),
    )


def serialize_manifest(manifest: Manifest) -> bytes:
    return _serialize_document(manifest.document)


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--root", type=Path, default=Path.cwd())
    build.add_argument("--gate-mode", required=True, choices=sorted(GATE_MODES))
    build.add_argument("--gate-result", required=True, choices=sorted(GATE_RESULTS))
    build.add_argument("--max-paths", type=int, default=50)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    built = build_manifest(
        args.root,
        gate_mode=args.gate_mode,
        gate_result=args.gate_result,
        max_paths=args.max_paths,
    )
    print(serialize_manifest(built).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
