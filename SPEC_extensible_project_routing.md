# SPEC: Extensible Project Routing

Status: handoff to coder. TDD: write failing tests first (`test/python/`).
Use CodeGraph for symbol/impact lookups before editing.

## Goal

Repos declare their own lazy specialist agents in
`swarmforge/project-agents.toml` and the risk router recommends them —
safely, bounded, data-only — without hardcoding roles in
`swarmforge/gates/risk.py`. The validated recommendation travels from the
coder worktree to the planner, which consumes it instead of re-deriving it.

## Non-goals

- No universal registration of `data-engineer` / `ui-reviewer`; they ship as
  templates only.
- No executable configuration: routing entries never carry commands, shell,
  or interpolatable strings.
- No new I/O in `route_manifest` (stays pure, no side effects).

## 1. Declarative routing in `project-agents.toml`

Extend each `[agents.<name>]` entry with an optional `[agents.<name>.routing]`
table:

```toml
[agents.data-engineer]
role = "data-engineer"
backend_instance = "claude-trabajo"
mode = "lazy"
prompt = "swarmforge/roles/data-engineer.prompt"
tags = ["data", "etl"]

[agents.data-engineer.routing]
paths = ["dbt/**", "pipelines/**", "*.sql"]
signals = ["size", "public-api"]
priority = 10
mandatory = false
```

Semantics:

- `paths`: glob list matched case-insensitively against manifest paths
  (same `fnmatchcase(path.lower(), pattern.lower())` family as
  `_matches_any`, `swarmforge/gates/risk.py:147`).
- `signals`: subset of the existing `SIGNALS` enum
  (`swarmforge/gates/risk.py:17`: `lockfile`, `concurrency`, `public-api`,
  `size`). Unknown signal names are a validation error — never silently
  matched.
- `priority`: int; lower value = earlier in reviewer order. Ties break by
  agent name for determinism.
- `mandatory`: bool; a matching mandatory agent is always included before
  optional ones (mandatory first, then by priority, then name).
- Matching rule: agent matches when any declared path glob matches OR any
  declared signal fired. Absent `routing` table = never recommended
  (backward compatible).
- Keys are a closed set: `paths`, `signals`, `priority`, `mandatory`.
  Unknown keys are a validation error. No key may contain commands; values
  are data only (strings/ints/bools/lists thereof), validated by type.

## 2. Registry validation (trust boundary)

Add a loader/validator (suggested: `swarmforge/gates/registry.py`, pure
functions, no subprocess) that parses `project-agents.toml` and rejects:

- `prompt` not confined to the repo: must be a relative path, normalized,
  staying under the repo root (reject absolute paths, `..` traversal,
  symlink-style escapes by lexical normalization). It must point under
  `swarmforge/roles/`.
- `backend_instance` not present in the authorized set derived from
  `swarmforge/backends.toml` (the configured instances are authoritative;
  never invent or fall back).
- `mode` other than the authorized lazy window values already accepted by
  the swarm runtime (today: `lazy`; keep the check table-driven so an
  authorized window list can grow).
- Malformed `routing` per section 1.

Validation failures for a single agent exclude that agent and surface a
bounded reason string; they must not crash the gate (gate route generation
stays best-effort per `write_route_artifacts`,
`swarmforge/gates/gate.py:291`).

## 3. `route.json` output extension

`route_manifest` (`swarmforge/gates/risk.py:162`) keeps its current fields
and behavior (`schema_version`, `route`, `score`, `reasons`, `commit`) and
adds:

- `reviewers`: ordered list of agent names.
  - Order: mandatory first, then ascending `priority`, then name.
  - Deduplicated (an agent appears once even if multiple globs/signals hit).
  - Hard cap: max 2 entries; overflow is dropped after ordering, and a
    bounded reason (e.g. `reviewers:truncated`) is added to `reasons`.
  - Only agents registered in `project-agents.toml` and passing section-2
    validation may appear. Never the primary `route` value duplicated as a
    reviewer.
- Bump `SCHEMA_VERSION`.
- The registered-agent set is passed in as data (e.g. a
  `routing_agents: Mapping[str, ...]` parameter with a default of empty) so
  `route_manifest` stays I/O-free; `write_route_artifacts` loads the
  registry and injects it.
- Must support a repo registering `data-engineer` + `ui-reviewer` and both
  appearing ordered in `reviewers`.

