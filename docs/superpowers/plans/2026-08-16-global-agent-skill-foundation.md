# Global Agent and Skill Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox syntax for tracking.

**Goal:** Build the deterministic catalog, validators, canonical global agents,
and idempotent installation layer required to improve every global and
repository-local agent and skill.

**Architecture:** Keep canonical cross-runtime contracts in this repository,
render native Claude Markdown and Codex TOML definitions from those contracts,
and validate both against shared evaluation cases. Discover local artifacts
without changing them, record their disposition in a generated manifest, and
install only validated global artifacts through explicit symlinks.

**Tech Stack:** Python 3.11+, pytest, stdlib tomllib, PyYAML, Claude agent
Markdown, Codex agent TOML, symlinks, and the existing deterministic gate.

**Spec:** docs/superpowers/specs/2026-08-16-global-agent-skill-improvement-design.md

## Global Constraints

- This configures Codex and Claude directly; it must not depend on SwarmForge.
- Both Claude profiles must resolve to identical global definitions.
- Repository-local agents and skills remain owned by their repositories.
- Reviewers and verifiers are read-only and use least privilege.
- Every artifact receives a recorded disposition and evidence-backed verdict.
- New or modified skills require RED-GREEN-REFACTOR forward testing.
- Loops stop after five iterations and report BLOCKED; exhaustion never becomes OK.
- Unknown existing global files are never overwritten without an explicit backup.

---

### Task 1: First-party artifact inventory

**Files:**

- Create: agent_system/__init__.py
- Create: agent_system/inventory.py
- Create: agents/catalog.toml
- Create: test/python/test_agent_system_inventory.py
- Generate: manifests/agent-skill-inventory.yaml

**Interfaces:**

- Artifact(kind, name, source, owner, runtime, first_party).
- discover_artifacts(roots: Sequence[Path]) -> list[Artifact].
- write_inventory(artifacts: Sequence[Artifact], output: Path) -> None.
- Exclude .git, .venv, .worktrees, .claude/worktrees, caches, and dependencies.

- [ ] **Step 1: Write the failing inventory tests**

    def test_discovery_excludes_generated_and_keeps_first_party(tmp_path):
        local = tmp_path / "repo" / ".claude" / "agents" / "etl.md"
        generated = tmp_path / "repo" / ".venv" / ".agents" / "skills" / "x" / "SKILL.md"
        local.parent.mkdir(parents=True)
        generated.parent.mkdir(parents=True)
        local.write_text("---\nname: etl\n---\n")
        generated.write_text("---\nname: x\n---\n")
        assert [a.name for a in discover_artifacts([tmp_path / "repo"])] == ["etl"]

    def test_inventory_is_reproducible(tmp_path):
        artifacts = [
            Artifact("skill", "zeta", Path("/b"), "repo-b", "shared", True),
            Artifact("agent", "alpha", Path("/a"), "repo-a", "claude", True),
        ]
        write_inventory(artifacts, tmp_path / "one.yaml")
        write_inventory(reversed(artifacts), tmp_path / "two.yaml")
        assert (tmp_path / "one.yaml").read_bytes() == (tmp_path / "two.yaml").read_bytes()

- [ ] **Step 2: Run python3 -m pytest -q test/python/test_agent_system_inventory.py**

Expected: FAIL because agent_system.inventory does not exist.

- [ ] **Step 3: Implement immutable discovery and deterministic YAML**

Use a frozen dataclass, sorted POSIX paths, explicit prune names, and
yaml.safe_dump with sort_keys=True and allow_unicode=True. Read roots and owners
from agents/catalog.toml; do not hard-code user home paths in the module.

- [ ] **Step 4: Generate the initial inventory**

Run:

    python3 -m agent_system.inventory --catalog agents/catalog.toml \
      --output manifests/agent-skill-inventory.yaml

Expected: global definitions plus all first-party artifacts from the seven
data-platform repositories, with generated environments absent.

- [ ] **Step 5: Verify and commit**

Run the inventory test. Add only the five Task 1 paths and commit:

    feat(agents): add first-party artifact inventory

