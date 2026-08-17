"""Discover agent and skill definitions with explicit source ownership."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

import yaml


PRUNE_NAMES = frozenset(
    {
        ".cache",
        ".git",
        ".local-data",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        ".worktrees",
        "__pycache__",
        "caches",
        "dependencies",
        "dist-packages",
        "node_modules",
        "site-packages",
        "vendor",
        "worktrees",
    }
)
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class InventoryError(RuntimeError):
    """Raised when inventory discovery cannot produce a complete result."""


@dataclass(frozen=True, slots=True, order=True)
class Installation:
    path: Path
    owner: str
    runtime: str


@dataclass(frozen=True, slots=True)
class Artifact:
    kind: str
    name: str
    source: Path
    owner: str | None
    installations: tuple[Installation, ...]

    @property
    def first_party(self) -> bool:
        """Return whether an exact canonical owner was declared for the source."""
        return self.owner is not None

    @property
    def runtime(self) -> str:
        """Compatibility accessor for single-installation consumers."""
        return self.installations[0].runtime


@dataclass(frozen=True, slots=True)
class OwnershipSource:
    path: Path
    kind: str
    name: str
    owner: str


@dataclass(frozen=True, slots=True)
class CatalogRoot:
    path: Path
    owner: str
    runtime: str | None = None


class Catalog(list[CatalogRoot]):
    """Loaded observation roots plus their exact ownership declarations."""

    def __init__(
        self,
        roots: Iterable[CatalogRoot],
        ownership_sources: Iterable[OwnershipSource],
        *,
        home: Path,
        catalog_dir: Path,
    ) -> None:
        super().__init__(roots)
        self.ownership_sources = tuple(ownership_sources)
        self.home = home
        self.catalog_dir = catalog_dir


def _contains_pruned_part(path: Path) -> bool:
    return any(part in PRUNE_NAMES for part in path.parts)


def _iter_files(root: Path) -> Iterator[tuple[Path, Path]]:
    def visit(
        directory: Path,
        ancestors: frozenset[tuple[int, int]],
        resolved_directory: Path | None = None,
    ) -> Iterator[tuple[Path, Path]]:
        try:
            if resolved_directory is None:
                resolved_directory = directory.resolve(strict=True)
            if _contains_pruned_part(resolved_directory):
                return
            stat = resolved_directory.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in ancestors:
                return
            entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
        except (OSError, RuntimeError) as error:
            raise InventoryError(f"cannot traverse {directory}: {error}") from error

        next_ancestors = ancestors | {identity}
        for entry in entries:
            if entry.name in PRUNE_NAMES:
                continue
            try:
                source = entry.resolve(strict=True)
                if source.is_dir():
                    yield from visit(entry, next_ancestors, source)
                elif source.is_file() and not _contains_pruned_part(source):
                    yield entry.absolute(), source
            except InventoryError:
                raise
            except (OSError, RuntimeError) as error:
                raise InventoryError(f"cannot inspect {entry}: {error}") from error

    yield from visit(root, frozenset())


def _artifact_kind(path: Path) -> str | None:
    if path.name == "SKILL.md":
        return "skill"
    if path.suffix == ".md" and (
        path.parent.name == "agents"
        or (path.parent.name == "claude" and path.parent.parent.name == "agents")
    ):
        return "agent"
    if path.suffix == ".toml" and (
        (path.parent.name == "codex" and path.parent.parent.name == "agents")
        or (path.parent.name == "agents" and ".codex" in path.parts)
    ):
        return "agent"
    return None


def _runtime(path: Path, root: Path) -> str:
    parts = path.relative_to(root.resolve()).parts
    if ".claude" in parts or "claude" in parts:
        return "claude"
    if ".codex" in parts or "codex" in parts:
        return "codex"
    return "shared"


def _frontmatter_name(path: Path) -> str | None:
    if path.suffix != ".md":
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise InventoryError(f"cannot read {path}: {error}") from error
    if not text.startswith("---\n"):
        return None
    frontmatter, separator, _ = text.partition("\n---\n")
    if not separator:
        return None
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "name":
            try:
                parsed_name = yaml.safe_load(value.strip())
            except yaml.YAMLError as error:
                raise InventoryError(f"cannot parse name in {path}: {error}") from error
            if isinstance(parsed_name, str):
                return parsed_name
    return None


def _validate_name(name: str, path: Path) -> None:
    if not NAME_PATTERN.fullmatch(name):
        raise InventoryError(f"invalid artifact name {name!r} in {path}")


def _artifact_name(path: Path, kind: str) -> str:
    declared_name = _frontmatter_name(path)
    name = declared_name or (path.parent.name if kind == "skill" else path.stem)
    _validate_name(name, path)
    return name


def discover_artifacts(roots: Sequence[Path]) -> list[Artifact]:
    catalog_roots = [CatalogRoot(Path(root), Path(root).name) for root in roots]
    return _discover_catalog_roots(catalog_roots)


def _discover_catalog_roots(
    roots: Sequence[CatalogRoot],
    *,
    ownership_sources: Sequence[OwnershipSource] | None = None,
) -> list[Artifact]:
    if ownership_sources is None and isinstance(roots, Catalog):
        ownership_sources = roots.ownership_sources
    ownership_sources = ownership_sources or ()
    ownership_by_path: dict[Path, OwnershipSource] = {}
    for source in ownership_sources:
        if source.kind not in {"agent", "skill"}:
            raise InventoryError(
                f"unrecognized ownership kind {source.kind!r} for {source.path}"
            )
        _validate_name(source.name, source.path)
        if not source.owner.strip():
            raise InventoryError(f"empty canonical owner for {source.path}")
        try:
            canonical_path = source.path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise InventoryError(
                f"cannot resolve ownership source {source.path}: {error}"
            ) from error
        ownership_by_path[canonical_path] = source
    if len(ownership_by_path) != len(ownership_sources):
        raise InventoryError("ownership catalog contains duplicate canonical sources")

    discovered: dict[Path, tuple[str, str, set[Installation]]] = {}
    for catalog_root in roots:
        try:
            root = catalog_root.path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise InventoryError(
                f"cannot resolve catalog root {catalog_root.path}: {error}"
            ) from error
        if not root.is_dir():
            raise InventoryError(f"catalog root is not a directory: {root}")
        for location, source in _iter_files(root):
            kind = _artifact_kind(source)
            if kind is None:
                continue
            name = _artifact_name(source, kind)
            installation = Installation(
                path=location,
                owner=catalog_root.owner,
                runtime=catalog_root.runtime or _runtime(location, catalog_root.path),
            )
            current = discovered.get(source)
            if current is None:
                discovered[source] = (kind, name, {installation})
            else:
                current_kind, current_name, installations = current
                if (current_kind, current_name) != (kind, name):
                    raise InventoryError(
                        f"source {source} has conflicting identities: "
                        f"{current_kind}/{current_name} and {kind}/{name}"
                    )
                installations.add(installation)

    unobserved_sources = set(ownership_by_path).difference(discovered)
    if unobserved_sources:
        missing = ", ".join(path.as_posix() for path in sorted(unobserved_sources))
        raise InventoryError(f"declared ownership sources not observed: {missing}")

    artifacts = []
    for source, (kind, name, installations) in discovered.items():
        owned = ownership_by_path.get(source)
        if owned is not None and (owned.kind, owned.name) != (kind, name):
            raise InventoryError(
                f"ownership identity mismatch for {source}: "
                f"catalog has {owned.kind}/{owned.name}, source has {kind}/{name}"
            )
        artifacts.append(
            Artifact(
                kind=kind,
                name=name,
                source=source,
                owner=owned.owner if owned is not None else None,
                installations=tuple(sorted(installations)),
            )
        )
    return sorted(
        artifacts,
        key=lambda artifact: (artifact.source.as_posix(), artifact.kind, artifact.name),
    )


def _portable_path(
    path: Path,
    *,
    home: Path | None,
    catalog_dir: Path | None,
    roots: Sequence[CatalogRoot],
) -> str:
    absolute = path.absolute()
    candidates: list[tuple[str, Path]] = []
    if home is not None:
        candidates.append(("home", home.resolve()))
    if catalog_dir is not None:
        candidates.append(("catalog", catalog_dir.resolve()))
    candidates.extend(
        (f"root-{root.owner}", root.path.expanduser().resolve()) for root in roots
    )
    for token, base in candidates:
        try:
            relative = absolute.relative_to(base)
        except ValueError:
            continue
        return f"{token}:{relative.as_posix()}"
    raise InventoryError(f"path has no portable catalog base: {path}")


def write_inventory(
    artifacts: Iterable[Artifact],
    output: Path,
    *,
    home: Path | None = None,
    catalog_dir: Path | None = None,
    roots: Sequence[CatalogRoot] = (),
) -> None:
    records = []
    for artifact in artifacts:
        if artifact.kind not in {"agent", "skill"}:
            raise InventoryError(
                f"unrecognized artifact kind {artifact.kind!r} for {artifact.source}"
            )
        _validate_name(artifact.name, artifact.source)
        records.append(
            {
                "first_party": artifact.first_party,
                "owner": artifact.owner,
                "installations": [
                    {
                        "owner": installation.owner,
                        "path": _portable_path(
                            installation.path,
                            home=home,
                            catalog_dir=catalog_dir,
                            roots=roots,
                        ),
                        "runtime": installation.runtime,
                    }
                    for installation in artifact.installations
                ],
                "kind": artifact.kind,
                "name": artifact.name,
                "source": _portable_path(
                    artifact.source,
                    home=home,
                    catalog_dir=catalog_dir,
                    roots=roots,
                ),
            }
        )
    records.sort(key=lambda record: (record["source"], record["kind"], record["name"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump({"artifacts": records}, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )


def _resolve_catalog_path(
    raw_path: str, base: str, *, catalog_dir: Path, home: Path
) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        if base == "home":
            raise ValueError("home-relative catalog roots must stay within home")
        raise ValueError(f"{base}-relative catalog paths must not be absolute")
    if base == "home":
        if ".." in path.parts:
            raise ValueError("home-relative catalog roots must stay within home")
        return home / path
    if base == "catalog":
        return catalog_dir / path
    raise ValueError(f"unsupported catalog base: {base}")


def _load_catalog(path: Path, *, home: Path | None = None) -> Catalog:
    try:
        with path.open("rb") as catalog_file:
            data = tomllib.load(catalog_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise InventoryError(f"cannot read catalog {path}: {error}") from error
    resolved_home = (home or Path.home()).resolve()
    catalog_dir = path.parent.resolve()
    roots = []
    for entry in data.get("roots", []):
        root_path = _resolve_catalog_path(
            entry["path"],
            entry.get("base", "catalog"),
            catalog_dir=catalog_dir,
            home=resolved_home,
        )
        roots.append(
            CatalogRoot(
                path=root_path,
                owner=entry["owner"],
                runtime=entry.get("runtime"),
            )
        )
    ownership_sources = []
    ownership = data.get("ownership", {})
    for entry in ownership.get("sources", []):
        source_path = _resolve_catalog_path(
            entry["path"],
            entry.get("base", "catalog"),
            catalog_dir=catalog_dir,
            home=resolved_home,
        )
        ownership_sources.append(
            OwnershipSource(
                path=source_path.resolve(),
                kind=entry["kind"],
                name=entry["name"],
                owner=(entry["owner"] if "owner" in entry else ownership["owner"]),
            )
        )
    return Catalog(
        roots,
        ownership_sources,
        home=resolved_home,
        catalog_dir=catalog_dir,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    catalog = _load_catalog(args.catalog)
    artifacts = _discover_catalog_roots(catalog)
    write_inventory(
        artifacts,
        args.output,
        home=catalog.home,
        catalog_dir=catalog.catalog_dir,
        roots=catalog,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