## 4. Transport worktree → planner

- `write_route_artifacts` already writes
  `.swarmforge/artifacts/route/route.json` in the worktree. Extend the
  handoff flow so the validated recommendation reaches the planner bounded:
  the handoff draft references the commit; the planner-side consumption
  reads the artifact for that commit (or the artifact content is copied
  into `.swarmforge/` runtime state by the existing handoff scripts —
  follow the existing `swarmforge/scripts/` handoff conventions, do not
  invent a new channel).
- The transported payload is exactly the validated `route.json` (bounded:
  reviewers ≤ 2, reasons are enum-like strings, no free text, no paths
  beyond `max_paths` policy). Re-validate on the planner side before use:
  unknown reviewer names are dropped, malformed payload is ignored (planner
  falls back to routing without recommendation).
- Update `swarmforge/roles/planner.prompt`: instruct the planner to read
  the validated route recommendation when present, treat it as advisory
  input bounded by `project-agents.toml` (the trust boundary), and never
  start a lazy specialist merely because it appears in `reviewers` —
  specialists start only when the planner routes real unresolved risk.

## 5. New role prompts and templates

Under `swarmforge/roles/` add general lazy role prompts (concise, same
style as existing ones):

- `specifier.prompt` — turns vague tasks into bounded specs/acceptance
  criteria.
- `hardender.prompt` — hardening reviewer: mutation-testing gaps, CRAP
  score hotspots, DRY violations; analyzes, does not implement.
- `qa.prompt` — exploratory/regression QA on the delivered change.

Under a template location that is NOT the live registry (suggested:
`swarmforge/roles/templates/`): `data-engineer.prompt` and
`ui-reviewer.prompt` templates. Do NOT add any of these to
`swarmforge/project-agents.toml` in this repo — repos opt in individually.
Document in a short comment in `project-agents.toml` how to register one.

## 6. Bug fix: `tests/` directory

`_tests_docs_only` (`swarmforge/gates/risk.py:155`) treats only `test/` and
`docs/` as low-risk. Add `tests/` so repos with the standard pytest layout
get the same routing.

## 7. TDD — required failing tests first (`test/python/`)

Extend `test_risk.py` (+ new `test_registry.py` if section 2 lands in its
own module; also cover transport in `test_gate.py`):

1. Trust: reviewer recommendation includes only agents registered and
   validated; an agent with unauthorized backend/prompt-escape/mode is
   excluded and never appears in `reviewers`.
2. Unknowns: unknown `signals` value or unknown routing key → agent
   rejected with bounded reason; router output remains valid.
3. Priority/limit: mandatory-first then priority-then-name ordering;
   dedupe; >2 matches truncate to 2 with `reviewers:truncated` reason.
4. Transport E2E: gate run writes `route.json` with `reviewers`; the
   planner-side consumption path reads and re-validates it for the same
   commit (unknown reviewer dropped, malformed payload ignored).
5. Backward compatibility: registry without any `routing` tables (current
   file) yields `reviewers: []` and byte-stable prior behavior for `route`,
   `score`, `reasons`.
6. Lazy not started: producing a recommendation performs no process start,
   no specialist prompt load — assert no subprocess/backends invocation in
   the routing path.
7. `tests/`-layout change is covered: paths under `tests/` route as
   tests-docs-only.

## 8. Constraints

- Python engineering rules apply: smallest clear change, Ruff clean,
  existing pyproject/env, no tool installs; gates and CI authoritative.
- `route_manifest` stays pure; all I/O stays in `gate.py` /
  `write_route_artifacts` (best-effort, never fails the gate).
- Token economy in all new prompts: short, machine-like.
- Escalation: consult architect only if a real design risk emerges during
  implementation (e.g. transport channel conflicts with handoff protocol).
  Security-reviewer reviews the finished trust boundary (registry
  validation + transport) at the end.

## Acceptance criteria

- All section-7 tests exist, initially failing, then pass.
- `route.json` from a repo with registered `data-engineer` + `ui-reviewer`
  contains both, ordered, deduped, capped at 2, only-registered.
- Repo without routing tables: identical routing behavior to today except
  the added empty `reviewers` field and `tests/` fix.
- `planner.prompt` updated; templates present but unregistered.
- Full gate (`ruff check`, `ruff format --check`, typing if configured,
  `pytest -q`) passes.
