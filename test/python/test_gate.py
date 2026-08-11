from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).parents[2]
GATE = REPO_ROOT / "swarmforge" / "gates" / "gate.py"


def run(
    *args: str, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def init_repo(root: Path) -> None:
    run("git", "init", "-q", cwd=root)
    run("git", "config", "user.email", "test@example.com", cwd=root)
    run("git", "config", "user.name", "Test User", cwd=root)


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


def gate(
    root: Path, mode: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return run(sys.executable, str(GATE), mode, cwd=root, env=env)


def telemetry_events(root: Path) -> list[dict[str, object]]:
    metrics = root / ".swarmforge" / "metrics" / "events.jsonl"
    return [json.loads(line) for line in metrics.read_text().splitlines()]


def setup_affected_project(
    root: Path, *, create_index: bool = True, affected_config: str = ""
) -> tuple[dict[str, str], Path, Path]:
    init_repo(root)
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n\n[tool.pytest.ini_options]\n"
    )
    if affected_config:
        (root / "swarmforge").mkdir()
        (root / "swarmforge/python-gates.toml").write_text(affected_config)
    source = root / "src/app.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n")
    first_test = root / "test/test_app.py"
    first_test.parent.mkdir()
    first_test.write_text("def test_app():\n    assert True\n")
    second_test = root / "test/helpers_test.py"
    second_test.write_text("def test_helper():\n    assert True\n")
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-q", "-m", "initial", cwd=root)
    source.write_text("VALUE = 2\n")
    if create_index:
        (root / ".codegraph").mkdir()

    calls = root / "calls.log"
    stdin_log = root / "affected.stdin"
    fake_bin = root / "bin"
    write_executable(
        fake_bin / "ruff",
        '#!/bin/sh\nprintf \'ruff:%s\\n\' "$*" >> "$SWARMFORGE_TEST_CALLS"\n',
    )
    write_executable(
        fake_bin / "pytest",
        '#!/bin/sh\nprintf \'pytest:%s\\n\' "$*" >> "$SWARMFORGE_TEST_CALLS"\n',
    )
    write_executable(
        fake_bin / "codegraph",
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" > "$SWARMFORGE_TEST_CODEGRAPH_ARGS"\n'
        'cat > "$SWARMFORGE_TEST_CODEGRAPH_STDIN"\n'
        'sleep "${SWARMFORGE_TEST_CODEGRAPH_SLEEP:-0}"\n'
        "printf '%s' \"$SWARMFORGE_TEST_CODEGRAPH_OUTPUT\"\n"
        'exit "${SWARMFORGE_TEST_CODEGRAPH_EXIT:-0}"\n',
    )
    env = os.environ | {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SWARMFORGE_TEST_CALLS": str(calls),
        "SWARMFORGE_TEST_CODEGRAPH_ARGS": str(root / "affected.args"),
        "SWARMFORGE_TEST_CODEGRAPH_STDIN": str(stdin_log),
        "SWARMFORGE_TEST_CODEGRAPH_OUTPUT": json.dumps(
            ["test/test_app.py", "test/helpers_test.py"]
        ),
    }
    return env, calls, stdin_log


def test_non_python_project_is_a_quiet_noop(tmp_path: Path) -> None:
    result = gate(tmp_path, "--fast")

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_fast_gate_runs_ruff_only_for_changed_python_files(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n"
    )
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n")
    run("git", "add", ".", cwd=tmp_path)
    run("git", "commit", "-q", "-m", "initial", cwd=tmp_path)
    source.write_text("VALUE = 2\n")
    calls = tmp_path / "calls.log"
    fake_bin = tmp_path / "bin"
    write_executable(
        fake_bin / "ruff",
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$SWARMFORGE_TEST_CALLS"\n',
    )
    env = os.environ | {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SWARMFORGE_TEST_CALLS": str(calls),
    }

    result = gate(tmp_path, "--fast", env)

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert calls.read_text().splitlines() == [
        "check --fix src/app.py",
        "format src/app.py",
    ]
    recorded = telemetry_events(tmp_path)
    assert len(recorded) == 1
    assert recorded[0]["event"] == "gate"
    assert recorded[0]["result"] == "pass"
    assert recorded[0]["gate_mode"] == "fast"
    assert recorded[0]["gate_failure_count"] == 0
    assert recorded[0]["tool_output_bytes_exposed"] == 0


