# Task: risk-manifest-router

Deterministic local risk manifest + explainable rule-based router, produced at
the pre-handoff / pre-complete gates. No LLM, no network, stdlib only. At most
4 focused commits (tests+docs with the code they cover); hand back the tip.

## Deliverables

1. `swarmforge/gates/manifest.py` — manifest builder (importable + CLI).
2. `swarmforge/gates/risk.py` — risk scorer and router (importable, pure
   functions over the manifest + config; no subprocess calls of its own).
3. Integration in `swarmforge/gates/gate.py`: in `pre-handoff` and
   `pre-complete` modes only, after the gate commands finish (pass or fail),
   build the manifest, score it, and atomically write
   `.swarmforge/artifacts/route/manifest.json` and
   `.swarmforge/artifacts/route/route.json` (write to temp file in the same
   directory, then rename). Silent on success; any manifest/router failure is
   swallowed and NEVER changes gate exit codes or stderr (same posture as
   telemetry).
4. Tests in `test/python/` + docs (`swarmforge/gates/README.md` section) +
   telemetry addition below.

## Manifest (`manifest.json`)

Deterministic: same repository state and config → byte-identical output. No
wall-clock timestamps. Fixed key order, compact separators. Fields:

- `schema_version`: 1
- `commit`: full HEAD sha; `base`: merge-base with `main` when resolvable,
  else `null`
- `diff`: `{files_changed, insertions, deletions}` (ints, from
  `git diff --numstat <base-or-empty-tree>..HEAD` plus staged/untracked à la
  `changed_python_files`, `gate.py:32`)
- `paths`: repo-relative changed paths, sorted, capped at `max_paths`
  (default 50). Exclude any path matching the sensitive filter (defaults:
  `*.env*`, `*secret*`, `*credential*`, `*key*`, `*.pem`, `*token*`);
  excluded paths still count in `diff` and still feed the scorer.
- `gates`: latest known result per gate mode, read from the tail (bounded,
  last 64 KiB max) of `.swarmforge/metrics/events.jsonl` when present, else
  `{}` — plus the in-process result of the gate run that is generating the
  manifest.
- `codegraph_impact`: `{affected_test_count: int}` obtained via the existing
  `codegraph affected --json --stdin -p <root>` call pattern with the same
  timeout/validation/confinement rules as the gate's affected-tests feature
  (`gate.py:183`); `null` on any failure, absence, or timeout — never blocks.
- `truncated`: bool. Hard cap: serialized manifest ≤ 8192 bytes. If over,
  truncate progressively (drop `paths` entries from the end, then the whole
  list) until under the cap and set `truncated: true`. Never emit >8 KiB.
- No file contents, no diffs, no env values, no absolute paths.

## Risk scorer and router (`route.json`)

Explainable and configurable; output:
`{schema_version: 1, route, score, reasons, commit}` where `reasons` is a
sorted list of stable enum strings (e.g. `class:security`,
`signal:lockfile`, `signal:large-change`) — no free text, no paths.

Mandatory classes (path-pattern matched against ALL changed paths, including
sensitive-filtered ones):

- `security`: patterns over auth/crypto/secret/token/password/permission
  paths and the sensitive filter above.
- `migrations`: `*migration*`, `*schema*`, `*.sql`, alembic-style dirs.

Signals (each adds configured weight to `score`):

- `lockfile`: dependency/lock manifests (`pyproject.toml`, `requirements*`,
  `*.lock`, `package.json`, `pnpm-lock.yaml`, …)
- `concurrency`: added lines in the diff matching `async def|threading|
  multiprocessing|asyncio|Lock(|Semaphore(` — scan added lines only, bounded
  to first 10000 added lines, content used for matching only, never stored.
- `public-api`: changes under configured public-API globs (default:
  `**/__init__.py`, `swarmforge/scripts/*.sh`, `swarmforge/gates/*.py`)
- `size`: files_changed and inserted-LOC thresholds (defaults: >10 files or
  >500 insertions)
- `tests-docs-only`: every changed path under `test/`, `docs/`, or `*.md` —
  forces route `done` unless a mandatory class also matched.

Routing enum `coder | done | architect | security-reviewer`, precedence:

1. Any gate result failed (including the generating run) → `coder`, always.
2. `security` class matched → `security-reviewer`.
3. `migrations` class matched, or `score >= architect_threshold`
   (default 5) → `architect`.
4. Otherwise → `done`.

Configuration: optional `[risk]` table in `swarmforge/python-gates.toml`:
`architect_threshold` (positive int), per-signal `weights` (ints ≥ 0),
additive pattern lists `security_patterns`, `migration_patterns`,
`public_api_patterns`, `sensitive_patterns`, and `max_paths`. Validation on
load; ANY invalid value → discard the whole table and use built-in defaults
(safe fallback, silent, gate unaffected). Router must work with CodeGraph
absent: `codegraph_impact: null` is not an error and adds no weight.

## Handoff integration — recommendation only

`route.json` is a recommendation the planner reads when processing the next
inbound handoff. Do NOT send handoffs, start agents, execute commands from
manifest/route content, or add new activation mechanisms: the existing
protocol (agent-authored `swarm_handoff.sh` drafts; `handoffd` lazily starts
registered lazy roles on delivery, `handoffd.bb:109`) is the only activation
path, and routes must only ever name roles present in
`swarmforge/project-agents.toml` plus `coder`/`done`; an unknown role in
config maps to safe fallback defaults.

## Telemetry

Additive optional key on the `gate` event: `route` (enum above or `null`) and
`risk_score` (int | null). Counts and enums only; reasons list is NOT
recorded (bounded size). Document in `swarmforge/telemetry.md`.

## Mandatory tests (`test/python/test_manifest.py`, `test_risk.py`, plus
`test_gate.py` integration)

1. tests/docs-only low-risk change → route `done`, no reviewer.
2. High-risk (files/LOC over threshold) → `architect`.
3. Change touching an auth/secret path → `security-reviewer`.
4. Failed gate + security path → `coder` (precedence).
5. Determinism: building twice on identical state → byte-identical
   `manifest.json` and `route.json`.
6. Privacy: sensitive paths absent from `paths`; no file contents or env
   values anywhere in either JSON; reasons contain no paths.
7. Size: pathological repo state (hundreds of long paths) → ≤8192 bytes,
   `truncated: true`, still valid JSON.
8. Invalid `[risk]` config → defaults used, gate exit code unchanged.
9. CodeGraph absent → `codegraph_impact: null`, routing still works.
10. Gate success remains byte-silent with manifest generation enabled.

## Acceptance

`python3 -m pytest test/python -q` and `bb test` pass; Ruff clean; no new
dependencies; no tool installs; ≤4 commits. Architect stays lazy — escalate
only on a real architectural finding, via planner.

## Out of scope

Auto-sending handoffs from hooks, LLM scoring, remote export, scoring
non-Python content semantics, rewriting existing gate/telemetry behavior.
