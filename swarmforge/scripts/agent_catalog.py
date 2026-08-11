#!/usr/bin/env python3
"""Emit the small, trusted agent catalog exposed to the planner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from swarmforge.gates.registry import load_registry
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from swarmforge.gates.registry import load_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    registry_path = root / "swarmforge" / "project-agents.toml"
    if not registry_path.exists():
        return 0
    registry = load_registry(root)
    for name, agent in registry.agents.items():
        print(
            "\t".join(
                [
                    name,
                    agent.role,
                    agent.backend_instance,
                    agent.mode,
                    ",".join(agent.tags),
                ]
            )
        )
    if registry.errors:
        errors = ",".join(
            f"{name}:{reason}" for name, reason in registry.errors.items()
        )
        print(f"INVALID_AGENT_REGISTRY: {errors}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
