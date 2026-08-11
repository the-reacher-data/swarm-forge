#!/usr/bin/env python3
"""Check and bootstrap the bounded SwarmForge toolchain for a project."""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]
CORE_TOOLS = ("git", "curl", "tar", "python3", "uv", "tmux", "bb", "codegraph")
MANAGED_GROUPS = ("dev", "hardening")
STANDARD_GROUP_PACKAGES = {
    "dev": ("pytest", "pytest-cov", "ruff"),
    "hardening": ("mutmut", "pytest-crap", "xenon", "pylint"),
}
REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class ToolCheck:
    name: str
    ok: bool
    detail: str


def _toml(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _backend_requirements(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    document = _toml(root / "swarmforge" / "backends.toml")
    instances = document.get("instances", {})
    if not isinstance(instances, dict):
        raise ValueError("swarmforge/backends.toml: instances must be a table")
    commands: set[str] = set()
    profiles: set[str] = set()
    for instance in instances.values():
        if not isinstance(instance, dict):
            raise ValueError("swarmforge/backends.toml: instance must be a table")
        command = instance.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not isinstance(command[0], str)
            or not command[0]
        ):
            raise ValueError("swarmforge/backends.toml: invalid command")
        commands.add(command[0])
        profile = instance.get("profile")
        if profile is not None:
            if not isinstance(profile, str) or not profile:
                raise ValueError("swarmforge/backends.toml: invalid profile")
            profiles.add(profile)
    return tuple(sorted(commands)), tuple(sorted(profiles))


def inspect_machine(
    root: Path,
    *,
    which: Callable[[str], str | None] = shutil.which,
    home: Path | None = None,
) -> tuple[ToolCheck, ...]:
    """Return bounded checks without installing or invoking any tool."""
    backend_commands, profiles = _backend_requirements(root)
    tool_names = tuple(dict.fromkeys((*CORE_TOOLS, *backend_commands)))
    checks = [
        ToolCheck(name, bool(path := which(name)), path or "not-found")
        for name in tool_names
    ]
    profile_root = (home or Path.home()) / ".aisw" / "profiles"
    checks.extend(
        ToolCheck(
            f"claude-profile:{profile}",
            (path := profile_root / profile).is_dir(),
            str(path),
        )
        for profile in profiles
    )
    return tuple(checks)


def project_groups(root: Path) -> tuple[str, ...]:
    """Return managed dependency groups already declared by the project."""
    document = _toml(root / "pyproject.toml")
    raw_groups = document.get("dependency-groups", {})
    if not isinstance(raw_groups, dict):
        raise ValueError("pyproject.toml: dependency-groups must be a table")
    return tuple(
        group for group in MANAGED_GROUPS if isinstance(raw_groups.get(group), list)
    )


def _normalized_requirement(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = REQUIREMENT_NAME.match(value)
    if match is None:
        return None
    return re.sub(r"[-_.]+", "-", match.group().lower())


def missing_group_packages(root: Path) -> dict[str, tuple[str, ...]]:
    """Return standard packages absent from each managed dependency group."""
    document = _toml(root / "pyproject.toml")
    raw_groups = document.get("dependency-groups", {})
    if not isinstance(raw_groups, dict):
        raise ValueError("pyproject.toml: dependency-groups must be a table")
    missing: dict[str, tuple[str, ...]] = {}
    for group, expected in STANDARD_GROUP_PACKAGES.items():
        raw_requirements = raw_groups.get(group, [])
        if not isinstance(raw_requirements, list):
            raise ValueError(
                f"pyproject.toml: dependency-groups.{group} must be an array"
            )
        present = {
            normalized
            for requirement in raw_requirements
            if (normalized := _normalized_requirement(requirement)) is not None
        }
        absent = tuple(package for package in expected if package not in present)
        if absent:
            missing[group] = absent
    return missing


def bootstrap_commands(root: Path) -> tuple[tuple[str, ...], ...]:
    """Build bounded argv to declare, sync, and index the standard toolchain."""
    commands: list[tuple[str, ...]] = []
    if (root / "pyproject.toml").is_file():
        for group, packages in missing_group_packages(root).items():
            commands.append(("uv", "add", "--group", group, "--no-sync", *packages))
        uv_command = ["uv", "sync"]
        for group in MANAGED_GROUPS:
            uv_command.extend(("--group", group))
        commands.append(tuple(uv_command))
    if (root / ".git").exists() and not (root / ".codegraph").exists():
        commands.append(("codegraph", "init", "-i"))
    return tuple(commands)


def _print_doctor(
    root: Path,
    *,
    config_root: Path = FRAMEWORK_ROOT,
    strict_project: bool = True,
) -> int:
    try:
        checks = inspect_machine(config_root)
        groups = project_groups(root) if (root / "pyproject.toml").is_file() else ()
        missing_packages = (
            missing_group_packages(root) if (root / "pyproject.toml").is_file() else {}
        )
    except (OSError, tomllib.TOMLDecodeError, ValueError) as error:
        print(f"DOCTOR_CONFIG_FAILED: {error}", file=sys.stderr)
        return 2
    for check in checks:
        status = "OK" if check.ok else "MISSING"
        print(f"{status}\tmachine\t{check.name}\t{check.detail}")
    if (root / "pyproject.toml").is_file():
        for group in MANAGED_GROUPS:
            status = "OK" if group in groups else "SKIP"
            print(f"{status}\tproject-group\t{group}")
            for package in STANDARD_GROUP_PACKAGES[group]:
                status = (
                    "MISSING" if package in missing_packages.get(group, ()) else "OK"
                )
                print(f"{status}\tproject-package\t{group}:{package}")
    else:
        print("SKIP\tproject\tpyproject.toml")
    index_present = (root / ".codegraph").exists()
    index_required = (root / ".git").exists()
    index_status = "OK" if index_present else "MISSING" if index_required else "SKIP"
    print(f"{index_status}\tproject\t.codegraph")
    machine_ok = all(check.ok for check in checks)
    project_ok = not missing_packages and (index_present or not index_required)
    return 0 if machine_ok and (project_ok or not strict_project) else 2


def _bootstrap(root: Path, *, config_root: Path = FRAMEWORK_ROOT) -> int:
    if _print_doctor(root, config_root=config_root, strict_project=False) != 0:
        print("BOOTSTRAP_BLOCKED: install missing machine tools first", file=sys.stderr)
        return 2
    try:
        commands = bootstrap_commands(root)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as error:
        print(f"BOOTSTRAP_CONFIG_FAILED: {error}", file=sys.stderr)
        return 2
    if not commands:
        print("BOOTSTRAP_OK: nothing to do")
        return 0
    for command in commands:
        print(f"BOOTSTRAP_RUN: {shlex.join(command)}")
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode != 0:
            print(
                f"BOOTSTRAP_FAILED: {command[0]} exit={result.returncode}",
                file=sys.stderr,
            )
            return 2
    print("BOOTSTRAP_OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "bootstrap"))
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.command == "doctor":
        return _print_doctor(root)
    return _bootstrap(root)


if __name__ == "__main__":
    raise SystemExit(main())
