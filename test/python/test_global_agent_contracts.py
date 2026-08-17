from __future__ import annotations

from pathlib import Path
import tomllib

import yaml

from agent_system.evaluations import CORE_AGENT_NAMES


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills/dev-loop/SKILL.md"
CATALOG = ROOT / "agents/catalog.toml"


def _frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    _, raw, _ = text.split("---", 2)
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed


def test_dev_loop_is_the_only_canonical_workflow_skill() -> None:
    assert SKILL.is_file()
    assert not (ROOT / "skills/cuimo-engineering-workflow").exists()
    metadata = _frontmatter(SKILL)
    assert metadata["name"] == "dev-loop"
    assert str(metadata["description"]).startswith("Use when ")


def test_dev_loop_routes_every_native_core_role_without_legacy_council() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert all(f"`{role}`" in text for role in CORE_AGENT_NAMES)
    assert "council-swarm" not in text
    assert "cuimo-engineering-workflow" not in text


def test_catalog_owns_only_the_new_workflow_name() -> None:
    with CATALOG.open("rb") as source:
        document = tomllib.load(source)
    skills = {
        entry["name"]
        for entry in document["ownership"]["sources"]
        if entry["kind"] == "skill"
    }
    assert "dev-loop" in skills
    assert "cuimo-engineering-workflow" not in skills
