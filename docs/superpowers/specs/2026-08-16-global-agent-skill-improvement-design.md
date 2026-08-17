# Global Agent and Skill Improvement Design

## Status

Approved in conversation for specification. Implementation requires review and
approval of this document.

## Problem

CUIMO currently has reusable agents and skills spread across three global
runtime profiles and several `data-platform-*` repositories. Some definitions
are strong domain references, while others mix reusable knowledge, autonomous
responsibility, repository rules, and orchestration. Claude and Codex therefore
do not have one consistent way to discover, invoke, validate, and maintain them.

The system must improve every existing agent and skill with the appropriate
authoring rubric, not only add a new global catalog. It must also decide whether
each artifact should remain an agent, become a skill, be split into both, move
to repository instructions, or be removed as a duplicate.

## Desired outcome

Provide a versioned, testable agent-and-skill architecture that:

- works globally in Codex and both Claude profiles;
- preserves repository-specific expertise and instructions;
- evaluates all global and local agents with `agent-development`;
- evaluates all created or modified skills with `skill-creator` and
  `writing-skills`;
- uses independent, bounded improvement loops with objective exit criteria;
- makes reusable domain knowledge, including Loom engineering, available from
  every relevant repository;
- avoids coupling this configuration to SwarmForge.

## Scope

### Global runtime profiles

- Codex: `~/.codex/agents` and `~/.codex/skills`.
- Claude personal: `~/.aisw/profiles/personal/agents` and `skills`.
- Claude trabajo: `~/.aisw/profiles/trabajo/agents` and `skills`.

### Repository-local inventory

The implementation inventory includes all first-party agent and skill
definitions found under the following repositories. Generated environments,
dependency directories, worktrees, and caches are excluded.

| Repository | Current agent definitions |
|---|---|
| `data-platform-arch` | `ecs-fargate-architect`, `security-reviewer`, `cost-reviewer`, `runbook-writer`, `aws-network-architect`, `infra-orchestrator`, `clickhouse-dba`, `terraform-implementer`, `infra-ci-cd-engineer` |
| `data-platform-ml` | `ml-lifecycle-orchestrator` |
| `data-platform-web` | `streamlit-data-platform-architect` |
| `data-platform-analytics` | `data-science-expert` |
| `data-platform-pipelines` | `loom-expert`, two definitions named `etl-platform-engineer` |
| `data-platform-catalog` | `catalog-platform-engineer`, `catalog-ci-cd-engineer`, `catalog-python-reviewer` |
| `data-platform-mongo-cdc-daemon` | No current local agent definition; its Loom usage is still in scope for skill routing |

All first-party `SKILL.md` files in these repositories are also in scope,
including architecture, release, Streamlit, data science, ETL, data
contracts, Prefect, Loom, cross-repository change, CDC decomposition, and
catalog skills. The implementation must generate a manifest from the filesystem
at execution time so newly discovered first-party definitions are not silently
omitted.

Existing Paseo artifacts are inventoried only so their disposition is explicit.
They are not an execution dependency and must not be selected by the global
workflow.

### Global core roles

The global workflow exposes seven stable responsibilities:

| Role | Responsibility |
|---|---|
| `lead-engineer` | Discover requirements, own the proposal, route skills and specialists, and accept the result. |
| `proposal-critic` | Challenge a proposal independently before implementation. |
| `implementer` | Execute one approved, bounded task. |
| `verifier` | Validate acceptance criteria and evidence independently without modifying production code. |
| `architecture-reviewer` | Review boundaries, contracts, state, dependencies, and compatibility. |
| `security-reviewer` | Review identity, authorization, secrets, isolation, integrity, and abuse paths. |
| `simplicity-reviewer` | Detect avoidable concepts, indirection, abstractions, and blast radius. |

Existing global specialists remain available only when they represent a
distinct responsibility. Their definitions are improved by the same process;
they are not automatically forced into the core workflow.

## Architecture

### Layering and precedence

1. Runtime safety, permissions, and system instructions always win.
2. Repository `AGENTS.md` or `CLAUDE.md` supplies stable local constraints.
3. Repository-local skills and agents supply domain-specific behavior.
4. Global workflow agents and skills supply reusable process and expertise.

Local specialists extend the global system. A local definition must not copy a
global workflow wholesale. A name collision must be resolved explicitly by
renaming, merging, or documenting a deliberate local override.

### Canonical sources and installation

The methodology repository is the canonical source for global definitions:

```text
agents/
  claude/
  codex/
  evals/
skills/
  <global-skill>/
manifests/
  agent-skill-inventory.yaml
```

Global runtime locations use symlinks to these versioned sources. Repository
artifacts remain versioned in their owning repository. Both Claude profiles
must resolve to identical global definitions. Codex receives native TOML agent
definitions; Claude receives native Markdown definitions. Their behavioral
contracts and evaluation cases remain shared.

Installation must be idempotent. Existing non-symlinked files are backed up or
left untouched until their replacement target and diff are explicit. No
repository-local definition is promoted globally merely because it exists in
multiple repositories.

## Agent-versus-skill classification

Every artifact is assigned exactly one disposition after evaluation:

| Disposition | Use when |
|---|---|
| Keep or improve as agent | It performs autonomous multi-step work with a distinct responsibility and a verifiable output. |
| Extract or improve as skill | It provides reusable knowledge, procedure, reference material, or tooling usable by multiple agents. |
| Split into agent and skill | It contains both autonomous responsibility and reusable domain knowledge. |
| Move to repository instructions | It is a stable, mandatory rule unique to one repository. |
| Merge or retire | It duplicates another artifact without a distinct contract. |

