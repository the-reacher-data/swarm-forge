from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).parents[2]
GATE = REPO_ROOT / "swarmforge" / "gates" / "gate.py"
sys.path.insert(0, str(REPO_ROOT))


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


def setup_route_project(root: Path, *, command: str = "pass", risk: str = "") -> None:
    init_repo(root)
    run("git", "branch", "-M", "main", cwd=root)
    (root / ".gitignore").write_text(".swarmforge/\n")
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n"
    )
    (root / "swarmforge").mkdir()
    (root / "swarmforge/python-gates.toml").write_text(
        f"[commands]\npre_handoff = [[{json.dumps(sys.executable)}, '-c', "
        f"{json.dumps(command)}]]\n"
        f"pre_complete = [[{json.dumps(sys.executable)}, '-c', "
        f"{json.dumps(command)}]]\n"
        f"{risk}"
    )
    (root / "docs").mkdir()
    (root / "docs/routing.md").write_text("initial\n")
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-q", "-m", "initial", cwd=root)
    (root / "docs/routing.md").write_text("updated\n")


def route_artifacts(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    artifact_dir = root / ".swarmforge/artifacts/route"
    return (
        json.loads((artifact_dir / "manifest.json").read_text()),
        json.loads((artifact_dir / "route.json").read_text()),
    )


def test_non_python_project_is_a_quiet_noop(tmp_path: Path) -> None:
    result = gate(tmp_path, "--fast")

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_hardening_without_commands_is_a_quiet_opt_in_noop(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n"
    )

    result = gate(tmp_path, "--hardening")

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert not (tmp_path / ".swarmforge/artifacts/gates").exists()
    recorded = telemetry_events(tmp_path)
    assert len(recorded) == 1
    assert recorded[0]["gate_mode"] == "hardening"
    assert recorded[0]["result"] == "pass"


def test_hardening_runs_only_explicit_argv_commands(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n"
    )
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge/python-gates.toml").write_text(
        "[commands]\n"
        'hardening = [["hardening-check", "mutation"], '
        '["hardening-check", "dry"]]\n'
    )
    calls = tmp_path / "calls.log"
    fake_bin = tmp_path / "bin"
    write_executable(
        fake_bin / "hardening-check",
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$SWARMFORGE_TEST_CALLS"\n'
        "printf '%s\\n' \"$*\"\n",
    )
    env = os.environ | {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SWARMFORGE_TEST_CALLS": str(calls),
    }

    result = gate(tmp_path, "--hardening", env)

    assert result.returncode == 0
    assert result.stderr == ""
    assert calls.read_text().splitlines() == ["mutation", "dry"]
    reports = [
        Path(line.removeprefix("HARDENING_REPORT: "))
        for line in result.stdout.splitlines()
        if line.startswith("HARDENING_REPORT: ")
    ]
    assert len(reports) == 2
    assert [report.read_text().strip() for report in reports] == [
        "mutation",
        "dry",
    ]


def test_empty_hardening_commands_do_not_fall_back_to_default_gate(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n"
    )
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge/python-gates.toml").write_text(
        "[commands]\nhardening = []\n"
    )

    result = gate(tmp_path, "--hardening")

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert not (tmp_path / ".swarmforge/artifacts/gates").exists()


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


def test_default_commands_use_uv_for_locked_projects(tmp_path: Path) -> None:
    from swarmforge.gates.gate import default_commands

    (tmp_path / "uv.lock").write_text("")
    (tmp_path / "tests").mkdir()

    assert default_commands(tmp_path, "stop") == [
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "ruff", "format", "--check", "."],
        ["uv", "run", "pytest", "-q"],
    ]


def test_gate_loads_ignored_local_config_before_portable_config(tmp_path: Path) -> None:
    from swarmforge.gates.gate import load_config

    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge/python-gates.toml").write_text("[gate]\ntimeout_seconds=1")
    (tmp_path / ".swarmforge").mkdir()
    (tmp_path / ".swarmforge/python-gates.toml").write_text(
        "[gate]\ntimeout_seconds=99"
    )

    assert load_config(tmp_path)["gate"] == {"timeout_seconds": 99}


def test_stop_gate_reuses_pass_for_identical_repository_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    init_repo(project)
    (project / ".gitignore").write_text(".swarmforge/\n")
    (project / "pyproject.toml").write_text(
        "[project]\nname='sample'\nversion='0.1.0'\n"
    )
    (project / "swarmforge").mkdir()
    (project / "swarmforge/python-gates.toml").write_text(
        "[commands]\nstop=[['quality-check']]\n"
    )
    source = project / "src/app.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n")
    run("git", "add", ".", cwd=project)
    run("git", "commit", "-q", "-m", "initial", cwd=project)
    calls = tmp_path / "calls.log"
    fake_bin = tmp_path / "bin"
    write_executable(
        fake_bin / "quality-check",
        '#!/bin/sh\nprintf "run\\n" >> "$SWARMFORGE_TEST_CALLS"\n',
    )
    env = os.environ | {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SWARMFORGE_TEST_CALLS": str(calls),
    }

    assert gate(project, "--stop", env).returncode == 0
    assert gate(project, "--stop", env).returncode == 0
    source.write_text("VALUE = 2\n")
    assert gate(project, "--stop", env).returncode == 0

    assert calls.read_text().splitlines() == ["run", "run"]


