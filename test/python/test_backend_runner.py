import json
import subprocess
import sys
from pathlib import Path


RUNNER = Path(__file__).parents[2] / "swarmforge" / "scripts" / "backend_runner.py"


def resolve(tmp_path: Path, instance: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--root",
            str(tmp_path),
            "--instance",
            instance,
            "--role",
            "planner",
            "--display",
            "Planner",
            "--worktree",
            str(tmp_path),
            "--prompt",
            str(tmp_path / "prompt.md"),
            "--dry-run",
        ],
        text=True,
        capture_output=True,
    )


def test_resolves_distinct_claude_profiles_without_fallback(tmp_path: Path) -> None:
    swarmforge = tmp_path / "swarmforge"
    swarmforge.mkdir()
    (tmp_path / "prompt.md").write_text("Be concise.\n")
    (swarmforge / "backends.toml").write_text(
        """
[instances.claude-trabajo]
kind = "claude"
command = ["claude"]
profile = "trabajo"

[instances.claude-personal]
kind = "claude"
command = ["claude"]
profile = "personal"
""".strip()
    )

    trabajo = resolve(tmp_path, "claude-trabajo")
    personal = resolve(tmp_path, "claude-personal")

    assert trabajo.returncode == personal.returncode == 0
    assert json.loads(trabajo.stdout)["environment"]["CLAUDE_CONFIG_DIR"].endswith(
        "/.aisw/profiles/trabajo"
    )
    assert json.loads(personal.stdout)["environment"]["CLAUDE_CONFIG_DIR"].endswith(
        "/.aisw/profiles/personal"
    )


def test_unknown_instance_fails_instead_of_falling_back(tmp_path: Path) -> None:
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "prompt.md").write_text("Be concise.\n")

    result = resolve(tmp_path, "claude-missing")

    assert result.returncode == 2
    assert "Unknown backend instance" in result.stderr
