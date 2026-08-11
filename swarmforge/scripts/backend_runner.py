#!/usr/bin/env python3
"""Resolve a SwarmForge backend instance and replace this process with its CLI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any


LEGACY_BACKENDS = {
    "claude": {"kind": "claude", "command": ["claude"]},
    "codex": {"kind": "codex", "command": ["codex"]},
    "copilot": {"kind": "copilot", "command": ["copilot"]},
    "grok": {"kind": "grok", "command": ["grok"]},
}


class ConfigurationError(Exception):
    pass


def load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def merge_instance(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(
        {key: value for key, value in override.items() if key != "environment"}
    )
    merged["environment"] = {
        **base.get("environment", {}),
        **override.get("environment", {}),
    }
    return merged


def resolve_instance(root: Path, name: str) -> dict[str, Any]:
    runtime = root / ".swarmforge/runtime/backends.toml"
    portable = load_toml(
        runtime if runtime.is_file() else root / "swarmforge" / "backends.toml"
    )
    local = load_toml(root / ".swarmforge" / "backends.local.toml")
    portable_instance = portable.get("instances", {}).get(name)
    local_instance = local.get("instances", {}).get(name, {})
    if portable_instance is None:
        portable_instance = LEGACY_BACKENDS.get(name)
    if portable_instance is None:
        raise ConfigurationError(f"Unknown backend instance: {name}")
    instance = merge_instance(portable_instance, local_instance)
    kind = instance.get("kind")
    command = instance.get("command")
    if kind not in LEGACY_BACKENDS:
        raise ConfigurationError(f"Unsupported backend kind for {name}: {kind}")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise ConfigurationError(f"Backend {name} requires a non-empty command array")
    environment = instance.get("environment", {})
    if not isinstance(environment, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ConfigurationError(f"Backend {name} environment must contain strings")
    if profile := instance.get("profile"):
        if kind != "claude" or not isinstance(profile, str):
            raise ConfigurationError(f"Backend {name} has an invalid profile")
        environment = {
            "CLAUDE_CONFIG_DIR": str(Path.home() / ".aisw" / "profiles" / profile),
            **environment,
        }
    instance["environment"] = {
        key: os.path.expandvars(os.path.expanduser(value))
        for key, value in environment.items()
    }
    return instance


def build_argv(instance: dict[str, Any], args: argparse.Namespace) -> list[str]:
    command = list(instance["command"])
    prompt = args.prompt.read_text()
    kind = instance["kind"]
    if kind == "claude":
        return command + [
            "--append-system-prompt-file",
            str(args.prompt),
            "--permission-mode",
            "acceptEdits",
            "-n",
            f"SwarmForge {args.display}",
            *args.extra,
            prompt,
        ]
    if kind == "codex":
        return command + ["-C", str(args.worktree), *args.extra, prompt]
    if kind == "copilot":
        return command + [
            "-C",
            str(args.worktree),
            "--name",
            f"SwarmForge {args.display}",
            *args.extra,
            "-i",
            prompt,
        ]
    permission = (
        "bypassPermissions"
        if any(
            item in args.extra
            for item in ("--always-approve", "--yolo", "bypassPermissions")
        )
        else "acceptEdits"
    )
    return command + [
        "--cwd",
        str(args.worktree),
        "--permission-mode",
        permission,
        *args.extra,
        "--rules",
        prompt,
        "--verbatim",
        prompt,
    ]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--root", type=Path, required=True)
    result.add_argument("--instance", required=True)
    result.add_argument("--role")
    result.add_argument("--display")
    result.add_argument("--worktree", type=Path)
    result.add_argument("--prompt", type=Path)
    result.add_argument("--check", action="store_true")
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("extra", nargs="*")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        instance = resolve_instance(args.root.resolve(), args.instance)
        if not args.dry_run:
            executable = instance["command"][0]
            if shutil.which(executable) is None:
                raise ConfigurationError(
                    f"Backend {args.instance} command not found: {executable}"
                )
            profile_dir = instance["environment"].get("CLAUDE_CONFIG_DIR")
            if profile_dir and not Path(profile_dir).is_dir():
                raise ConfigurationError(
                    f"Backend {args.instance} profile not found: {profile_dir}"
                )
        if args.check:
            return 0
        missing = [
            name
            for name in ("role", "display", "worktree", "prompt")
            if getattr(args, name) is None
        ]
        if missing:
            raise ConfigurationError(f"Backend launch requires: {', '.join(missing)}")
        argv = build_argv(instance, args)
    except (ConfigurationError, OSError, tomllib.TOMLDecodeError) as error:
        print(error, file=sys.stderr)
        return 2
    environment = {
        **os.environ,
        **instance["environment"],
        "SWARMFORGE_ROLE": args.role,
    }
    if args.dry_run:
        print(json.dumps({"argv": argv, "environment": instance["environment"]}))
        return 0
    os.chdir(args.worktree)
    os.execvpe(argv[0], argv, environment)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