An agent prompt must not be used as a storage location for large domain
references. A skill must not impersonate an autonomous owner or verifier.

## Loom decision

The current `loom-expert` is a thin reviewer wrapper around the
`loom-consumer` skill. Loom is directly consumed by at least
`data-platform-pipelines`, `data-platform-catalog`, and
`data-platform-mongo-cdc-daemon`.

The reusable knowledge becomes a global `loom-engineering` skill. It covers:

- public and private API decisions;
- runner, compiler, storage, schema, history, checkpoint, and lineage behavior;
- Polars, Spark, Prefect, ClickHouse, streaming, and compatibility concerns when
  present;
- ownership classification between the consumer, a compatibility adapter, and
  `loom-py`;
- canonical-source consultation and cross-repository validation.

Repository-specific Loom versions, adapters, and required checks remain local.
A separate global `loom-reviewer` agent is retained only if baseline and
forward tests show that autonomous independent review adds value beyond the
global verifier loading `loom-engineering`. Otherwise `loom-expert` is retired
after its useful contract is captured by the skill.

## Improvement methodology

### Agents

Every global and local agent is assessed with `agent-development` for:

- precise identity and responsibility;
- positive and negative triggering conditions;
- representative invocation scenarios;
- least-privilege tools and read-only enforcement for reviewers;
- second-person system prompt with an explicit process;
- quality standards, edge cases, and output contract;
- model inheritance unless a measured requirement justifies an override;
- overlap and routing conflicts with other agents.

Claude files must pass the official structural validator. Codex TOML files must
parse and contain the supported required fields. Shared behavioral contracts
must remain semantically equivalent across runtimes.

### Skills

Every new or modified skill is assessed with `skill-creator` and
`writing-skills`. The process is RED-GREEN-REFACTOR:

1. Define representative trigger, application, variation, and negative cases.
2. Run a clean baseline without the candidate skill and record the observed
   failure or gap.
3. Write the smallest skill that addresses demonstrated failures.
4. Validate structure and discovery metadata.
5. Forward-test with fresh context and no leaked expected answer.
6. Refactor and repeat until the exit criteria pass.

Existing skills are not rewritten cosmetically. If the baseline already meets
the contract, the artifact is retained and the passing evidence is recorded.

## Orchestration loop

`dev-loop` orchestrates engineering work after installation.
The improvement project itself uses a worker/verifier loop:

1. Build the inventory and classify one coherent artifact group.
2. Run baseline evaluations.
3. Have a worker propose the minimal change.
4. Have an independent verifier apply the appropriate rubric and tests.
5. Correct supported failures and repeat.
6. Install only after the canonical artifact passes.

The worker and verifier use fresh, independent runtime-native subagent contexts.
The council uses two additional read-only contexts and aggregates findings by
evidence rather than vote. Each artifact group has a maximum of five
iterations and a maximum elapsed time recorded in the run manifest. Exhausting
either bound produces `BLOCKED` with evidence; it never produces a false `OK`.

## Evaluation contracts

### Agent acceptance

An agent receives `OK` only when all applicable checks pass:

- native format validation;
- two or more positive trigger cases select the agent;
- two or more negative cases do not select it;
- its tools and sandbox match least privilege;
- a representative task follows the required process;
- output conforms to its declared contract;
- it does not claim another agent's ownership;
- Claude and Codex variants are behaviorally equivalent where both exist.

### Skill acceptance

A skill receives `OK` only when:

- the baseline demonstrates a real reusable gap, or an existing skill already
  demonstrably fills it;
- frontmatter, name, and directory validate;
- trigger cases discover it and negative cases avoid it;
- application and edge cases produce the expected decisions or artifacts;
- fresh-context forward tests pass without expected-answer leakage;
- installation resolves correctly in all intended profiles.

### System acceptance

The project receives `OK` only when:

- every in-scope artifact has a recorded disposition and verdict;
- all retained or created artifacts pass their relevant contract;
- both Claude profiles expose the same global catalog;
- Codex loads the global catalog;
- local repository rules and specialists remain discoverable;
- collisions and duplicates have explicit resolutions;
- `loom-engineering` works in each direct Loom consumer;
- deterministic repository gates and the methodology `pre-complete` gate pass;
- unresolved risks and blocked artifacts are reported without being hidden.

## Failure handling and rollback

- Never overwrite an unknown global or local definition without preserving its
  original content and showing the replacement mapping.
- Install in one profile first, validate discovery, then apply the identical
  mapping to the second Claude profile and Codex.
- If an installed artifact fails runtime discovery, restore the prior link or
  file and keep the canonical candidate marked failed.
- Do not modify Loom framework source as part of this project. Findings that
  require `loom-py` changes become separate proposals.
- Repository changes are isolated by repository and verified with that
  repository's own instructions and gates.

## Non-goals

- Redesigning SwarmForge or making it the delivery mechanism.
- Depending on Paseo for loops, councils, agent lifecycle, or installation.
- Rewriting application or infrastructure code unrelated to agent/skill
  discovery and validation.
- Making every specialist global.
- Running every reviewer on every engineering task.
- Treating model consensus or majority vote as evidence.
- Publishing changes or creating pull requests without separate authorization.

## Deliverables

- Versioned global Claude and Codex agent definitions.
- Improved repository-local agent definitions.
- Improved first-party global and local skills.
- Global `loom-engineering` skill and its evaluation suite.
- Artifact inventory with disposition, ownership, installation targets, and
  verdicts.
- Automated structural, trigger, permission, output-contract, and installation
  checks where deterministic automation is possible.
- A concise migration and rollback record for both Claude profiles and Codex.
