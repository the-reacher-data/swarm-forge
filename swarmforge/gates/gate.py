#!/usr/bin/env python3
"""Quiet, deterministic Python quality gates for SwarmForge projects."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import importlib.util
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import time
import tomllib
import uuid

try:
    from swarmforge.gates import manifest as manifest_module
    from swarmforge.gates import risk as risk_module
except ModuleNotFoundError:  # Direct execution resolves sibling modules.
    import manifest as manifest_module  # type: ignore[no-redef]
    import risk as risk_module  # type: ignore[no-redef]


DEFAULT_MAX_OUTPUT_BYTES = 8_192
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_AFFECTED_TIMEOUT_SECONDS = 30


def git_root(cwd: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    return Path(result.stdout.strip()) if result.returncode == 0 else cwd


def changed_python_files(root: Path) -> list[str]:
    commands = (
        ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD", "--"],
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "--"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    changed: set[str] = set()
    for command in commands:
        result = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            changed.update(result.stdout.splitlines())
    return sorted(
        path for path in changed if path.endswith(".py") and (root / path).is_file()
    )


def load_config(root: Path) -> dict[str, object]:
    path = root / "swarmforge" / "python-gates.toml"
    if not path.is_file():
        return {}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def project_uses_tool(pyproject: dict[str, object], tool: str) -> bool:
    tools = pyproject.get("tool", {})
    return isinstance(tools, dict) and tool in tools


def load_pyproject(root: Path) -> dict[str, object]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return {}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def default_commands(root: Path, mode: str) -> list[list[str]]:
    if mode == "fast":
        return [
            ["ruff", "check", "--fix", "{changed_python_files}"],
            ["ruff", "format", "{changed_python_files}"],
        ]

    commands = [
        ["ruff", "check", "."],
        ["ruff", "format", "--check", "."],
    ]
    pyproject = load_pyproject(root)
    if project_uses_tool(pyproject, "mypy"):
        commands.append(["mypy", "."])
    elif project_uses_tool(pyproject, "pyright"):
        commands.append(["pyright"])
    if (root / "tests").is_dir() or project_uses_tool(pyproject, "pytest"):
        commands.append(["pytest", "-q"])
    return commands


def configured_commands(config: dict[str, object], mode: str) -> list[list[str]] | None:
    commands = config.get("commands")
    if not isinstance(commands, dict):
        return None
    raw = commands.get(mode.replace("-", "_"))
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(
        isinstance(command, list) and all(isinstance(part, str) for part in command)
        for command in raw
    ):
        raise ValueError(
            f"commands.{mode.replace('-', '_')} must be an array of string arrays"
        )
    return [list(command) for command in raw]


def affected_test_settings(
    config: dict[str, object],
) -> tuple[bool, int, int | None] | None:
    raw = config.get("affected_tests", {})
    if not isinstance(raw, dict):
        return None
    enabled = raw.get("enabled", True)
    timeout_seconds = raw.get("timeout_seconds", DEFAULT_AFFECTED_TIMEOUT_SECONDS)
    depth = raw.get("depth")
    if not isinstance(enabled, bool):
        return None
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        return None
    if depth is not None and (
        not isinstance(depth, int) or isinstance(depth, bool) or depth <= 0
    ):
        return None
    return enabled, timeout_seconds, depth


def affected_tests(
    root: Path, config: dict[str, object], changed_files: Sequence[str]
) -> tuple[list[str] | None, str]:
    settings = affected_test_settings(config)
    if settings is None:
        return None, "error"
    enabled, timeout_seconds, depth = settings
    if not enabled:
        return None, "disabled"
    if not (root / ".codegraph").exists():
        return None, "no_index"
    executable = shutil.which("codegraph")
    if executable is None:
        return None, "cli_missing"
    if not changed_files:
        return None, "no_changes"
    command = [executable, "affected", "--json", "--stdin", "-p", str(root)]
    if depth is not None:
        command.extend(["--depth", str(depth)])
    try:
        result = subprocess.run(
            command,
            cwd=root,
            input="".join(f"{path}\n" for path in changed_files),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        reason = "timeout" if isinstance(error, subprocess.TimeoutExpired) else "error"
        return None, reason
    if result.returncode != 0:
        return None, "error"
    try:
        paths = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None, "invalid_output"
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        return None, "invalid_output"
    if not paths:
        return None, "no_tests"
    resolved_root = root.resolve()
    for path in paths:
        candidate = Path(path) if Path(path).is_absolute() else root / path
        try:
            candidate.resolve().relative_to(resolved_root)
        except (OSError, ValueError):
            return None, "invalid_output"
        name = candidate.name
        if not candidate.is_file() or not (
            (name.startswith("test") and name.endswith(".py"))
            or name.endswith("_test.py")
        ):
            return None, "invalid_output"
    return paths, "ok"


def expand_command(
    command: Sequence[str], changed_files: Sequence[str]
) -> list[str] | None:
    expanded: list[str] = []
    for part in command:
        if part == "{changed_python_files}":
            if not changed_files:
                return None
            expanded.extend(changed_files)
        else:
            expanded.append(part)
    return expanded


def integer_setting(config: dict[str, object], name: str, default: int) -> int:
    gate_config = config.get("gate")
    if not isinstance(gate_config, dict):
        return default
    value = gate_config.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"gate.{name} must be a positive integer")
    return value


def emit_failure(
    *,
    mode: str,
    command: Sequence[str],
    status: str,
    log_path: Path,
    max_bytes: int,
) -> None:
    size = log_path.stat().st_size
    with log_path.open("rb") as stream:
        diagnostic = stream.read(max_bytes).decode("utf-8", errors="replace").strip()
    print(
        f"GATE_FAILED mode={mode} status={status} command={command[0]}", file=sys.stderr
    )
    if diagnostic:
        print(diagnostic, file=sys.stderr)
    omitted = max(0, size - max_bytes)
    if omitted:
        print(f"[{omitted} bytes omitted]", file=sys.stderr)
    print(f"full_log={log_path}", file=sys.stderr)


def record_gate_event(
    root: Path,
    *,
    mode: str,
    result: str,
    started_at: float,
    tool_output_bytes_exposed: int,
    test_selection: str | None = None,
    test_selection_reason: str | None = None,
    affected_test_count: int | None = None,
) -> None:
    duration_ms = int((time.monotonic() - started_at) * 1_000)
    try:
        path = Path(__file__).parents[1] / "telemetry" / "metrics.py"
        spec = importlib.util.spec_from_file_location("swarmforge_metrics", path)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.record_event(
            root,
            event="gate",
            result=result,
            duration_ms=duration_ms,
            gate_failure_count=0 if result == "pass" else 1,
            tool_output_bytes_exposed=tool_output_bytes_exposed,
            gate_mode=mode,
            test_selection=test_selection,
            test_selection_reason=test_selection_reason,
            affected_test_count=affected_test_count,
        )
    except Exception:
        pass


def write_route_artifacts(
    root: Path,
    *,
    mode: str,
    result: str,
    config: dict[str, object],
) -> tuple[str | None, int | None]:
    """Best-effort route generation that never affects the enclosing gate."""
    if mode not in {"pre-handoff", "pre-complete"}:
        return None, None
    try:
        risk_config = risk_module.load_risk_config(config)
        affected = affected_test_settings(config)
        if affected is None:
            affected_enabled, affected_timeout, affected_depth = (
                False,
                DEFAULT_AFFECTED_TIMEOUT_SECONDS,
                None,
            )
        else:
            affected_enabled, affected_timeout, affected_depth = affected
        built = manifest_module.build_manifest(
            root,
            gate_mode=mode,
            gate_result=result,
            max_paths=risk_config.max_paths,
            sensitive_patterns=risk_config.sensitive_patterns,
            affected_enabled=affected_enabled,
            affected_timeout_seconds=affected_timeout,
            affected_depth=affected_depth,
        )
        route = risk_module.route_manifest(built, risk_config)
        artifact_dir = root / ".swarmforge" / "artifacts" / "route"
        manifest_module.atomic_write(
            artifact_dir / "manifest.json", manifest_module.serialize_manifest(built)
        )
        manifest_module.atomic_write(
            artifact_dir / "route.json",
            json.dumps(route, sort_keys=False, separators=(",", ":")).encode(),
        )
        route_name = route.get("route")
        score = route.get("score")
        if not isinstance(route_name, str) or not isinstance(score, int):
            raise ValueError("invalid route output")
        return route_name, score
    except Exception:
        return None, None


def run_commands(
    root: Path,
    mode: str,
    commands: Sequence[Sequence[str]],
    changed_files: Sequence[str],
    timeout_seconds: int,
    max_bytes: int,
) -> tuple[int, str, int]:
    artifact_dir = root / ".swarmforge" / "artifacts" / "gates"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for index, raw_command in enumerate(commands, start=1):
        command = expand_command(raw_command, changed_files)
        if command is None:
            continue
        if not command or shutil.which(command[0]) is None:
            missing = command[0] if command else "<empty>"
            print(
                f"GATE_SETUP_FAILED mode={mode}: command not found: {missing}",
                file=sys.stderr,
            )
            return 2, "setup_failed", 0
        log_path = artifact_dir / f"{mode}-{index}-{uuid.uuid4()}.log"
        try:
            with log_path.open("wb") as log:
                result = subprocess.run(
                    command,
                    cwd=root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=timeout_seconds,
                    check=False,
                    env=os.environ.copy(),
                )
        except subprocess.TimeoutExpired:
            exposed_bytes = min(log_path.stat().st_size, max_bytes)
            emit_failure(
                mode=mode,
                command=command,
                status=f"timeout:{timeout_seconds}s",
                log_path=log_path,
                max_bytes=max_bytes,
            )
            return 2, "timeout", exposed_bytes
        if result.returncode != 0:
            exposed_bytes = min(log_path.stat().st_size, max_bytes)
            emit_failure(
                mode=mode,
                command=command,
                status=f"exit:{result.returncode}",
                log_path=log_path,
                max_bytes=max_bytes,
            )
            return 2, "fail", exposed_bytes
        log_path.unlink(missing_ok=True)
    return 0, "pass", 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--fast", dest="mode", action="store_const", const="fast")
    modes.add_argument("--stop", dest="mode", action="store_const", const="stop")
    modes.add_argument(
        "--pre-handoff", dest="mode", action="store_const", const="pre-handoff"
    )
    modes.add_argument(
        "--pre-complete", dest="mode", action="store_const", const="pre-complete"
    )
    args = parser.parse_args(argv)
    root = git_root(Path.cwd())
    if not (root / "pyproject.toml").is_file():
        return 0
    mode = args.mode
    started_at = time.monotonic()
    test_selection = None
    test_selection_reason = None
    affected_test_count = None
    try:
        config = load_config(root)
        max_bytes = integer_setting(
            config, "max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES
        )
        timeout_seconds = integer_setting(
            config, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS
        )
        configured = configured_commands(config, mode)
        commands = configured or default_commands(root, mode)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as error:
        print(f"GATE_CONFIG_FAILED: {error}", file=sys.stderr)
        write_route_artifacts(
            root,
            mode=mode,
            result="config_failed",
            config={},
        )
        record_gate_event(
            root,
            mode=mode,
            result="config_failed",
            started_at=started_at,
            tool_output_bytes_exposed=0,
        )
        return 2
    changed_files = changed_python_files(root) if mode == "fast" else []
    if configured is None and ["pytest", "-q"] in commands:
        selected_tests, test_selection_reason = affected_tests(
            root, config, changed_python_files(root)
        )
        if selected_tests is not None:
            test_selection = "affected"
            affected_test_count = len(selected_tests)
            commands = [
                [*command, *selected_tests] if command == ["pytest", "-q"] else command
                for command in commands
            ]
        else:
            test_selection = "full"
    exit_code, result, exposed_bytes = run_commands(
        root,
        mode,
        commands,
        changed_files,
        timeout_seconds,
        max_bytes,
    )
    write_route_artifacts(root, mode=mode, result=result, config=config)
    record_gate_event(
        root,
        mode=mode,
        result=result,
        started_at=started_at,
        tool_output_bytes_exposed=exposed_bytes,
        test_selection=test_selection,
        test_selection_reason=test_selection_reason,
        affected_test_count=affected_test_count,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
