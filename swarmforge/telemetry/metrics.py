#!/usr/bin/env python3
"""Best-effort local JSONL telemetry for SwarmForge lifecycle events."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tomllib
import uuid


SCHEMA_VERSION = 1
MAX_LINE_BYTES = 4_096
MAX_STRING_CHARS = 256
EVENTS = {
    "gate",
    "handoff_submitted",
    "task_accepted",
    "task_completed",
    "batch_accepted",
    "batch_completed",
}
RESULTS = {
    "pass",
    "fail",
    "setup_failed",
    "config_failed",
    "timeout",
    "submitted",
    "accepted",
    "completed",
}
TEST_SELECTION_REASONS = {
    "ok",
    "disabled",
    "no_index",
    "cli_missing",
    "no_changes",
    "error",
    "timeout",
    "invalid_output",
    "no_tests",
}
ROUTES = {"coder", "done", "architect", "security-reviewer"}


def _bounded(value: str | None) -> str | None:
    return value[:MAX_STRING_CHARS] if value is not None else None


def _telemetry_dir(root: Path) -> Path | None:
    if os.environ.get("SWARMFORGE_TELEMETRY") == "0":
        return None
    try:
        config_path = root / "swarmforge" / "python-gates.toml"
        if config_path.is_file():
            with config_path.open("rb") as stream:
                config = tomllib.load(stream)
        else:
            config = {}
        telemetry = config.get("telemetry", {})
        if not isinstance(telemetry, dict):
            return None
        enabled = telemetry.get("enabled", True)
        directory = telemetry.get("dir", ".swarmforge/metrics")
        if (
            not isinstance(enabled, bool)
            or not isinstance(directory, str)
            or not directory
        ):
            return None
        if not enabled:
            return None
        target = root / directory
        # Confine writes beneath the project root: reject absolute `dir`,
        # `..` traversal, and symlink escapes visible at resolution time.
        if not target.resolve().is_relative_to(root.resolve()):
            return None
        return target
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return None


def _backend_instance(root: Path, role: str | None) -> str | None:
    if role is None:
        return None
    try:
        for line in (root / ".swarmforge" / "roles.tsv").read_text().splitlines():
            fields = line.split("\t")
            if len(fields) > 5 and fields[0] == role:
                return _bounded(fields[5])
    except OSError:
        pass
    return None


def _optional_integer(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("telemetry counts and durations must be integers")
    if value < 0:
        raise ValueError("telemetry counts and durations cannot be negative")
    return value


def record_event(
    root: Path,
    *,
    event: str,
    result: str,
    id: str | None = None,
    duration_ms: int | None = None,
    handoff_count: int | None = None,
    gate_failure_count: int | None = None,
    tool_output_bytes_exposed: int | None = None,
    gate_mode: str | None = None,
    test_selection: str | None = None,
    test_selection_reason: str | None = None,
    affected_test_count: int | None = None,
    route: str | None = None,
    risk_score: int | None = None,
) -> bool:
    """Append one event, returning false when telemetry is disabled or dropped."""
    if event not in EVENTS or result not in RESULTS:
        return False
    expected_results = {
        "gate": {"pass", "fail", "setup_failed", "config_failed", "timeout"},
        "handoff_submitted": {"submitted"},
        "task_accepted": {"accepted"},
        "task_completed": {"completed"},
        "batch_accepted": {"accepted"},
        "batch_completed": {"completed"},
    }
    if result not in expected_results[event]:
        return False
    if event == "gate":
        if gate_failure_count not in {0, 1}:
            return False
        if test_selection not in {None, "affected", "full"}:
            return False
        if test_selection_reason not in TEST_SELECTION_REASONS | {None}:
            return False
        if route not in ROUTES | {None}:
            return False
        if test_selection is None and (
            test_selection_reason is not None or affected_test_count is not None
        ):
            return False
    elif any(
        value is not None
        for value in (
            gate_failure_count,
            tool_output_bytes_exposed,
            gate_mode,
            test_selection,
            test_selection_reason,
            affected_test_count,
            route,
            risk_score,
        )
    ):
        return False
    metrics_dir = _telemetry_dir(root)
    if metrics_dir is None:
        return False
    try:
        role = _bounded(os.environ.get("SWARMFORGE_ROLE"))
        record: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event": event,
            "id": _bounded(id) or str(uuid.uuid4()),
            "role": role,
            "backend_instance": _backend_instance(root, role),
            "result": result,
            "duration_ms": _optional_integer(duration_ms),
            "handoff_count": _optional_integer(handoff_count),
            "gate_failure_count": _optional_integer(gate_failure_count),
            "tool_output_bytes_exposed": _optional_integer(tool_output_bytes_exposed),
            "tokens": {"input": None, "output": None, "cache_read": None},
        }
        if gate_mode is not None:
            record["gate_mode"] = _bounded(gate_mode)
        if event == "gate":
            record["test_selection"] = test_selection
            record["test_selection_reason"] = test_selection_reason
            record["affected_test_count"] = _optional_integer(affected_test_count)
            record["route"] = route
            record["risk_score"] = _optional_integer(risk_score)
        payload = (
            json.dumps(record, sort_keys=False, separators=(",", ":")) + "\n"
        ).encode()
        if len(payload) > MAX_LINE_BYTES:
            return False
        metrics_dir.mkdir(parents=True, exist_ok=True)
        path = metrics_dir / "events.jsonl"
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            return os.write(descriptor, payload) == len(payload)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
    except Exception:
        return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    record = subparsers.add_parser("record")
    record.add_argument("--root", type=Path, default=Path.cwd())
    record.add_argument("--event", required=True, choices=sorted(EVENTS))
    record.add_argument("--id")
    record.add_argument("--result", required=True, choices=sorted(RESULTS))
    record.add_argument("--duration-ms", type=int)
    record.add_argument("--handoff-count", type=int)
    record.add_argument("--gate-failure-count", type=int)
    record.add_argument("--tool-output-bytes-exposed", type=int)
    record.add_argument("--gate-mode")
    record.add_argument("--test-selection", choices=["affected", "full"])
    record.add_argument(
        "--test-selection-reason", choices=sorted(TEST_SELECTION_REASONS)
    )
    record.add_argument("--affected-test-count", type=int)
    record.add_argument("--route", choices=sorted(ROUTES))
    record.add_argument("--risk-score", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record_event(
            args.root,
            event=_bounded(args.event) or "",
            id=_bounded(args.id),
            result=_bounded(args.result) or "",
            duration_ms=args.duration_ms,
            handoff_count=args.handoff_count,
            gate_failure_count=args.gate_failure_count,
            tool_output_bytes_exposed=args.tool_output_bytes_exposed,
            gate_mode=_bounded(args.gate_mode),
            test_selection=_bounded(args.test_selection),
            test_selection_reason=_bounded(args.test_selection_reason),
            affected_test_count=args.affected_test_count,
            route=_bounded(args.route),
            risk_score=args.risk_score,
        )
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
