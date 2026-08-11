from __future__ import annotations

import json
from pathlib import Path

import pytest

from swarmforge.scripts.toolchain import (
    _print_doctor,
    bootstrap_commands,
    inspect_machine,
    install_cli,
    integrate_project,
    missing_group_packages,
    project_groups,
)


def test_inspect_machine_includes_backends_and_profiles(tmp_path: Path) -> None:
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge/backends.toml").write_text(
        """
[instances.codex-primary]
kind = "codex"
command = ["codex"]

[instances.claude-work]
kind = "claude"
command = ["claude"]
profile = "work"
""".strip()
    )
    home = tmp_path / "home"
    (home / ".aisw/profiles/work").mkdir(parents=True)
    available = {
        name: f"/tools/{name}"
        for name in (
            "bb",
            "claude",
            "codegraph",
            "codex",
            "curl",
            "git",
            "python3",
            "tar",
            "tmux",
            "uv",
        )
    }

    checks = inspect_machine(tmp_path, which=available.get, home=home)

    assert all(check.ok for check in checks)
    assert [check.name for check in checks].count("codex") == 1
    assert any(check.name == "claude-profile:work" for check in checks)


def test_inspect_machine_reports_missing_tool_without_installing(
    tmp_path: Path,
) -> None:
    checks = inspect_machine(tmp_path, which=lambda name: None, home=tmp_path)

    assert checks
    assert all(not check.ok for check in checks)
    assert {check.name for check in checks} >= {"uv", "codegraph", "tmux"}


def test_doctor_reads_backends_from_framework_not_target(
    tmp_path: Path, capsys
) -> None:
    framework = tmp_path / "framework"
    target = tmp_path / "target"
    (framework / "swarmforge").mkdir(parents=True)
    target.mkdir()
    (framework / "swarmforge/backends.toml").write_text(
        """
[instances.custom]
kind = "codex"
command = ["swarmforge-test-custom-codex"]
""".strip()
    )

    assert _print_doctor(target, config_root=framework) == 2
    assert (
        "MISSING\tmachine\tswarmforge-test-custom-codex\tnot-found"
        in capsys.readouterr().out
    )


def test_project_groups_selects_only_declared_managed_groups(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "sample"
version = "0.1.0"

[dependency-groups]
dev = ["pytest", "ruff"]
hardening = ["mutmut", "pytest-crap", "xenon", "pylint"]
docs = ["mkdocs"]
""".strip()
    )

    assert project_groups(tmp_path) == ("dev", "hardening")


def test_optional_dev_extra_is_preserved_as_a_managed_group(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "sample"
version = "0.1.0"

[project.optional-dependencies]
dev = ["pytest", "ruff"]

[dependency-groups]
hardening = ["mutmut", "pytest-crap", "xenon", "pylint"]
""".strip()
    )

    assert project_groups(tmp_path) == ("dev", "hardening")
    assert bootstrap_commands(tmp_path) == (
        ("uv", "add", "--optional", "dev", "--no-sync", "pytest-cov"),
        ("uv", "sync", "--extra", "dev", "--group", "hardening"),
    )


def test_missing_group_packages_normalizes_versioned_requirements(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "sample"
version = "0.1.0"

[dependency-groups]
dev = ["pytest>=8", "pytest_cov"]
hardening = ["mutmut", "Pylint>=4"]
""".strip()
    )

    assert missing_group_packages(tmp_path) == {
        "dev": ("ruff",),
        "hardening": ("pytest-crap", "xenon"),
    }


def test_bootstrap_commands_are_bounded_and_skip_absent_project_features(
    tmp_path: Path,
) -> None:
    assert bootstrap_commands(tmp_path) == ()

    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='sample'\nversion='0.1.0'\n"
        "[dependency-groups]\nhardening=['mutmut']\n"
    )
    (tmp_path / ".git").mkdir()

    assert bootstrap_commands(tmp_path) == (
        (
            "uv",
            "add",
            "--group",
            "dev",
            "--no-sync",
            "pytest",
            "pytest-cov",
            "ruff",
        ),
        (
            "uv",
            "add",
            "--group",
            "hardening",
            "--no-sync",
            "pytest-crap",
            "xenon",
            "pylint",
        ),
        ("uv", "sync", "--group", "dev", "--group", "hardening"),
        ("codegraph", "init", "-i"),
    )


def test_bootstrap_commands_do_not_reinitialize_codegraph(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='sample'\nversion='0.1.0'\n"
        "[dependency-groups]\ndev=['pytest']\n"
    )
    (tmp_path / ".codegraph").mkdir()

    assert bootstrap_commands(tmp_path) == (
        ("uv", "add", "--group", "dev", "--no-sync", "pytest-cov", "ruff"),
        (
            "uv",
            "add",
            "--group",
            "hardening",
            "--no-sync",
            "mutmut",
            "pytest-crap",
            "xenon",
            "pylint",
        ),
        ("uv", "sync", "--group", "dev", "--group", "hardening"),
    )


def test_integrate_project_preserves_settings_and_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    cli = tmp_path / "bin/swarm"
    (project / ".git/info").mkdir(parents=True)
    (project / ".claude").mkdir()
    (project / ".claude/agents").mkdir()
    (project / ".claude/agents/data-engineer.md").write_text("Review data changes.\n")
    (project / ".claude/settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(make test)"]}})
    )

    first = integrate_project(project, cli_path=cli)
    second = integrate_project(project, cli_path=cli)

    assert first == second
    claude = json.loads((project / ".claude/settings.json").read_text())
    assert "Bash(make test)" in claude["permissions"]["allow"]
    assert "mcp__codegraph__codegraph_context" in claude["permissions"]["allow"]
    assert claude["hooks"]["Stop"][0]["hooks"][0]["command"].endswith(" gate stop")
    codex = json.loads((project / ".codex/hooks.json").read_text())
    assert codex["hooks"]["PostToolUse"][0]["matcher"] == "apply_patch|Edit|Write"
    assert "hardening" in (project / ".swarmforge/python-gates.toml").read_text()
    assert "[mcp_servers.codegraph]" in (project / ".codex/config.toml").read_text()
    runtime = project / ".swarmforge/runtime"
    assert "lazy-window data-engineer" in (runtime / "swarmforge.conf").read_text()
    assert "[agents.data-engineer]" in (runtime / "project-agents.toml").read_text()
    assert (runtime / "roles/data-engineer.prompt").is_file()
    exclude = (project / ".git/info/exclude").read_text()
    assert exclude.count("# SwarmForge local integration") == 1


def test_install_cli_creates_idempotent_local_links(tmp_path: Path) -> None:
    framework = tmp_path / "framework"
    bin_dir = tmp_path / "bin"
    framework.mkdir()
    (framework / "swarm").write_text("launcher")
    (framework / "close-swarm").write_text("closer")

    install_cli(bin_dir=bin_dir, framework_root=framework)
    install_cli(bin_dir=bin_dir, framework_root=framework)

    assert (bin_dir / "swarm").resolve() == framework / "swarm"
    assert (bin_dir / "close-swarm").resolve() == framework / "close-swarm"


def test_install_cli_refuses_to_replace_unmanaged_file(tmp_path: Path) -> None:
    framework = tmp_path / "framework"
    bin_dir = tmp_path / "bin"
    framework.mkdir()
    bin_dir.mkdir()
    (framework / "swarm").write_text("launcher")
    (framework / "close-swarm").write_text("closer")
    (bin_dir / "swarm").write_text("some other command")

    with pytest.raises(FileExistsError):
        install_cli(bin_dir=bin_dir, framework_root=framework)