def test_failed_gate_bounds_feedback_and_keeps_full_log(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n"
    )
    fake_bin = tmp_path / "bin"
    write_executable(
        fake_bin / "ruff",
        "#!/bin/sh\nprintf 'ruff failure: ' >&2\ni=0\nwhile [ $i -lt 12000 ]; do printf 'x' >&2; i=$((i + 1)); done\nprintf '\\n' >&2\nexit 1\n",
    )
    env = os.environ | {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}

    result = gate(tmp_path, "--stop", env)

    assert result.returncode == 2
    assert len(result.stderr.encode()) <= 9_000
    assert "bytes omitted" in result.stderr
    log_line = next(
        line for line in result.stderr.splitlines() if line.startswith("full_log=")
    )
    assert Path(log_line.removeprefix("full_log=")).is_file()
    recorded = telemetry_events(tmp_path)
    assert len(recorded) == 1
    assert recorded[0]["result"] == "fail"
    assert recorded[0]["gate_failure_count"] == 1
    assert recorded[0]["tool_output_bytes_exposed"] == 8_192
    assert "ruff failure" not in json.dumps(recorded[0])


def test_gate_config_failure_is_recorded_without_changing_stderr(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n"
    )
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge/python-gates.toml").write_text(
        "[commands]\nfast = 'invalid'\n"
    )

    result = gate(tmp_path, "--fast")

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("GATE_CONFIG_FAILED:")
    recorded = telemetry_events(tmp_path)
    assert len(recorded) == 1
    assert recorded[0]["result"] == "config_failed"
    assert recorded[0]["gate_failure_count"] == 1


def test_gate_setup_failure_is_recorded(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n"
    )
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge/python-gates.toml").write_text(
        "[commands]\nfast = [['swarmforge-command-that-does-not-exist']]\n"
    )

    result = gate(tmp_path, "--fast")

    assert result.returncode == 2
    assert "GATE_SETUP_FAILED" in result.stderr
    recorded = telemetry_events(tmp_path)
    assert len(recorded) == 1
    assert recorded[0]["result"] == "setup_failed"
    assert recorded[0]["tool_output_bytes_exposed"] == 0


def test_gate_timeout_is_recorded(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n"
    )
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge/python-gates.toml").write_text(
        "[gate]\ntimeout_seconds = 1\n\n"
        f"[commands]\nstop = [[{json.dumps(sys.executable)}, '-c', "
        "'import time; time.sleep(2)']]\n"
    )

    result = gate(tmp_path, "--stop")

    assert result.returncode == 2
    assert "status=timeout:1s" in result.stderr
    recorded = telemetry_events(tmp_path)
    assert len(recorded) == 1
    assert recorded[0]["result"] == "timeout"
    assert recorded[0]["gate_failure_count"] == 1


def test_telemetry_failure_does_not_change_gate_result(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n"
    )
    (tmp_path / "blocked").write_text("not a directory")
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge/python-gates.toml").write_text(
        "[telemetry]\ndir = 'blocked/metrics'\n\n"
        f"[commands]\nfast = [[{json.dumps(sys.executable)}, '-c', 'pass']]\n"
    )

    result = gate(tmp_path, "--fast")

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""


def test_gate_runs_only_codegraph_affected_tests(tmp_path: Path) -> None:
    env, calls, stdin_log = setup_affected_project(tmp_path)

    result = gate(tmp_path, "--stop", env)

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert calls.read_text().splitlines()[-1] == (
        "pytest:-q test/test_app.py test/helpers_test.py"
    )
    assert (tmp_path / "affected.args").read_text().strip() == (
        f"affected --json --stdin -p {tmp_path}"
    )
    assert stdin_log.read_text().splitlines() == ["src/app.py"]
    recorded = telemetry_events(tmp_path)
    assert recorded[0]["test_selection"] == "affected"
    assert recorded[0]["test_selection_reason"] == "ok"
    assert recorded[0]["affected_test_count"] == 2
    assert "test_app.py" not in json.dumps(recorded[0])