### Task 2: Native format and permission validators

**Files:**

- Create: agent_system/validation.py
- Create: test/python/test_agent_system_validation.py
- Generate: manifests/baseline-validation.yaml

**Interfaces:**

- Finding(code: str, path: Path, message: str).
- validate_claude_agent(path: Path) -> list[Finding].
- validate_codex_agent(path: Path) -> list[Finding].
- validate_skill(path: Path) -> list[Finding].
- CLI exits 0 without findings and 1 with findings.

- [ ] **Step 1: Write failing permission tests**

    def test_read_only_claude_reviewer_rejects_write_tool(tmp_path):
        path = tmp_path / "security-reviewer.md"
        path.write_text(
            "---\nname: security-reviewer\n"
            "description: Use this agent when reviewing security. Typical triggers "
            "include auth review and secret review. See \"When to invoke\" in the body.\n"
            "model: inherit\ncolor: red\ntools: [Read, Write, Grep]\n---\n"
            "You are a reviewer.\n## When to invoke\n- **Auth.** Review auth.\n"
        )
        assert [f.code for f in validate_claude_agent(path)] == [
            "read-only-agent-has-write-tool"
        ]

    def test_codex_reviewer_requires_read_only_sandbox(tmp_path):
        path = tmp_path / "verifier.toml"
        path.write_text(
            'name = "verifier"\ndescription = "Verify evidence"\n'
            'developer_instructions = "Remain read-only."\n'
        )
        assert "missing-read-only-sandbox" in {
            f.code for f in validate_codex_agent(path)
        }

- [ ] **Step 2: Verify the tests fail**

Run python3 -m pytest -q test/python/test_agent_system_validation.py.

- [ ] **Step 3: Implement validators**

Claude validation enforces name, detailed trigger description, model, color,
identifier, When to invoke, process, quality, output, and edge cases. Codex
validation enforces valid TOML, name, description, developer_instructions, and
sandbox_mode = "read-only" for critics, reviewers, and verifiers. Skill
validation invokes the official quick_validate.py and checks discovery metadata.

- [ ] **Step 4: Record the RED baseline**

Run validation against the inventory and write
manifests/baseline-validation.yaml. A non-zero result is expected and retained
as evidence.

- [ ] **Step 5: Verify unit tests and commit**

Commit:

    feat(agents): validate native definitions and permissions

### Task 3: Shared evaluation contracts

**Files:**

- Create: agents/evals/core-agents.yaml
- Create: agents/evals/skill-contract.yaml
- Create: agents/evals/equivalence.yaml
- Create: agent_system/evaluations.py
- Create: test/python/test_agent_system_evaluations.py

**Interfaces:**

- EvaluationCase(id, target, kind, prompt, expected, forbidden).
- load_cases(path: Path) -> list[EvaluationCase].
- evaluate_static_contract(case, artifact) -> list[Finding].
- compare_native_variants(claude_dir, codex_dir, mapping) -> list[Finding].
- Every core agent has at least two positive and two negative trigger cases.

- [ ] **Step 1: Write the failing completeness test**

    def test_every_core_agent_has_positive_and_negative_cases():
        cases = load_cases(Path("agents/evals/core-agents.yaml"))
        for target in CORE_AGENT_NAMES:
            selected = [case for case in cases if case.target == target]
            assert sum(case.expected == "invoke" for case in selected) >= 2
            assert sum(case.expected == "do-not-invoke" for case in selected) >= 2

- [ ] **Step 2: Verify the test fails because cases and loader are absent**

- [ ] **Step 3: Add strict cases**

Positive prompts cover ambiguity, proposal challenge, bounded implementation,
independent verification, architecture, security, and overengineering. Negative
prompts deliberately overlap nearby roles. Skill cases encode baseline,
discovery, application, variation, and negative phases. Equivalence cases name
shared role boundaries and required output fields.

- [ ] **Step 4: Verify and commit**

Run the evaluation test and commit:

    test(agents): define shared evaluation contracts

