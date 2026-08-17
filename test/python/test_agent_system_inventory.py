from pathlib import Path

import pytest
import yaml

from agent_system.inventory import (
    Artifact,
    CatalogRoot,
    Installation,
    InventoryError,
    OwnershipSource,
    _discover_catalog_roots,
    _load_catalog,
    discover_artifacts,
    main,
    write_inventory,
)


CATALOG = Path(__file__).parents[2] / "agents" / "catalog.toml"


def test_ownership_contract_has_one_exact_policy_and_canonical_owner() -> None:
    assert "owner" in OwnershipSource.__dataclass_fields__
    assert "first_party" not in CatalogRoot.__dataclass_fields__
    assert "first_party_skills" not in CatalogRoot.__dataclass_fields__


def test_catalog_owner_is_canonical_not_an_installation_owner(tmp_path: Path) -> None:
    source = tmp_path / "repo" / ".claude" / "agents" / "owned-agent.md"
    _write_agent(source, "owned-agent")
    catalog_path = tmp_path / "catalog.toml"
    catalog_path.write_text(
        """
[ownership]
sources = [
  { path = "repo/.claude/agents/owned-agent.md", kind = "agent", name = "owned-agent", owner = "cuimo" },
]

[[roots]]
path = "repo"
owner = "claude-personal"
runtime = "claude"
""".strip(),
        encoding="utf-8",
    )

    catalog = _load_catalog(catalog_path, home=tmp_path / "home")
    [artifact] = _discover_catalog_roots(catalog)
    output = tmp_path / "inventory.yaml"
    write_inventory(
        [artifact],
        output,
        home=catalog.home,
        catalog_dir=catalog.catalog_dir,
        roots=catalog,
    )

    assert artifact.owner == "cuimo"
    assert artifact.installations[0].owner == "claude-personal"
    assert (
        yaml.safe_load(output.read_text(encoding="utf-8"))["artifacts"][0]["owner"]
        == "cuimo"
    )


def test_existing_declared_source_outside_observation_roots_fails_closed(
    tmp_path: Path,
) -> None:
    observed = tmp_path / "observed"
    observed.mkdir()
    outside = tmp_path / "outside" / "agents" / "owned-agent.md"
    _write_agent(outside, "owned-agent")

    with pytest.raises(InventoryError, match="not observed"):
        _discover_catalog_roots(
            [CatalogRoot(observed, "observed")],
            ownership_sources=(
                OwnershipSource(outside, "agent", "owned-agent", "cuimo"),
            ),
        )


def test_discovery_excludes_generated_and_keeps_first_party(tmp_path: Path) -> None:
    local = tmp_path / "repo" / ".claude" / "agents" / "etl.md"
    generated = tmp_path / "repo" / ".venv" / ".agents" / "skills" / "x" / "SKILL.md"
    local.parent.mkdir(parents=True)
    generated.parent.mkdir(parents=True)
    local.write_text("---\nname: etl\n---\n", encoding="utf-8")
    generated.write_text("---\nname: x\n---\n", encoding="utf-8")

    assert [artifact.name for artifact in discover_artifacts([tmp_path / "repo"])] == [
        "etl"
    ]


