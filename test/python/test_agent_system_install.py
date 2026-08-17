from __future__ import annotations

from pathlib import Path

import pytest

from agent_system.install import (
    InstallError,
    apply_install,
    load_install_config,
    main,
    plan_install,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_unknown_file_is_reported_as_conflict_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source" / "verifier.md"
    target = tmp_path / "profile" / "agents" / "verifier.md"
    _write(source, "new definition")
    _write(target, "owned by user")

    actions = plan_install(source.parent, tmp_path / "profile")

    assert [(action.target, action.status) for action in actions] == [
        (target, "conflict")
    ]
    assert target.read_text(encoding="utf-8") == "owned by user"


def test_identical_file_is_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "source" / "verifier.md"
    target = tmp_path / "profile" / "agents" / "verifier.md"
    _write(source, "same definition")
    _write(target, "same definition")

    actions = plan_install(source.parent, tmp_path / "profile")

    assert [action.status for action in actions] == ["unchanged"]


def test_apply_creates_files_and_second_plan_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source" / "lead-engineer.md"
    target = tmp_path / "profile" / "agents" / "lead-engineer.md"
    _write(source, "canonical definition")

    actions = plan_install(source.parent, tmp_path / "profile")
    assert [action.status for action in actions] == ["create"]

    apply_install(actions, tmp_path / "backups")

    assert target.read_text(encoding="utf-8") == "canonical definition"
    assert [
        action.status for action in plan_install(source.parent, tmp_path / "profile")
    ] == ["unchanged"]


def test_explicit_replace_backs_up_previous_file(tmp_path: Path) -> None:
    source = tmp_path / "source" / "security-reviewer.md"
    target = tmp_path / "profile" / "agents" / "security-reviewer.md"
    backup_root = tmp_path / "backups"
    _write(source, "canonical definition")
    _write(target, "previous definition")

    actions = plan_install(
        source.parent,
        tmp_path / "profile",
        replace={"security-reviewer.md"},
    )
    assert [action.status for action in actions] == ["replace"]

    apply_install(actions, backup_root)

    assert target.read_text(encoding="utf-8") == "canonical definition"
    assert actions[0].backup is not None
    assert actions[0].backup.read_text(encoding="utf-8") == "previous definition"


def test_apply_refuses_unresolved_conflicts_before_writing_anything(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    profile_root = tmp_path / "profile"
    _write(source_root / "a.md", "new a")
    _write(source_root / "b.md", "new b")
    _write(profile_root / "agents" / "b.md", "user b")
    actions = plan_install(source_root, profile_root)

    with pytest.raises(InstallError, match="unresolved conflict"):
        apply_install(actions, tmp_path / "backups")

    assert not (profile_root / "agents" / "a.md").exists()
    assert (profile_root / "agents" / "b.md").read_text(encoding="utf-8") == "user b"


def test_replace_cannot_follow_target_symlink_outside_profile(tmp_path: Path) -> None:
    source = tmp_path / "source" / "verifier.md"
    target = tmp_path / "profile" / "agents" / "verifier.md"
    outside = tmp_path / "outside.md"
    _write(source, "canonical")
    _write(outside, "outside")
    target.parent.mkdir(parents=True)
    target.symlink_to(outside)

    actions = plan_install(
        source.parent,
        tmp_path / "profile",
        replace={"verifier.md"},
    )

    assert [action.status for action in actions] == ["conflict"]
    assert outside.read_text(encoding="utf-8") == "outside"


def test_config_resolves_sources_from_config_and_profiles_from_home(
    tmp_path: Path,
) -> None:
    config = tmp_path / "agents" / "install-targets.toml"
    _write(tmp_path / "agents" / "claude" / "verifier.md", "definition")
    _write(
        config,
        """version = 1
backup_path = ".cuimo/backups"

[[targets]]
name = "claude-personal"
source = "claude"
profile = ".aisw/profiles/personal"
replace = ["security-reviewer.md"]
""",
    )
    fake_home = tmp_path / "home"

    loaded = load_install_config(config, home=fake_home)

    assert loaded.backup_root == fake_home / ".cuimo/backups"
    assert loaded.targets[0].source_root == tmp_path / "agents" / "claude"
    assert loaded.targets[0].profile_root == fake_home / ".aisw/profiles/personal"
    assert loaded.targets[0].replace == frozenset({"security-reviewer.md"})


def test_cli_dry_run_writes_report_without_installing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "agents" / "install-targets.toml"
    report = tmp_path / "dry-run.yaml"
    _write(tmp_path / "agents" / "codex" / "verifier.toml", "definition")
    _write(
        config,
        """version = 1
backup_path = ".cuimo/backups"

[[targets]]
name = "codex-global"
source = "codex"
profile = ".codex"
replace = []
""",
    )
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))

    exit_code = main(["--config", str(config), "--report", str(report)])

    assert exit_code == 0
    assert not (fake_home / ".codex/agents/verifier.toml").exists()
    assert "status: create" in report.read_text(encoding="utf-8")


def test_cli_apply_installs_and_records_portable_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "agents" / "install-targets.toml"
    report = tmp_path / "applied.yaml"
    _write(tmp_path / "agents" / "codex" / "verifier.toml", "definition")
    _write(
        config,
        """version = 1
backup_path = ".cuimo/backups"

[[targets]]
name = "codex-global"
source = "codex"
profile = ".codex"
replace = []
""",
    )
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))

    exit_code = main(["--config", str(config), "--report", str(report), "--apply"])

    assert exit_code == 0
    assert (fake_home / ".codex/agents/verifier.toml").read_text(
        encoding="utf-8"
    ) == "definition"
    rendered = report.read_text(encoding="utf-8")
    assert str(fake_home) not in rendered
    assert "applied: true" in rendered
