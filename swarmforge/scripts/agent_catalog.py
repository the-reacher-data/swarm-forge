#!/usr/bin/env python3
"""Emit the small, trusted agent catalog exposed to the planner."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path


def inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    registry = root / "swarmforge" / "project-agents.toml"
    if not registry.exists():
        return 0
    try:
        with registry.open("rb") as stream:
            agents = tomllib.load(stream).get("agents", {})
        for name, config in sorted(agents.items()):
            prompt = root / config["prompt"]
            if not inside(root, prompt) or not prompt.is_file():
                raise ValueError(
                    f"untrusted or missing prompt for {name}: {config.get('prompt')}"
                )
            tags = ",".join(config.get("tags", []))
            print(
                "\t".join(
                    [
                        name,
                        config["role"],
                        config["backend_instance"],
                        config.get("mode", "lazy"),
                        tags,
                    ]
                )
            )
    except (KeyError, OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"INVALID_AGENT_REGISTRY: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