def test_discovery_prunes_generated_worktrees_caches_and_dependencies(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    kept = root / ".claude" / "agents" / "etl.md"
    excluded_roots = [
        root / ".git",
        root / ".worktrees",
        root / ".claude" / "worktrees",
        root / ".cache",
        root / ".pytest_cache",
        root / "node_modules",
        root / "vendor",
        root / "site-packages",
        root / "caches",
        root / "dependencies",
    ]
    kept.parent.mkdir(parents=True)
    kept.write_text("---\nname: etl\n---\n", encoding="utf-8")
    for index, excluded_root in enumerate(excluded_roots):
        skill = excluded_root / ".agents" / "skills" / f"excluded-{index}" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(f"---\nname: excluded-{index}\n---\n", encoding="utf-8")

    assert [artifact.name for artifact in discover_artifacts([root])] == ["etl"]


def test_discovery_follows_installed_skill_symlink_once_without_cycles(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "portable-skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: portable-skill\n---\n", encoding="utf-8"
    )
    (source / "cycle").symlink_to(source, target_is_directory=True)
    installed = tmp_path / "home" / ".codex" / "skills"
    installed.mkdir(parents=True)
    (installed / "portable-skill").symlink_to(source, target_is_directory=True)

    artifacts = discover_artifacts([installed])

    assert [(artifact.kind, artifact.name) for artifact in artifacts] == [
        ("skill", "portable-skill")
    ]


def test_discovery_does_not_follow_symlink_into_generated_destination(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "repo" / ".venv" / "skills" / "generated"
    generated.mkdir(parents=True)
    (generated / "SKILL.md").write_text("---\nname: generated\n---\n", encoding="utf-8")
    installed = tmp_path / "home" / ".codex" / "skills"
    installed.mkdir(parents=True)
    (installed / "generated").symlink_to(generated, target_is_directory=True)

    assert discover_artifacts([installed]) == []


def test_discovery_rejects_file_symlink_into_generated_destination(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "repo" / ".venv" / "skills" / "generated" / "SKILL.md"
    generated.parent.mkdir(parents=True)
    generated.write_text("---\nname: generated\n---\n", encoding="utf-8")
    installed = tmp_path / "home" / ".codex" / "skills" / "generated"
    installed.mkdir(parents=True)
    (installed / "SKILL.md").symlink_to(generated)

    assert discover_artifacts([installed.parent]) == []


def test_canonical_first_party_source_precedes_third_party_symlink(
    tmp_path: Path,
) -> None:
    canonical_root = tmp_path / "repo"
    skill = canonical_root / ".agents" / "skills" / "owned" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: owned\n---\n", encoding="utf-8")
    global_skills = tmp_path / "home" / ".codex" / "skills"
    global_skills.mkdir(parents=True)
    (global_skills / "owned").symlink_to(skill.parent, target_is_directory=True)

    artifacts = _discover_catalog_roots(
        [
            CatalogRoot(
                global_skills,
                "codex-global",
            ),
            CatalogRoot(
                canonical_root,
                "owned-repo",
            ),
        ],
        ownership_sources=(
            OwnershipSource(skill.resolve(), "skill", "owned", "cuimo"),
        ),
    )

    assert len(artifacts) == 1
    assert artifacts[0].first_party is True
    assert {item.owner for item in artifacts[0].installations} == {
        "codex-global",
        "owned-repo",
    }


def test_arbitrary_copied_skill_inside_first_party_repo_is_not_owned(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "first-party-repo"
    copied = repo / ".agents" / "skills" / "surprise-vendor-tool" / "SKILL.md"
    copied.parent.mkdir(parents=True)
    copied.write_text("---\nname: surprise-vendor-tool\n---\n", encoding="utf-8")

    [artifact] = _discover_catalog_roots(
        [
            CatalogRoot(
                repo,
                "first-party-repo",
            )
        ]
    )

    assert artifact.first_party is False


def test_allowlisted_skill_linked_from_global_is_owned_by_canonical_source(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "first-party-repo"
    skill = repo / ".agents" / "skills" / "known-cuimo-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: known-cuimo-skill\n---\n", encoding="utf-8")
    installed = tmp_path / "home" / ".codex" / "skills"
    installed.mkdir(parents=True)
    (installed / "known-cuimo-skill").symlink_to(skill.parent, target_is_directory=True)
    roots = [
        CatalogRoot(
            installed,
            "codex-global",
        ),
        CatalogRoot(
            repo,
            "first-party-repo",
        ),
    ]
    ownership = (
        OwnershipSource(skill.resolve(), "skill", "known-cuimo-skill", "cuimo"),
    )

    for ordered_roots in (roots, list(reversed(roots))):
        [artifact] = _discover_catalog_roots(ordered_roots, ownership_sources=ownership)
        assert artifact.source == skill.resolve()
        assert artifact.first_party is True
        assert set(artifact.installations) == {
            Installation(
                installed / "known-cuimo-skill" / "SKILL.md",
                "codex-global",
                "shared",
            ),
            Installation(skill, "first-party-repo", "shared"),
        }


def test_inventory_is_reproducible(tmp_path: Path) -> None:
    home = tmp_path / "home"
    artifacts = [
        Artifact(
            "skill",
            "zeta",
            home / "b",
            "cuimo",
            (Installation(home / "b", "repo-b", "shared"),),
        ),
        Artifact(
            "agent",
            "alpha",
            home / "a",
            "cuimo",
            (Installation(home / "a", "repo-a", "claude"),),
        ),
    ]

    write_inventory(artifacts, tmp_path / "one.yaml", home=home)
    write_inventory(reversed(artifacts), tmp_path / "two.yaml", home=home)

    assert (tmp_path / "one.yaml").read_bytes() == (tmp_path / "two.yaml").read_bytes()


def test_write_inventory_rejects_unrecognized_artifact_kind(tmp_path: Path) -> None:
    source = tmp_path / "workflow.md"
    artifact = Artifact(
        kind="workflow",
        name="workflow",
        source=source,
        owner=None,
        installations=(Installation(source, "repo", "shared"),),
    )

    with pytest.raises(InventoryError, match="unrecognized artifact kind 'workflow'"):
        write_inventory([artifact], tmp_path / "inventory.yaml", home=tmp_path)


def test_discovery_uses_the_declared_frontmatter_name(tmp_path: Path) -> None:
    agent = tmp_path / "repo" / ".claude" / "agents" / "legacy-filename.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "---\nname: canonical-agent\ndescription: Test fixture\n---\nBody\n",
        encoding="utf-8",
    )

    [artifact] = discover_artifacts([tmp_path / "repo"])

    assert artifact.name == "canonical-agent"


def test_discovery_tolerates_invalid_non_name_frontmatter(tmp_path: Path) -> None:
    agent = tmp_path / "repo" / ".claude" / "agents" / "legacy.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "---\nname: canonical-agent\ndescription: Scope: invalid YAML\n---\nBody\n",
        encoding="utf-8",
    )

    [artifact] = discover_artifacts([tmp_path / "repo"])

    assert artifact.name == "canonical-agent"


def test_catalog_toml_is_not_misclassified_as_an_agent(tmp_path: Path) -> None:
    catalog = tmp_path / "repo" / "agents" / "catalog.toml"
    catalog.parent.mkdir(parents=True)
    catalog.write_text('[[roots]]\npath = "/repo"\n', encoding="utf-8")

    assert discover_artifacts([tmp_path / "repo"]) == []


def test_catalog_paths_are_relative_to_the_catalog_file(tmp_path: Path) -> None:
    agent = tmp_path / "repo" / ".claude" / "agents" / "etl.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("---\nname: etl\n---\n", encoding="utf-8")
    catalog = tmp_path / "config" / "catalog.toml"
    catalog.parent.mkdir()
    catalog.write_text(
        '[[roots]]\npath = "../repo"\nowner = "example"\n', encoding="utf-8"
    )
    output = tmp_path / "inventory.yaml"

    assert main(["--catalog", str(catalog), "--output", str(output)]) == 0

    assert (
        yaml.safe_load(output.read_text(encoding="utf-8"))["artifacts"][0]["name"]
        == "etl"
    )


def test_catalog_home_paths_are_resolved_from_supplied_home(tmp_path: Path) -> None:
    catalog = tmp_path / "config" / "catalog.toml"
    catalog.parent.mkdir()
    catalog.write_text(
        """
[[roots]]
base = "home"
path = ".codex/agents"
owner = "codex-global"
""".strip(),
        encoding="utf-8",
    )
    supplied_home = tmp_path / "portable-home"

    [root] = _load_catalog(catalog, home=supplied_home)

    assert root.path == supplied_home / ".codex" / "agents"


def test_repository_catalog_declares_all_global_roots_portably(
    tmp_path: Path,
) -> None:
    supplied_home = tmp_path / "portable-home"

    roots = _load_catalog(CATALOG, home=supplied_home)

    global_paths = {
        root.path.relative_to(supplied_home).as_posix()
        for root in roots
        if root.path.is_relative_to(supplied_home)
    }
    assert global_paths == {
        ".codex/agents",
        ".codex/skills",
        ".aisw/profiles/personal/agents",
        ".aisw/profiles/personal/skills",
        ".aisw/profiles/trabajo/agents",
        ".aisw/profiles/trabajo/skills",
    }


def test_repository_catalog_roots_do_not_encode_ownership_policy(
    tmp_path: Path,
) -> None:
    supplied_home = tmp_path / "portable-home"

    roots = _load_catalog(CATALOG, home=supplied_home)

    global_roots = {
        root.path.relative_to(supplied_home).as_posix()
        for root in roots
        if root.path.is_relative_to(supplied_home)
    }
    assert global_roots == {
        ".codex/agents",
        ".codex/skills",
        ".aisw/profiles/personal/agents",
        ".aisw/profiles/personal/skills",
        ".aisw/profiles/trabajo/agents",
        ".aisw/profiles/trabajo/skills",
    }


def test_repository_catalog_declares_exact_auditable_ownership_sources(
    tmp_path: Path,
) -> None:
    catalog = _load_catalog(CATALOG, home=tmp_path / "portable-home")

    assert catalog.ownership_sources
    assert len({item.path for item in catalog.ownership_sources}) == len(
        catalog.ownership_sources
    )
    assert all(item.kind in {"agent", "skill"} for item in catalog.ownership_sources)


def test_repository_catalog_migrates_all_known_global_cuimo_sources(
    tmp_path: Path,
) -> None:
    supplied_home = tmp_path / "portable-home"
    catalog = _load_catalog(CATALOG, home=supplied_home)
    global_sources = [
        source
        for source in catalog.ownership_sources
        if source.path.is_relative_to(supplied_home)
    ]
    by_kind = {
        kind: [source for source in global_sources if source.kind == kind]
        for kind in ("agent", "skill")
    }

    assert {source.name for source in by_kind["agent"]} == {
        "architecture-reviewer",
        "ci-cd-platform-engineer",
        "code-implementer",
        "data-architecture-engineer",
        "implementer",
        "lead-engineer",
        "performance-reviewer",
        "proposal-critic",
        "security-reviewer",
        "simplicity-reviewer",
        "task-orchestrator",
        "test-writer",
        "verifier",
    }
    assert len(by_kind["agent"]) == 39
    assert {source.name for source in by_kind["skill"]} == {
        "anti-overengineering",
        "codegraph-query",
        "context-packer",
        "dev-cycle",
        "enhanced-dev-cycle",
        "git-workflow",
        "loom-kernel",
        "plan-builder",
        "python-backend",
        "tdd-workflow",
    }
    assert len(by_kind["skill"]) == 29
    assert {source.owner for source in global_sources} == {"cuimo"}


@pytest.mark.parametrize(
    ("kind", "name", "message"),
    [
        ("workflow", "owned-agent", "unrecognized ownership kind"),
        ("agent", "../owned-agent", "invalid artifact name"),
    ],
)
def test_declared_ownership_identity_must_be_recognized(
    tmp_path: Path, kind: str, name: str, message: str
) -> None:
    source = tmp_path / "repo" / "agents" / "owned-agent.md"
    _write_agent(source, "owned-agent")

    with pytest.raises(InventoryError, match=message):
        _discover_catalog_roots(
            [CatalogRoot(tmp_path / "repo", "repo")],
            ownership_sources=(OwnershipSource(source, kind, name, "cuimo"),),
        )


def test_home_relative_catalog_roots_cannot_escape_home(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.toml"
    supplied_home = tmp_path / "portable-home"

    for unsafe_path in ("../outside", "/absolute/outside"):
        catalog.write_text(
            f"""
[[roots]]
base = "home"
path = "{unsafe_path}"
owner = "unsafe"
""".strip(),
            encoding="utf-8",
        )

        try:
            _load_catalog(catalog, home=supplied_home)
        except ValueError as error:
            assert str(error) == "home-relative catalog roots must stay within home"
        else:
            raise AssertionError(f"unsafe home-relative root accepted: {unsafe_path}")


def _write_agent(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\n---\n", encoding="utf-8")


def _write_skill(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\n---\n", encoding="utf-8")


def test_external_agent_symlink_and_copy_remain_third_party_in_global_and_repo(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external-source" / "agents" / "external-agent.md"
    _write_agent(external, "external-agent")
    global_agents = tmp_path / "home" / ".codex" / "agents"
    global_agents.mkdir(parents=True)
    (global_agents / "external-agent.md").symlink_to(external)
    repo_copy = tmp_path / "repo" / ".claude" / "agents" / "external-agent.md"
    _write_agent(repo_copy, "external-agent")

    artifacts = _discover_catalog_roots(
        [
            CatalogRoot(global_agents, "codex-global", runtime="codex"),
            CatalogRoot(tmp_path / "repo", "owned-repo", runtime="claude"),
        ],
        ownership_sources=(),
    )

    assert len(artifacts) == 2
    assert all(not artifact.first_party for artifact in artifacts)


def test_skill_cannot_spoof_an_owned_name_from_a_different_source(
    tmp_path: Path,
) -> None:
    owned = tmp_path / "canonical" / "skills" / "owned-skill" / "SKILL.md"
    spoof = tmp_path / "repo" / ".agents" / "skills" / "owned-skill" / "SKILL.md"
    _write_skill(owned, "owned-skill")
    _write_skill(spoof, "owned-skill")

    artifacts = _discover_catalog_roots(
        [
            CatalogRoot(tmp_path / "repo", "repo"),
            CatalogRoot(tmp_path / "canonical", "canonical"),
        ],
        ownership_sources=(
            OwnershipSource(owned.resolve(), "skill", "owned-skill", "cuimo"),
        ),
    )
    artifact = next(item for item in artifacts if item.source == spoof.resolve())

    assert artifact.source == spoof.resolve()
    assert artifact.first_party is False


def test_exact_declared_source_is_first_party(tmp_path: Path) -> None:
    source = tmp_path / "repo" / ".claude" / "agents" / "owned-agent.md"
    _write_agent(source, "owned-agent")

    [artifact] = _discover_catalog_roots(
        [CatalogRoot(tmp_path / "repo", "repo")],
        ownership_sources=(
            OwnershipSource(source.resolve(), "agent", "owned-agent", "cuimo"),
        ),
    )

    assert artifact.first_party is True


def test_three_installations_of_one_source_are_preserved(tmp_path: Path) -> None:
    source = tmp_path / "catalog" / "skills" / "shared-skill" / "SKILL.md"
    _write_skill(source, "shared-skill")
    codex = tmp_path / "home" / ".codex" / "skills"
    personal = tmp_path / "home" / ".aisw" / "profiles" / "personal" / "skills"
    trabajo = tmp_path / "home" / ".aisw" / "profiles" / "trabajo" / "skills"
    for directory in (codex, personal, trabajo):
        directory.mkdir(parents=True)
        (directory / "shared-skill").symlink_to(source.parent, target_is_directory=True)

    [artifact] = _discover_catalog_roots(
        [
            CatalogRoot(codex, "codex-global", runtime="codex"),
            CatalogRoot(personal, "claude-personal", runtime="claude"),
            CatalogRoot(trabajo, "claude-trabajo", runtime="claude"),
        ],
        ownership_sources=(
            OwnershipSource(source.resolve(), "skill", "shared-skill", "cuimo"),
        ),
    )

    assert artifact.first_party is True
    assert {(item.owner, item.runtime) for item in artifact.installations} == {
        ("codex-global", "codex"),
        ("claude-personal", "claude"),
        ("claude-trabajo", "claude"),
    }


def test_read_error_aborts_discovery_instead_of_omitting_artifact(
    tmp_path: Path,
) -> None:
    agent = tmp_path / "repo" / ".claude" / "agents" / "broken.md"
    agent.parent.mkdir(parents=True)
    agent.write_bytes(b"---\nname: \xff\n---\n")

    try:
        _discover_catalog_roots([CatalogRoot(tmp_path / "repo", "repo")])
    except InventoryError as error:
        assert "broken.md" in str(error)
    else:
        raise AssertionError("read error was silently omitted")


def test_manifest_contains_only_portable_paths(tmp_path: Path) -> None:
    home = tmp_path / "portable-home"
    source = home / "src" / "agent.md"
    artifact = Artifact(
        kind="agent",
        name="portable-agent",
        source=source,
        owner="cuimo",
        installations=(Installation(source, "owner", "claude"),),
    )
    output = tmp_path / "inventory.yaml"

    write_inventory([artifact], output, home=home)

    rendered = output.read_text(encoding="utf-8")
    assert str(tmp_path) not in rendered
    assert "home:src/agent.md" in rendered


def test_discovery_result_is_independent_of_root_order(tmp_path: Path) -> None:
    source = tmp_path / "source" / "skills" / "ordered" / "SKILL.md"
    _write_skill(source, "ordered")
    one = tmp_path / "one" / "skills"
    two = tmp_path / "two" / "skills"
    for root in (one, two):
        root.mkdir(parents=True)
        (root / "ordered").symlink_to(source.parent, target_is_directory=True)
    roots = [CatalogRoot(one, "one"), CatalogRoot(two, "two")]
    ownership = (OwnershipSource(source.resolve(), "skill", "ordered", "cuimo"),)

    assert _discover_catalog_roots(
        roots, ownership_sources=ownership
    ) == _discover_catalog_roots(list(reversed(roots)), ownership_sources=ownership)


def test_invalid_declared_name_fails_explicitly(tmp_path: Path) -> None:
    agent = tmp_path / "repo" / ".claude" / "agents" / "unsafe.md"
    _write_agent(agent, "../unsafe")

    try:
        _discover_catalog_roots([CatalogRoot(tmp_path / "repo", "repo")])
    except InventoryError as error:
        assert "invalid artifact name" in str(error)
    else:
        raise AssertionError("invalid name was accepted")


def test_missing_declared_ownership_source_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    missing = root / ".claude" / "agents" / "missing.md"

    try:
        _discover_catalog_roots(
            [CatalogRoot(root, "repo")],
            ownership_sources=(OwnershipSource(missing, "agent", "missing", "cuimo"),),
        )
    except InventoryError as error:
        assert "ownership source" in str(error)
    else:
        raise AssertionError("missing ownership source was silently accepted")
