#!/usr/bin/env python3
"""Validate the repository-owned lazy specialist registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import tomllib


ALLOWED_AGENT_KEYS = {
    "role",
    "backend_instance",
    "mode",
    "prompt",
    "tags",
    "routing",
}
ALLOWED_ROUTING_KEYS = {"paths", "signals", "priority", "mandatory"}
ALLOWED_SIGNALS = {"lockfile", "concurrency", "public-api", "size"}
ALLOWED_MODES = {"lazy"}
MAX_AGENTS = 32
MAX_IDENTIFIER_LENGTH = 64
MAX_PROMPT_LENGTH = 256
MAX_ROUTING_PATHS = 32
MAX_ROUTING_PATH_LENGTH = 256
MAX_TAGS = 16
MAX_TAG_LENGTH = 64
ROLE_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


@dataclass(frozen=True)
class RoutingRule:
    paths: tuple[str, ...]
    signals: tuple[str, ...]
    priority: int
    mandatory: bool


@dataclass(frozen=True)
class RegisteredAgent:
    name: str
    role: str
    backend_instance: str
    mode: str
    prompt: str
    tags: tuple[str, ...]
    routing: RoutingRule | None


@dataclass(frozen=True)
class RegistryResult:
    agents: dict[str, RegisteredAgent]
    errors: dict[str, str]


def _string_list(
    value: object, *, max_items: int, max_length: int
) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item and len(item) <= max_length for item in value
    ):
        return None
    if len(value) > max_items:
        return None
    return tuple(value)


def _valid_prompt(prompt: object) -> bool:
    if (
        not isinstance(prompt, str)
        or not prompt
        or len(prompt) > MAX_PROMPT_LENGTH
        or "\\" in prompt
    ):
        return False
    path = PurePosixPath(prompt)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.parts[:2] == ("swarmforge", "roles")
        and len(path.parts) >= 3
    )


def _routing(value: object) -> tuple[RoutingRule | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, Mapping):
        return None, "invalid-routing"
    if not set(value).issubset(ALLOWED_ROUTING_KEYS):
        return None, "unknown-routing-key"
    paths = _string_list(
        value.get("paths", []),
        max_items=MAX_ROUTING_PATHS,
        max_length=MAX_ROUTING_PATH_LENGTH,
    )
    if paths is None:
        return None, "invalid-routing-paths"
    signals = _string_list(
        value.get("signals", []),
        max_items=len(ALLOWED_SIGNALS),
        max_length=MAX_IDENTIFIER_LENGTH,
    )
    if signals is None or not set(signals).issubset(ALLOWED_SIGNALS):
        return None, "invalid-routing-signal"
    priority = value.get("priority", 50)
    if not isinstance(priority, int) or isinstance(priority, bool):
        return None, "invalid-routing-priority"
    mandatory = value.get("mandatory", False)
    if not isinstance(mandatory, bool):
        return None, "invalid-routing-mandatory"
    return RoutingRule(paths, signals, priority, mandatory), None


def validate_registry(
    document: object,
    *,
    authorized_backends: set[str],
) -> RegistryResult:
    """Return valid agents and stable error codes without performing I/O."""
    if not isinstance(document, Mapping):
        return RegistryResult({}, {"registry": "invalid-registry"})
    raw_agents = document.get("agents", {})
    if not isinstance(raw_agents, Mapping):
        return RegistryResult({}, {"registry": "invalid-agents"})
    if len(raw_agents) > MAX_AGENTS:
        return RegistryResult({}, {"registry": "too-many-agents"})

    agents: dict[str, RegisteredAgent] = {}
    errors: dict[str, str] = {}
    for index, name in enumerate(sorted(raw_agents)):
        raw = raw_agents[name]
        error_key = (
            name
            if isinstance(name, str) and len(name) <= MAX_IDENTIFIER_LENGTH
            else f"agent-{index + 1}"
        )
        reason: str | None = None
        if not isinstance(name, str) or ROLE_PATTERN.fullmatch(name) is None:
            reason = "invalid-name"
        elif not isinstance(raw, Mapping):
            reason = "invalid-agent"
        elif not set(raw).issubset(ALLOWED_AGENT_KEYS):
            reason = "unknown-agent-key"

        if reason is not None:
            errors[error_key] = reason
            continue
        assert isinstance(raw, Mapping)
        role = raw.get("role")
        backend = raw.get("backend_instance")
        mode = raw.get("mode", "lazy")
        prompt = raw.get("prompt")
        tags = _string_list(
            raw.get("tags", []), max_items=MAX_TAGS, max_length=MAX_TAG_LENGTH
        )
        if not isinstance(role, str) or ROLE_PATTERN.fullmatch(role) is None:
            reason = "invalid-role"
        elif (
            not isinstance(backend, str)
            or len(backend) > MAX_IDENTIFIER_LENGTH
            or backend not in authorized_backends
        ):
            reason = "unauthorized-backend"
        elif not isinstance(mode, str) or mode not in ALLOWED_MODES:
            reason = "invalid-mode"
        elif not _valid_prompt(prompt):
            reason = "invalid-prompt"
        elif tags is None:
            reason = "invalid-tags"
        else:
            routing, reason = _routing(raw.get("routing"))
            if reason is None:
                assert isinstance(prompt, str)
                agents[name] = RegisteredAgent(
                    name=name,
                    role=role,
                    backend_instance=backend,
                    mode=mode,
                    prompt=prompt,
                    tags=tags,
                    routing=routing,
                )
        if reason is not None:
            errors[error_key] = reason
    return RegistryResult(agents, errors)


def _load_toml(path: Path) -> object:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def load_registry(root: Path) -> RegistryResult:
    """Load and validate the registry against authoritative backend instances."""
    try:
        backends = _load_toml(root / "swarmforge" / "backends.toml")
        registry = _load_toml(root / "swarmforge" / "project-agents.toml")
    except (OSError, tomllib.TOMLDecodeError):
        return RegistryResult({}, {"registry": "invalid-toml"})
    if not isinstance(backends, Mapping) or not isinstance(
        backends.get("instances"), Mapping
    ):
        return RegistryResult({}, {"registry": "invalid-backends"})
    result = validate_registry(registry, authorized_backends=set(backends["instances"]))
    roles_root = (root / "swarmforge" / "roles").resolve()
    agents = dict(result.agents)
    errors = dict(result.errors)
    for name, agent in result.agents.items():
        candidate = root / agent.prompt
        try:
            candidate.resolve().relative_to(roles_root)
        except (OSError, ValueError):
            agents.pop(name)
            errors[name] = "invalid-prompt"
            continue
        if not candidate.is_file():
            agents.pop(name)
            errors[name] = "missing-prompt"
    return RegistryResult(agents, dict(sorted(errors.items())))
