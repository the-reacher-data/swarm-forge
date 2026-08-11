from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from swarmforge.gates.manifest import Manifest
from swarmforge.gates.registry import RegisteredAgent, RoutingRule
from swarmforge.gates.risk import DEFAULT_CONFIG, load_risk_config, route_manifest


def built_manifest(
    paths: tuple[str, ...],
    *,
    files_changed: int | None = None,
    insertions: int = 0,
    gates: dict[str, str] | None = None,
    concurrency: bool = False,
) -> Manifest:
    return Manifest(
        document={
            "schema_version": 1,
            "commit": "a" * 40,
            "base": "b" * 40,
            "diff": {
                "files_changed": len(paths) if files_changed is None else files_changed,
                "insertions": insertions,
                "deletions": 0,
            },
            "paths": list(paths),
            "gates": gates or {"pre-handoff": "pass"},
            "codegraph_impact": None,
            "truncated": False,
        },
        all_paths=paths,
        concurrency_signal=concurrency,
    )


def agent(
    name: str,
    *,
    paths: tuple[str, ...] = (),
    signals: tuple[str, ...] = (),
    priority: int = 50,
    mandatory: bool = False,
) -> RegisteredAgent:
    return RegisteredAgent(
        name=name,
        role=name,
        backend_instance="authorized",
        mode="lazy",
        prompt=f"swarmforge/roles/{name}.prompt",
        tags=(),
        routing=RoutingRule(paths, signals, priority, mandatory),
    )


def test_tests_and_docs_only_force_done() -> None:
    result = route_manifest(
        built_manifest(("test/python/test_api.py", "docs/routing.md"))
    )

    assert result == {
        "schema_version": 2,
        "route": "done",
        "score": 0,
        "reasons": ["signal:tests-docs-only"],
        "commit": "a" * 40,
        "reviewers": [],
    }


def test_standard_pytest_directory_is_tests_docs_only() -> None:
    result = route_manifest(built_manifest(("tests/test_api.py",)))

    assert result["route"] == "done"
    assert result["reasons"] == ["signal:tests-docs-only"]


def test_reviewers_are_matched_deduplicated_ordered_and_capped() -> None:
    routing_agents = {
        "zeta": agent("zeta", paths=("src/**",), priority=1),
        "beta": agent("beta", signals=("public-api",), priority=2),
        "alpha": agent("alpha", paths=("src/**",), priority=99, mandatory=True),
    }

    result = route_manifest(
        built_manifest(("src/api.py", "swarmforge/gates/new.py")),
        routing_agents=routing_agents,
    )

    assert result["reviewers"] == ["alpha", "zeta"]
    assert result["reasons"] == ["reviewers:truncated", "signal:public-api"]


def test_equal_priority_reviewers_are_ordered_by_name() -> None:
    routing_agents = {
        "zeta": agent("zeta", paths=("src/**",), priority=10),
        "alpha": agent("alpha", paths=("src/**",), priority=10),
    }

    result = route_manifest(
        built_manifest(("src/app.py",)), routing_agents=routing_agents
    )

    assert result["reviewers"] == ["alpha", "zeta"]


def test_primary_route_is_not_duplicated_as_reviewer() -> None:
    routing_agents = {
        "security-reviewer": agent(
            "security-reviewer", paths=("src/auth/**",), mandatory=True
        )
    }

    result = route_manifest(
        built_manifest(("src/auth/token.py",)), routing_agents=routing_agents
    )

    assert result["route"] == "security-reviewer"
    assert result["reviewers"] == []


def test_recommendation_does_not_start_backends_or_load_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_call(*args: object, **kwargs: object) -> None:
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(subprocess, "run", unexpected_call)
    monkeypatch.setattr(Path, "read_text", unexpected_call)

    result = route_manifest(
        built_manifest(("src/app.py",)),
        routing_agents={"qa": agent("qa", paths=("src/**",))},
    )

    assert result["reviewers"] == ["qa"]


def test_large_change_routes_to_architect() -> None:
    result = route_manifest(
        built_manifest(("src/app.py",), files_changed=11),
    )

    assert result["route"] == "architect"
    assert result["score"] >= DEFAULT_CONFIG.architect_threshold
    assert result["reasons"] == ["signal:large-change"]


def test_sensitive_filtered_path_still_routes_to_security_reviewer() -> None:
    manifest = built_manifest(("config/secret-token.env",))
    manifest.document["paths"] = []

    result = route_manifest(manifest)

    assert result["route"] == "security-reviewer"
    assert result["reasons"] == ["class:security"]


def test_failed_gate_has_precedence_over_security() -> None:
    result = route_manifest(
        built_manifest(("src/auth/token.py",), gates={"pre-handoff": "setup_failed"})
    )

    assert result["route"] == "coder"
    assert result["reasons"] == ["class:security", "gate:failed"]


def test_migration_class_routes_to_architect() -> None:
    result = route_manifest(built_manifest(("alembic/versions/001_users.py",)))

    assert result["route"] == "architect"
    assert result["reasons"] == ["class:migrations"]


def test_signals_are_weighted_and_reasons_are_sorted() -> None:
    config = load_risk_config(
        {
            "risk": {
                "architect_threshold": 20,
                "weights": {
                    "lockfile": 1,
                    "concurrency": 2,
                    "public-api": 3,
                    "size": 4,
                },
                "security_patterns": [],
                "migration_patterns": [],
                "public_api_patterns": [],
                "sensitive_patterns": [],
                "max_paths": 10,
            }
        }
    )
    result = route_manifest(
        built_manifest(("pyproject.toml", "swarmforge/gates/new.py"), concurrency=True),
        config,
    )

    assert result["route"] == "done"
    assert result["score"] == 6
    assert result["reasons"] == [
        "signal:concurrency",
        "signal:lockfile",
        "signal:public-api",
    ]


def test_any_invalid_risk_value_discards_the_whole_table() -> None:
    config = load_risk_config(
        {
            "risk": {
                "architect_threshold": 99,
                "weights": {"size": -1},
                "max_paths": 2,
            }
        }
    )

    assert config == DEFAULT_CONFIG
