#!/usr/bin/env python3
"""Pure, deterministic risk scoring and routing for gate manifests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import fnmatch

from swarmforge.gates.manifest import DEFAULT_SENSITIVE_PATTERNS, Manifest


SCHEMA_VERSION = 1
SIGNALS = ("lockfile", "concurrency", "public-api", "size")
DEFAULT_SECURITY_PATTERNS = (
    "*auth*",
    "*crypto*",
    "*secret*",
    "*token*",
    "*password*",
    "*permission*",
)
DEFAULT_MIGRATION_PATTERNS = (
    "*migration*",
    "*schema*",
    "*.sql",
    "*alembic*",
)
DEFAULT_PUBLIC_API_PATTERNS = (
    "**/__init__.py",
    "swarmforge/scripts/*.sh",
    "swarmforge/gates/*.py",
)
LOCKFILE_PATTERNS = (
    "pyproject.toml",
    "requirements*",
    "*.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "cargo.lock",
    "go.sum",
)
RISK_KEYS = {
    "architect_threshold",
    "weights",
    "security_patterns",
    "migration_patterns",
    "public_api_patterns",
    "sensitive_patterns",
    "max_paths",
}


@dataclass(frozen=True)
class RiskConfig:
    architect_threshold: int
    weights: Mapping[str, int]
    security_patterns: tuple[str, ...]
    migration_patterns: tuple[str, ...]
    public_api_patterns: tuple[str, ...]
    sensitive_patterns: tuple[str, ...]
    max_paths: int


DEFAULT_CONFIG = RiskConfig(
    architect_threshold=5,
    weights={
        "lockfile": 2,
        "concurrency": 3,
        "public-api": 2,
        "size": 5,
    },
    security_patterns=DEFAULT_SECURITY_PATTERNS,
    migration_patterns=DEFAULT_MIGRATION_PATTERNS,
    public_api_patterns=DEFAULT_PUBLIC_API_PATTERNS,
    sensitive_patterns=DEFAULT_SENSITIVE_PATTERNS,
    max_paths=50,
)


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _patterns(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        return None
    return tuple(value)


def load_risk_config(document: object) -> RiskConfig:
    """Validate a complete risk table, falling back wholesale on any error."""
    if not isinstance(document, Mapping):
        return DEFAULT_CONFIG
    raw = document.get("risk")
    if raw is None:
        return DEFAULT_CONFIG
    if not isinstance(raw, Mapping) or not set(raw).issubset(RISK_KEYS):
        return DEFAULT_CONFIG

    threshold = raw.get("architect_threshold", DEFAULT_CONFIG.architect_threshold)
    max_paths = raw.get("max_paths", DEFAULT_CONFIG.max_paths)
    if not _positive_int(threshold) or not _positive_int(max_paths):
        return DEFAULT_CONFIG

    raw_weights = raw.get("weights", {})
    if not isinstance(raw_weights, Mapping) or not set(raw_weights).issubset(SIGNALS):
        return DEFAULT_CONFIG
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in raw_weights.values()
    ):
        return DEFAULT_CONFIG
    weights = dict(DEFAULT_CONFIG.weights)
    weights.update(raw_weights)

    resolved_patterns: dict[str, tuple[str, ...]] = {}
    for key, defaults in (
        ("security_patterns", DEFAULT_CONFIG.security_patterns),
        ("migration_patterns", DEFAULT_CONFIG.migration_patterns),
        ("public_api_patterns", DEFAULT_CONFIG.public_api_patterns),
        ("sensitive_patterns", DEFAULT_CONFIG.sensitive_patterns),
    ):
        additions = _patterns(raw.get(key, []))
        if additions is None:
            return DEFAULT_CONFIG
        resolved_patterns[key] = (*defaults, *additions)

    return RiskConfig(
        architect_threshold=threshold,
        weights=weights,
        security_patterns=resolved_patterns["security_patterns"],
        migration_patterns=resolved_patterns["migration_patterns"],
        public_api_patterns=resolved_patterns["public_api_patterns"],
        sensitive_patterns=resolved_patterns["sensitive_patterns"],
        max_paths=max_paths,
    )


def _matches_any(paths: Sequence[str], patterns: Sequence[str]) -> bool:
    return any(
        fnmatch.fnmatchcase(path.lower(), pattern.lower())
        for path in paths
        for pattern in patterns
    )


def _tests_docs_only(paths: Sequence[str]) -> bool:
    return bool(paths) and all(
        path.startswith(("test/", "docs/")) or path.lower().endswith(".md")
        for path in paths
    )


def route_manifest(
    manifest: Manifest, config: RiskConfig = DEFAULT_CONFIG
) -> dict[str, object]:
    """Return a bounded route recommendation without performing any I/O."""
    paths = manifest.all_paths
    document = manifest.document
    diff = document.get("diff")
    gates = document.get("gates")
    commit = document.get("commit")
    if not isinstance(diff, Mapping) or not isinstance(gates, Mapping):
        raise ValueError("invalid manifest")
    if not isinstance(commit, str):
        raise ValueError("invalid manifest commit")

    security = _matches_any(
        paths, (*config.security_patterns, *config.sensitive_patterns)
    )
    migrations = _matches_any(paths, config.migration_patterns)
    reasons: set[str] = set()
    if security:
        reasons.add("class:security")
    if migrations:
        reasons.add("class:migrations")

    signals = {
        "lockfile": _matches_any(paths, LOCKFILE_PATTERNS),
        "concurrency": manifest.concurrency_signal,
        "public-api": _matches_any(paths, config.public_api_patterns),
        "size": (
            int(diff.get("files_changed", 0)) > 10
            or int(diff.get("insertions", 0)) > 500
        ),
    }
    score = 0
    for signal, matched in signals.items():
        if matched:
            score += config.weights[signal]
            reason = "large-change" if signal == "size" else signal
            reasons.add(f"signal:{reason}")

    tests_docs_only = _tests_docs_only(paths)
    if tests_docs_only:
        reasons.add("signal:tests-docs-only")
    gate_failed = any(result != "pass" for result in gates.values())
    if gate_failed:
        reasons.add("gate:failed")

    if gate_failed:
        route = "coder"
    elif security:
        route = "security-reviewer"
    elif migrations:
        route = "architect"
    elif tests_docs_only:
        route = "done"
    elif score >= config.architect_threshold:
        route = "architect"
    else:
        route = "done"

    return {
        "schema_version": SCHEMA_VERSION,
        "route": route,
        "score": score,
        "reasons": sorted(reasons),
        "commit": commit,
    }
