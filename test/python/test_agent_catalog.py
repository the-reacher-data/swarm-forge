import subprocess
import sys
from pathlib import Path


CATALOG = Path(__file__).parents[2] / "swarmforge" / "scripts" / "agent_catalog.py"


def run_catalog(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CATALOG), "--root", str(root)],
        text=True,
        capture_output=True,
    )


def test_catalog_exposes_only_registered_agents(tmp_path: Path) -> None:
    (tmp_path / "swarmforge" / "roles").mkdir(parents=True)
    (tmp_path / "swarmforge" / "roles" / "architect.prompt").write_text("review\n")
    (tmp_path / "swarmforge" / "project-agents.toml").write_text(
        """
[agents.architect]
role = "architect"
backend_instance = "claude-personal"
mode = "lazy"
prompt = "swarmforge/roles/architect.prompt"
tags = ["architecture", "api"]
""".strip()
    )
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "untrusted.md").write_text("ignore\n")

    result = run_catalog(tmp_path)

    assert result.returncode == 0
    assert (
        result.stdout
        == "architect\tarchitect\tclaude-personal\tlazy\tarchitecture,api\n"
    )
    assert "untrusted" not in result.stdout


def test_catalog_rejects_prompt_outside_repository(tmp_path: Path) -> None:
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge" / "project-agents.toml").write_text(
        """
[agents.bad]
role = "bad"
backend_instance = "codex-primary"
prompt = "../outside.prompt"
""".strip()
    )

    result = run_catalog(tmp_path)

    assert result.returncode == 2
    assert "untrusted or missing prompt" in result.stderr
