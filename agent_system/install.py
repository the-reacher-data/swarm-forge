"""Plan and apply safe, idempotent global agent installations."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import os
import shutil
import tempfile
import tomllib
from typing import Any

import yaml


class InstallError(RuntimeError):
    """Raised when an installation cannot be applied safely."""


@dataclass(slots=True)
class InstallAction:
    """One planned file installation."""

    source: Path
    target: Path
    status: str
    backup: Path | None = None


@dataclass(frozen=True, slots=True)
class InstallTarget:
    """One configured CLI profile and its canonical source directory."""

    name: str
    source_root: Path
    profile_root: Path
    replace: frozenset[str]


@dataclass(frozen=True, slots=True)
class InstallConfig:
    """Resolved global installation configuration."""

    path: Path
    backup_root: Path
    targets: tuple[InstallTarget, ...]


def _same_content(left: Path, right: Path) -> bool:
    return left.read_bytes() == right.read_bytes()


def plan_install(
    source_root: Path,
    profile_root: Path,
    *,
    replace: Set[str] | frozenset[str] = frozenset(),
) -> list[InstallAction]:
    """Plan an installation into ``profile_root/agents`` without writing."""
    source_root = Path(source_root).resolve(strict=True)
    profile_root = Path(profile_root)
    agents_root = profile_root / "agents"
    resolved_agents_root = agents_root.resolve(strict=False)
    actions: list[InstallAction] = []

    for source in sorted(source_root.rglob("*")):
        if not source.is_file() or source.is_symlink():
            continue
        relative = source.relative_to(source_root)
        target = agents_root / relative
        try:
            target.resolve(strict=False).relative_to(resolved_agents_root)
        except ValueError:
            status = "conflict"
        else:
            replace_allowed = relative.as_posix() in replace
            if target.is_symlink():
                status = "conflict"
            elif not target.exists():
                status = "create"
            elif not target.is_file():
                status = "conflict"
            elif _same_content(source, target):
                status = "unchanged"
            elif replace_allowed:
                status = "replace"
            else:
                status = "conflict"
        actions.append(InstallAction(source=source, target=target, status=status))
    return actions


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def apply_install(actions: Iterable[InstallAction], backup_root: Path) -> None:
    """Apply a conflict-free plan, backing up every replaced regular file."""
    planned = list(actions)
    conflicts = [action.target for action in planned if action.status == "conflict"]
    if conflicts:
        rendered = ", ".join(str(path) for path in conflicts)
        raise InstallError(f"unresolved conflict: {rendered}")

    backup_root = Path(backup_root)
    for action in planned:
        if action.status == "unchanged":
            continue
        if action.status == "replace":
            if action.target.is_symlink() or not action.target.is_file():
                raise InstallError(
                    f"replacement target is not a regular file: {action.target}"
                )
            backup = backup_root / action.target.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                raise InstallError(f"backup already exists: {backup}")
            shutil.copy2(action.target, backup)
            action.backup = backup
        _atomic_copy(action.source, action.target)


def _safe_relative_path(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise InstallError(f"{field} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise InstallError(f"{field} must stay within its declared base")
    return path


def _within(base: Path, relative: Path, field: str) -> Path:
    resolved_base = base.resolve()
    resolved = (resolved_base / relative).resolve(strict=False)
    try:
        resolved.relative_to(resolved_base)
    except ValueError as error:
        raise InstallError(f"{field} resolves outside its declared base") from error
    return resolved


def load_install_config(path: Path, *, home: Path | None = None) -> InstallConfig:
    """Load and resolve a versioned installation config fail-closed."""
    path = Path(path).resolve(strict=True)
    try:
        with path.open("rb") as source:
            document = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise InstallError(f"cannot load install config {path}: {error}") from error
    if document.get("version") != 1:
        raise InstallError("install config version must be 1")
    resolved_home = (home or Path.home()).resolve()
    backup_relative = _safe_relative_path(document.get("backup_path"), "backup_path")
    backup_root = _within(resolved_home, backup_relative, "backup_path")
    raw_targets = document.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise InstallError("install config requires at least one target")

    targets: list[InstallTarget] = []
    seen_names: set[str] = set()
    for index, raw in enumerate(raw_targets):
        if not isinstance(raw, dict):
            raise InstallError(f"target {index} must be a table")
        unknown = sorted(set(raw) - {"name", "source", "profile", "replace"})
        if unknown:
            raise InstallError(
                f"target {index} has unknown fields: {', '.join(unknown)}"
            )
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip() or name in seen_names:
            raise InstallError(f"target {index} requires a unique non-empty name")
        seen_names.add(name)
        source_relative = _safe_relative_path(
            raw.get("source"), f"target {name} source"
        )
        profile_relative = _safe_relative_path(
            raw.get("profile"), f"target {name} profile"
        )
        source_root = _within(path.parent, source_relative, f"target {name} source")
        if not source_root.is_dir():
            raise InstallError(
                f"target {name} source is not a directory: {source_root}"
            )
        profile_root = _within(
            resolved_home, profile_relative, f"target {name} profile"
        )
        raw_replace = raw.get("replace", [])
        if not isinstance(raw_replace, list) or not all(
            isinstance(item, str)
            and item
            and not Path(item).is_absolute()
            and ".." not in Path(item).parts
            for item in raw_replace
        ):
            raise InstallError(f"target {name} replace must contain relative paths")
        targets.append(
            InstallTarget(
                name=name,
                source_root=source_root,
                profile_root=profile_root,
                replace=frozenset(raw_replace),
            )
        )
    return InstallConfig(path=path, backup_root=backup_root, targets=tuple(targets))


def _portable(path: Path | None, *, home: Path, config_dir: Path) -> str | None:
    if path is None:
        return None
    resolved = path.resolve(strict=False)
    for prefix, base in (("home", home.resolve()), ("config", config_dir.resolve())):
        try:
            relative = resolved.relative_to(base)
        except ValueError:
            continue
        return f"{prefix}:{relative.as_posix()}"
    return resolved.as_posix()


def _render_report(
    plans: Sequence[tuple[InstallTarget, list[InstallAction]]],
    *,
    applied: bool,
    home: Path,
    config_dir: Path,
    generated_at: str | None,
) -> str:
    actions = []
    for target, target_actions in plans:
        for action in target_actions:
            actions.append(
                {
                    "backup": _portable(
                        action.backup, home=home, config_dir=config_dir
                    ),
                    "profile": target.name,
                    "source": _portable(
                        action.source, home=home, config_dir=config_dir
                    ),
                    "status": action.status,
                    "target": _portable(
                        action.target, home=home, config_dir=config_dir
                    ),
                }
            )
    report = {
        "actions": actions,
        "applied": applied,
        "generated_at": generated_at,
        "summary": {
            status: sum(action["status"] == status for action in actions)
            for status in ("conflict", "create", "replace", "unchanged")
        },
    }
    return yaml.safe_dump(report, allow_unicode=True, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    config = load_install_config(args.config)
    plans = [
        (
            target,
            plan_install(
                target.source_root,
                target.profile_root,
                replace=target.replace,
            ),
        )
        for target in config.targets
    ]
    has_conflicts = any(
        action.status == "conflict"
        for _, target_actions in plans
        for action in target_actions
    )
    applied = False
    generated_at: str | None = None
    if args.apply and not has_conflicts:
        now = datetime.now(UTC)
        generated_at = now.isoformat()
        backup_run = config.backup_root / now.strftime("%Y%m%dT%H%M%S.%fZ")
        for target, actions in plans:
            apply_install(actions, backup_run / target.name)
        applied = True

    rendered = _render_report(
        plans,
        applied=applied,
        home=Path.home(),
        config_dir=config.path.parent,
        generated_at=generated_at,
    )
    if args.report is None:
        print(rendered, end="")
    else:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    return 2 if has_conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
