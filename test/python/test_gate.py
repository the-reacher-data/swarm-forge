from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


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
    assert calls.read_text().splitlines() == [
        "check --fix src/app.py",
        "format src/app.py",
    ]


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