@pytest.mark.parametrize(
    ("output", "exit_code", "reason"),
    [
        ("not-json", "0", "invalid_output"),
        (json.dumps({"test": "test/test_app.py"}), "0", "invalid_output"),
        (json.dumps([]), "0", "no_tests"),
        (json.dumps(["test/missing.py"]), "0", "invalid_output"),
        (json.dumps(["src/app.py"]), "0", "invalid_output"),
        (json.dumps(["test/test_app.py"]), "7", "error"),
    ],
)
def test_affected_selection_falls_back_on_unsafe_output(
    tmp_path: Path, output: str, exit_code: str, reason: str
) -> None:
    env, calls, _ = setup_affected_project(tmp_path)
    env |= {
        "SWARMFORGE_TEST_CODEGRAPH_OUTPUT": output,
        "SWARMFORGE_TEST_CODEGRAPH_EXIT": exit_code,
    }

    result = gate(tmp_path, "--stop", env)

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert calls.read_text().splitlines()[-1] == "pytest:-q"
    recorded = telemetry_events(tmp_path)
    assert recorded[0]["test_selection"] == "full"
    assert recorded[0]["test_selection_reason"] == reason
    assert recorded[0]["affected_test_count"] is None


def test_affected_selection_rejects_test_outside_root(tmp_path: Path) -> None:
    env, calls, _ = setup_affected_project(tmp_path)
    outside = Path(__file__).resolve()
    env["SWARMFORGE_TEST_CODEGRAPH_OUTPUT"] = json.dumps([str(outside)])

    result = gate(tmp_path, "--stop", env)

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert calls.read_text().splitlines()[-1] == "pytest:-q"
    assert telemetry_events(tmp_path)[0]["test_selection_reason"] == "invalid_output"


def test_affected_selection_falls_back_when_cli_is_missing(tmp_path: Path) -> None:
    env, calls, _ = setup_affected_project(tmp_path)
    (tmp_path / "bin/codegraph").rename(tmp_path / "bin/codegraph-disabled")
    env["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin:/bin"

    result = gate(tmp_path, "--stop", env)

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert calls.read_text().splitlines()[-1] == "pytest:-q"
    assert telemetry_events(tmp_path)[0]["test_selection_reason"] == "cli_missing"


def test_affected_selection_passes_configured_depth(tmp_path: Path) -> None:
    env, calls, _ = setup_affected_project(
        tmp_path, affected_config="[affected_tests]\ndepth = 3\n"
    )

    result = gate(tmp_path, "--stop", env)

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert calls.read_text().splitlines()[-1].startswith("pytest:-q test/test_app.py")
    assert (tmp_path / "affected.args").read_text().strip().endswith("--depth 3")


def test_affected_selection_falls_back_on_timeout(tmp_path: Path) -> None:
    env, calls, _ = setup_affected_project(
        tmp_path,
        affected_config="[affected_tests]\ntimeout_seconds = 1\n",
    )
    env["SWARMFORGE_TEST_CODEGRAPH_SLEEP"] = "2"

    result = gate(tmp_path, "--stop", env)

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert calls.read_text().splitlines()[-1] == "pytest:-q"
    assert telemetry_events(tmp_path)[0]["test_selection_reason"] == "timeout"


@pytest.mark.parametrize(
    ("create_index", "config", "reason"),
    [
        (False, "", "no_index"),
        (True, "[affected_tests]\nenabled = false\n", "disabled"),
        (True, "[affected_tests]\nenabled = 'invalid'\n", "error"),
    ],
)
def test_affected_selection_falls_back_when_unavailable_or_disabled(
    tmp_path: Path, create_index: bool, config: str, reason: str
) -> None:
    env, calls, _ = setup_affected_project(
        tmp_path, create_index=create_index, affected_config=config
    )

    result = gate(tmp_path, "--stop", env)

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert calls.read_text().splitlines()[-1] == "pytest:-q"
    assert telemetry_events(tmp_path)[0]["test_selection_reason"] == reason


def test_affected_selection_falls_back_without_python_changes(tmp_path: Path) -> None:
    env, calls, _ = setup_affected_project(tmp_path)
    (tmp_path / "src/app.py").write_text("VALUE = 1\n")

    result = gate(tmp_path, "--stop", env)

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert calls.read_text().splitlines()[-1] == "pytest:-q"
    assert telemetry_events(tmp_path)[0]["test_selection_reason"] == "no_changes"


def test_configured_commands_are_not_rewritten_by_affected_selection(
    tmp_path: Path,
) -> None:
    env, calls, _ = setup_affected_project(
        tmp_path,
        affected_config=(
            "[commands]\nstop = [['pytest', '-q', 'configured_test.py']]\n"
        ),
    )

    result = gate(tmp_path, "--stop", env)

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert calls.read_text().splitlines() == ["pytest:-q configured_test.py"]
    recorded = telemetry_events(tmp_path)[0]
    assert recorded["test_selection"] is None
    assert recorded["test_selection_reason"] is None
    assert recorded["affected_test_count"] is None