def test_stop_gate_reuses_pass_across_linked_worktrees(tmp_path: Path) -> None:
    project = tmp_path / "project"
    worker = tmp_path / "worker"
    project.mkdir()
    init_repo(project)
    (project / ".gitignore").write_text(".swarmforge/\n")
    (project / "pyproject.toml").write_text(
        "[project]\nname='sample'\nversion='0.1.0'\n"
    )
    (project / "swarmforge").mkdir()
    (project / "swarmforge/python-gates.toml").write_text(
        "[commands]\nstop=[['quality-check']]\n"
    )
    run("git", "add", ".", cwd=project)
    run("git", "commit", "-q", "-m", "initial", cwd=project)
    worktree = run(
        "git", "worktree", "add", "-q", "-b", "worker", str(worker), cwd=project
    )
    assert worktree.returncode == 0
    calls = tmp_path / "calls.log"
    fake_bin = tmp_path / "bin"
    write_executable(
        fake_bin / "quality-check",
        '#!/bin/sh\nprintf "run\\n" >> "$SWARMFORGE_TEST_CALLS"\n',
    )
    env = os.environ | {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SWARMFORGE_TEST_CALLS": str(calls),
    }

    assert gate(project, "--stop", env).returncode == 0
    assert gate(worker, "--stop", env).returncode == 0

    assert calls.read_text().splitlines() == ["run"]


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


def test_pre_handoff_writes_deterministic_low_risk_route_artifacts(
    tmp_path: Path,
) -> None:
    setup_route_project(tmp_path)

    first = gate(tmp_path, "--pre-handoff")
    artifact_dir = tmp_path / ".swarmforge/artifacts/route"
    first_manifest = (artifact_dir / "manifest.json").read_bytes()
    first_route = (artifact_dir / "route.json").read_bytes()
    second = gate(tmp_path, "--pre-handoff")
    manifest, route = route_artifacts(tmp_path)

    assert first.returncode == second.returncode == 0
    assert first.stdout == first.stderr == second.stdout == second.stderr == ""
    assert (artifact_dir / "manifest.json").read_bytes() == first_manifest
    assert (artifact_dir / "route.json").read_bytes() == first_route
    assert manifest["gates"]["pre-handoff"] == "pass"
    assert route["route"] == "done"
    assert route["reviewers"] == []
    assert route["reasons"] == ["signal:tests-docs-only"]
    recorded = telemetry_events(tmp_path)
    assert recorded[-1]["route"] == "done"
    assert recorded[-1]["risk_score"] == 0
    assert "reasons" not in recorded[-1]


def test_pre_handoff_writes_valid_registered_reviewers(tmp_path: Path) -> None:
    setup_route_project(tmp_path)
    roles = tmp_path / "swarmforge/roles"
    roles.mkdir()
    (roles / "data-engineer.prompt").write_text("Review data changes.\n")
    (roles / "ui-reviewer.prompt").write_text("Review UI changes.\n")
    (tmp_path / "swarmforge/backends.toml").write_text(
        "[instances.authorized]\nkind = 'claude'\ncommand = ['claude']\n"
    )
    (tmp_path / "swarmforge/project-agents.toml").write_text(
        """
[agents.data-engineer]
role = "data-engineer"
backend_instance = "authorized"
mode = "lazy"
prompt = "swarmforge/roles/data-engineer.prompt"
[agents.data-engineer.routing]
paths = ["docs/**"]
priority = 20

[agents.ui-reviewer]
role = "ui-reviewer"
backend_instance = "authorized"
mode = "lazy"
prompt = "swarmforge/roles/ui-reviewer.prompt"
[agents.ui-reviewer.routing]
paths = ["docs/**"]
priority = 10
""".strip()
    )

    result = gate(tmp_path, "--pre-handoff")
    _, route = route_artifacts(tmp_path)

    assert result.returncode == 0
    assert route["reviewers"] == ["ui-reviewer", "data-engineer"]


def test_failed_security_gate_routes_back_to_coder(tmp_path: Path) -> None:
    setup_route_project(tmp_path, command="raise SystemExit(1)")
    sensitive = tmp_path / "src/auth/token.py"
    sensitive.parent.mkdir(parents=True)
    sensitive.write_text("TOKEN = 'private'\n")

    result = gate(tmp_path, "--pre-complete")
    manifest, route = route_artifacts(tmp_path)

    assert result.returncode == 2
    assert manifest["paths"] == ["docs/routing.md"]
    assert route["route"] == "coder"
    assert route["reasons"] == ["class:security", "gate:failed"]
    assert "token.py" not in json.dumps(manifest)
    assert "token.py" not in json.dumps(route)


def test_invalid_risk_config_uses_defaults_without_changing_gate_exit(
    tmp_path: Path,
) -> None:
    setup_route_project(tmp_path, risk="\n[risk]\nmax_paths = -1\n")
    auth = tmp_path / "src/auth/handler.py"
    auth.parent.mkdir(parents=True)
    auth.write_text("VALUE = 1\n")

    result = gate(tmp_path, "--pre-handoff")
    _, route = route_artifacts(tmp_path)

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert route["route"] == "security-reviewer"


def test_route_artifact_failure_is_silent_and_non_blocking(tmp_path: Path) -> None:
    setup_route_project(tmp_path)
    blocker = tmp_path / ".swarmforge/artifacts/route"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("not a directory")

    result = gate(tmp_path, "--pre-handoff")

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert blocker.is_file()


def test_fast_gate_does_not_write_route_artifacts(tmp_path: Path) -> None:
    setup_route_project(tmp_path)

    result = gate(tmp_path, "--fast")

    assert result.returncode == 0
    assert not (tmp_path / ".swarmforge/artifacts/route").exists()