### Task 4: Canonical Claude core agents

**Files:**

- Create: agents/claude/lead-engineer.md
- Create: agents/claude/proposal-critic.md
- Create: agents/claude/implementer.md
- Create: agents/claude/verifier.md
- Create: agents/claude/architecture-reviewer.md
- Create: agents/claude/security-reviewer.md
- Create: agents/claude/simplicity-reviewer.md
- Generate: manifests/evaluations/global-claude-baseline.yaml

**Interfaces:**

- Every file uses model: inherit.
- Read-only roles use Read, Grep, and Glob only.
- Every body contains When to invoke, Core responsibilities, Process, Quality
  standards, Output format, and Edge cases.
- Output contracts match roles-and-contracts.md.
- Colors are lead blue, critic yellow, implementer green, verifier cyan,
  architecture blue, security red, and simplicity magenta.

- [ ] **Step 1: Run core cases against current global Claude agents**

Record missing definitions and bad triggers in the baseline report.

- [ ] **Step 2: Write the seven minimal definitions**

Descriptions contain two to four concrete trigger scenarios and a pointer to
When to invoke. Bodies use second person. Critics and reviewers cannot implement
or silently alter scope.

- [ ] **Step 3: Validate structure and static cases**

Run:

    python3 -m agent_system.validation --paths agents/claude
    python3 -m agent_system.evaluations --cases agents/evals/core-agents.yaml \
      --artifacts agents/claude --static

Expected: both exit 0.

- [ ] **Step 4: Commit**

Commit:

    feat(agents): add canonical Claude core roles

### Task 5: Behaviorally equivalent Codex core agents

**Files:**

- Create: agents/codex/lead-engineer.toml
- Create: agents/codex/proposal-critic.toml
- Create: agents/codex/implementer.toml
- Create: agents/codex/verifier.toml
- Create: agents/codex/architecture-reviewer.toml
- Create: agents/codex/security-reviewer.toml
- Create: agents/codex/simplicity-reviewer.toml
- Modify: agents/evals/equivalence.yaml
- Modify: manifests/agent-skill-inventory.yaml
- Modify: test/python/test_agent_system_evaluations.py

**Interfaces:**

- Every TOML defines name, description, and developer_instructions.
- Critic, verifier, and reviewers define sandbox_mode = "read-only".
- Model is omitted to inherit the parent model.
- Claude and Codex variants share responsibilities, trigger meaning, output
  fields, and permission boundaries.

- [ ] **Step 1: Add a failing native-equivalence test**

    def test_native_variants_share_role_invariants():
        assert compare_native_variants(
            Path("agents/claude"),
            Path("agents/codex"),
            Path("agents/evals/equivalence.yaml"),
        ) == []

- [ ] **Step 2: Verify it fails because Codex variants are absent**

- [ ] **Step 3: Write all seven Codex definitions and equivalence mappings**

Translate behavioral contracts, not Markdown formatting.

- [ ] **Step 4: Validate, test, and commit**

Commit:

    feat(agents): add equivalent Codex core roles

### Task 6: Idempotent global installer

**Files:**

- Create: agent_system/install.py
- Create: agents/install-targets.toml
- Create: test/python/test_agent_system_install.py

**Interfaces:**

- InstallAction(source, target, status, backup).
- plan_install(source_root: Path, profile_root: Path) -> list[InstallAction].
- apply_install(actions, backup_root: Path) -> None.
- Default is dry-run; mutation requires --apply.
- Re-running an identical installation yields only unchanged actions.

- [ ] **Step 1: Write failing conflict and idempotence tests**

    def test_unknown_file_is_not_overwritten(tmp_path):
        source = tmp_path / "source" / "verifier.md"
        target = tmp_path / "profile" / "agents" / "verifier.md"
        source.parent.mkdir()
        target.parent.mkdir(parents=True)
        source.write_text("new")
        target.write_text("owned by user")
        actions = plan_install(source.parent, tmp_path / "profile")
        assert actions[0].status == "conflict"
        assert target.read_text() == "owned by user"

