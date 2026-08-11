from __future__ import annotations

import argparse

import pytest

from swarmforge.gates.registry import RegisteredAgent
from swarmforge.gates.route_recommendation import (
    _read_document,
    validate_recommendation,
)


def registered(name: str) -> RegisteredAgent:
    return RegisteredAgent(
        name=name,
        role=name,
        backend_instance="authorized",
        mode="lazy",
        prompt=f"swarmforge/roles/{name}.prompt",
        tags=(),
        routing=None,
    )


def recommendation(reviewers: object) -> dict[str, object]:
    return {
        "schema_version": 2,
        "route": "done",
        "score": 0,
        "reasons": ["signal:tests-docs-only"],
        "commit": "a" * 40,
        "reviewers": reviewers,
    }


def test_revalidation_drops_unknown_reviewers() -> None:
    result = validate_recommendation(
        recommendation(["known", "unknown"]),
        expected_commit="a" * 10,
        registered_agents={"known": registered("known")},
    )

    assert result is not None
    assert result["reviewers"] == ["known"]


def test_revalidation_ignores_malformed_or_wrong_commit_payload() -> None:
    agents = {"known": registered("known")}

    assert (
        validate_recommendation(
            recommendation("known"),
            expected_commit="a" * 10,
            registered_agents=agents,
        )
        is None
    )
    assert (
        validate_recommendation(
            recommendation(["known"]),
            expected_commit="b" * 10,
            registered_agents=agents,
        )
        is None
    )


def test_revalidation_rejects_unbounded_reviewer_names() -> None:
    name = "a" * 65

    assert (
        validate_recommendation(
            recommendation([name]),
            expected_commit="a" * 10,
            registered_agents={name: registered(name)},
        )
        is None
    )


def test_transport_rejects_oversized_payload_before_json_parsing() -> None:
    args = argparse.Namespace(path=None, payload=" " * 4_097)

    with pytest.raises(ValueError, match="too large"):
        _read_document(args)
