from __future__ import annotations

from swarmforge.gates.registry import RegisteredAgent
from swarmforge.gates.route_recommendation import validate_recommendation


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