- [ ] **Step 2: Verify tests fail**

- [ ] **Step 3: Implement safe installation**

Declare Codex, Claude personal, and Claude trabajo targets. Resolve sources and
targets before mutation. Reject paths outside the declared profile. Backups use
an ISO timestamp and a manifest. Never delete stale files automatically.

- [ ] **Step 4: Verify and commit**

Commit:

    feat(agents): add safe global profile installer

### Task 7: Align workflow contracts and documentation

**Files:**

- Modify: skills/cuimo-engineering-workflow/SKILL.md
- Modify: skills/cuimo-engineering-workflow/references/roles-and-contracts.md
- Modify: agent_architecture_skills_blueprint.md
- Create: agents/README.md
- Create: test/python/test_global_agent_contracts.py

**Interfaces:**

- The workflow reads repository instructions and discovers local specialists
  before routing.
- Agent names match the seven canonical files.
- Missing optional specialists degrade to the core workflow.
- Documentation separates global process, reusable skill, and local rule.

- [ ] **Step 1: Write the failing name-consistency test**

    def test_workflow_role_names_match_native_catalogs():
        expected = set(CORE_AGENT_NAMES)
        assert claude_agent_names(Path("agents/claude")) == expected
        assert codex_agent_names(Path("agents/codex")) == expected
        assert workflow_role_names(
            Path("skills/cuimo-engineering-workflow/references/roles-and-contracts.md")
        ) == expected

- [ ] **Step 2: Verify current naming drift is detected**

- [ ] **Step 3: Align the workflow and blueprint**

Use lead-engineer and implementer consistently. Add local discovery and
agent-versus-skill routing. Keep SwarmForge explicitly out of scope.

- [ ] **Step 4: Validate and commit**

Run quick_validate.py for cuimo-engineering-workflow and the contract test.
Commit:

    docs(agents): align workflow contracts and routing

### Task 8: Independent loop, installation, and evidence-derived follow-ups

**Files:**

- Create: manifests/evaluations/global-core-final.yaml
- Create: manifests/install/global-install.yaml
- Create: docs/superpowers/plans/2026-08-16-loom-engineering.md
- Create: docs/superpowers/plans/2026-08-16-repository-agent-skill-upgrades.md

**Interfaces:**

- Worker and verifier use independent contexts and different providers when available.
- Exit requires all core structural, trigger, permission, output, equivalence,
  and installation cases to pass, or BLOCKED after five iterations.
- Follow-up plans use the final inventory and baseline reports and name exact
  repository files and gates.

- [ ] **Step 1: Run a five-iteration Paseo improvement loop**

Give the worker the spec, plan, raw artifacts, and failing cases. Give the
verifier artifacts and cases but not the worker's private reasoning or intended
fixes.

- [ ] **Step 2: Run all foundation tests**

    python3 -m pytest -q \
      test/python/test_agent_system_inventory.py \
      test/python/test_agent_system_validation.py \
      test/python/test_agent_system_evaluations.py \
      test/python/test_agent_system_install.py \
      test/python/test_global_agent_contracts.py

Expected: PASS.

- [ ] **Step 3: Dry-run and apply global installation**

First run:

    python3 -m agent_system.install --config agents/install-targets.toml

Resolve every conflict explicitly. Then apply and record actions and backups in
manifests/install/global-install.yaml.

- [ ] **Step 4: Verify installed targets and repository gate**

Validate the resolved symlinks for both Claude profiles and Codex. Run:

    python3 swarmforge/gates/gate.py --pre-complete

Expected: all commands exit 0; successful gate output stays out of model context.

- [ ] **Step 5: Write evidence-derived Loom and repository plans**

The Loom plan includes RED baselines in pipelines, catalog, and CDC before
creating loom-engineering. The repository plan enumerates every retained local
artifact, disposition, exact edits, native equivalents, and owning repository
gate. Neither plan may contain unresolved placeholders.

- [ ] **Step 6: Commit evidence and follow-up plans**

Commit:

    chore(agents): record global catalog verification
