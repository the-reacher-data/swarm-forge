#!/usr/bin/env python3
"""Validate bounded route recommendations for handoff transport."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
import re
from typing import Any

try:
    from swarmforge.gates.registry import RegisteredAgent, load_registry
except ModuleNotFoundError:  # Direct execution resolves sibling modules.
    from registry import RegisteredAgent, load_registry  # type: ignore[no-redef]


SCHEMA_VERSION = 2
ROUTES = {"architect", "coder", "done", "security-reviewer"}
REASONS = {
    "class:migrations",
    "class:security",
    "gate:failed",
    "reviewers:truncated",
    "signal:concurrency",
    "signal:large-change",
    "signal:lockfile",
    "signal:public-api",
    "signal:tests-docs-only",
}
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
MAX_RECOMMENDATION_CHARS = 4_096
MAX_REVIEWER_LENGTH = 64


def validate_recommendation(
    document: object,
    *,
    expected_commit: str,
    registered_agents: Mapping[str, RegisteredAgent],
) -> dict[str, object] | None:
    """Normalize trusted reviewers; ignore malformed recommendations."""
    if not isinstance(document, Mapping) or set(document) != {
        "schema_version",
        "route",
        "score",
        "reasons",
        "commit",
        "reviewers",
    }:
        return None
    route = document.get("route")
    score = document.get("score")
    reasons = document.get("reasons")
    commit = document.get("commit")
    reviewers = document.get("reviewers")
    if document.get("schema_version") != SCHEMA_VERSION or route not in ROUTES:
        return None
    if not isinstance(score, int) or isinstance(score, bool) or score < 0:
        return None
    if (
        not isinstance(reasons, list)
        or not all(isinstance(reason, str) and reason in REASONS for reason in reasons)
        or reasons != sorted(set(reasons))
    ):
        return None
    if (
        not isinstance(commit, str)
        or COMMIT_PATTERN.fullmatch(commit) is None
        or not commit.startswith(expected_commit.lower())
    ):
        return None
    if (
        not isinstance(reviewers, list)
        or len(reviewers) > 2
        or not all(
            isinstance(reviewer, str)
            and reviewer
            and len(reviewer) <= MAX_REVIEWER_LENGTH
            for reviewer in reviewers
        )
        or len(set(reviewers)) != len(reviewers)
        or route in reviewers
    ):
        return None
    trusted_reviewers = [
        reviewer for reviewer in reviewers if reviewer in registered_agents
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "route": route,
        "score": score,
        "reasons": reasons,
        "commit": commit,
        "reviewers": trusted_reviewers,
    }


def _read_document(args: argparse.Namespace) -> Any:
    payload = args.path.read_text() if args.path is not None else args.payload
    if len(payload) > MAX_RECOMMENDATION_CHARS:
        raise ValueError("route recommendation is too large")
    return json.loads(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--path", type=Path)
    source.add_argument("--payload")
    args = parser.parse_args()
    try:
        document = _read_document(args)
        registry = load_registry(args.root)
        recommendation = validate_recommendation(
            document,
            expected_commit=args.commit,
            registered_agents=registry.agents,
        )
    except (OSError, ValueError):
        recommendation = None
    if recommendation is not None:
        print(json.dumps(recommendation, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
