from __future__ import annotations

from pathlib import Path

from swarmforge.gates.registry import load_registry


def write_registry_project(root: Path, agents: str) -> None:
    roles = root / "swarmforge" / "roles"
    roles.mkdir(parents=True)
    for name in ("data-engineer", "ui-reviewer", "valid"):
        (roles / f"{name}.prompt").write_text("Review the change.\n")
    (root / "swarmforge" / "backends.toml").write_text(
        "[instances.authorized]\nkind = 'claude'\ncommand = ['claude']\n"
    )
    (root / "swarmforge" / "project-agents.toml").write_text(agents)


def test_registry_loads_valid_data_only_routing(tmp_path: Path) -> None:
    write_registry_project(
        tmp_path,
        """
[agents.data-engineer]
role = "data-engineer"
backend_instance = "authorized"
mode = "lazy"
prompt = "swarmforge/roles/data-engineer.prompt"
tags = ["data"]

[agents.data-engineer.routing]
paths = ["dbt/**", "*.sql"]
signals = ["size"]
priority = 10
mandatory = true
""".strip(),
    )

    result = load_registry(tmp_path)

    agent = result.agents["data-engineer"]
    assert result.errors == {}
    assert agent.routing is not None
    assert agent.routing.paths == ("dbt/**", "*.sql")
    assert agent.routing.signals == ("size",)
    assert agent.routing.priority == 10
    assert agent.routing.mandatory is True


def test_registry_loads_ignored_runtime_agents(tmp_path: Path) -> None:
    runtime = tmp_path / ".swarmforge/runtime"
    (runtime / "roles").mkdir(parents=True)
    (runtime / "backends.toml").write_text(
        "[instances.claude-work]\nkind='claude'\ncommand=['claude']\n"
    )
    (runtime / "project-agents.toml").write_text(
        """
[agents.data-engineer]
role = "data-engineer"
backend_instance = "claude-work"
mode = "lazy"
prompt = ".swarmforge/runtime/roles/data-engineer.prompt"
""".strip()
    )
    (runtime / "roles/data-engineer.prompt").write_text("Review data changes.\n")

    result = load_registry(tmp_path)

    assert set(result.agents) == {"data-engineer"}
    assert result.errors == {}


def test_registry_excludes_each_untrusted_agent_with_bounded_reason(
    tmp_path: Path,
) -> None:
    write_registry_project(
        tmp_path,
        """
[agents.valid]
role = "valid"
backend_instance = "authorized"
mode = "lazy"
prompt = "swarmforge/roles/valid.prompt"

[agents.valid.routing]
paths = ["src/**"]

[agents.backend]
role = "backend"
backend_instance = "not-authorized"
mode = "lazy"
prompt = "swarmforge/roles/valid.prompt"

[agents.escape]
role = "escape"
backend_instance = "authorized"
mode = "lazy"
prompt = "../outside.prompt"

[agents.eager]
role = "eager"
backend_instance = "authorized"
mode = "eager"
prompt = "swarmforge/roles/valid.prompt"
""".strip(),
    )

    result = load_registry(tmp_path)

    assert set(result.agents) == {"valid"}
    assert result.errors == {
        "backend": "unauthorized-backend",
        "eager": "invalid-mode",
        "escape": "invalid-prompt",
    }


def test_registry_rejects_unknown_routing_keys_and_signals(tmp_path: Path) -> None:
    write_registry_project(
        tmp_path,
        """
[agents.unknown-key]
role = "unknown-key"
backend_instance = "authorized"
mode = "lazy"
prompt = "swarmforge/roles/valid.prompt"

[agents.unknown-key.routing]
command = ["run-me"]

[agents.unknown-signal]
role = "unknown-signal"
backend_instance = "authorized"
mode = "lazy"
prompt = "swarmforge/roles/valid.prompt"

[agents.unknown-signal.routing]
signals = ["network"]
""".strip(),
    )

    result = load_registry(tmp_path)

    assert result.agents == {}
    assert result.errors == {
        "unknown-key": "unknown-routing-key",
        "unknown-signal": "invalid-routing-signal",
    }


def test_registry_bounds_agent_names_and_routing_lists(tmp_path: Path) -> None:
    long_name = "a" * 65
    write_registry_project(
        tmp_path,
        f"""
[agents.{long_name}]
role = "valid"
backend_instance = "authorized"
mode = "lazy"
prompt = "swarmforge/roles/valid.prompt"

[agents.valid]
role = "valid"
backend_instance = "authorized"
mode = "lazy"
prompt = "swarmforge/roles/valid.prompt"

[agents.valid.routing]
paths = {["src/**"] * 33!r}
""".strip(),
    )

    result = load_registry(tmp_path)

    assert result.agents == {}
    assert result.errors == {
        "agent-1": "invalid-name",
        "valid": "invalid-routing-paths",
    }
