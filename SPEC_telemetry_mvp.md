# Task: telemetry-mvp — deterministic telemetry for python-lean

## Goal

Record local, deterministic JSONL telemetry under `.swarmforge/metrics/` at the
deterministic gate and at handoff lifecycle boundaries. No model transcripts are
parsed; no prompts, credentials, or tool output content are stored.

## Deliverables

1. `swarmforge/telemetry/metrics.py` — stdlib-only Python module, also runnable
   as a CLI (`python3 swarmforge/telemetry/metrics.py record ...`).
2. Gate integration in `swarmforge/gates/gate.py`.
3. Handoff lifecycle integration in `swarm_handoff.bb`,
   `ready_for_next_task.bb`, `ready_for_next_batch.bb`,
   `done_with_current_task.bb`, `done_with_current_batch.bb` (best-effort
   subprocess calls to the CLI).
4. Focused tests: `test/python/test_telemetry.py`, additions to
   `test/python/test_gate.py`, and at least one assertion in
   `test/swarmforge/script_test.clj` that a lifecycle helper appends a metrics
   line while remaining silent on success.
5. Documentation: `swarmforge/telemetry.md` (schema, field semantics, privacy
   guarantees, configuration).

## Event schema (one JSON object per line)

Required keys, in this order, always present:

- `schema_version`: `1`
- `ts`: UTC ISO-8601 instant
- `event`: one of `gate`, `handoff_submitted`, `task_accepted`,
  `task_completed`, `batch_accepted`, `batch_completed`
- `id`: task name or handoff id when known, else a generated UUID4 string
- `role`: value of `SWARMFORGE_ROLE`, else `null`
- `backend_instance`: resolved from `.swarmforge/roles.tsv` column 6 by role,
  else `null`
- `result`: `pass` | `fail` | `setup_failed` | `config_failed` | `timeout` |
  `submitted` | `accepted` | `completed`
- `duration_ms`: integer when wall time is known (monotonic clock for the gate;
  `completed_at - dequeued_at` for task completion when both parse), else `null`
- `handoff_count`: integer when known (recipient count on submit, files in a
  batch), else `null`
- `gate_failure_count`: integer for `gate` events (0 or 1; the gate stops at
  first failure), else `null`
- `tool_output_bytes_exposed`: for `gate` events, the diagnostic bytes actually
  printed on failure (`min(log_size, max_bytes)`), `0` on success; else `null`
- `tokens`: `{"input": null, "output": null, "cache_read": null}` — provider
  token counters are not available in the MVP; keep the keys, keep them null.

Extra keys allowed: `gate_mode` for `gate` events. Nothing else.

## Hard constraints

- **Privacy**: never write prompts, transcripts, credentials, env dumps,
  command output content, or file contents — only counts, ids, timestamps,
  statuses. Tests must assert a gate-failure event contains no diagnostic text.
- **Silent success**: telemetry writes nothing to stdout/stderr on success.
- **Never interferes**: telemetry failure (unwritable dir, oversize record,
  bad config) must not change gate exit codes, gate stderr contract, or
  handoff helper behavior. In `gate.py`, wrap recording in a broad
  try/except; in `.bb` scripts, invoke the CLI with `:continue true`
  semantics and discard output.
- **Atomic append**: open `.swarmforge/metrics/events.jsonl` with `O_APPEND`,
  hold `fcntl.flock(LOCK_EX)` for a single `write()` of one newline-terminated
  line. Create the directory with `parents=True, exist_ok=True`.
- **Bounded output**: serialized line hard cap 4096 bytes; if exceeded, drop
  the record silently (never truncate mid-JSON). String field inputs from CLI
  args capped at 256 chars.
- **Deterministic**: fixed key order, `json.dumps(..., sort_keys=False,
  separators=(",", ":"))`, no floats for durations (integers only).
- **Optional configuration**: works with zero config. `SWARMFORGE_TELEMETRY=0`
  disables all recording. Optional `[telemetry]` table in
  `swarmforge/python-gates.toml` with `enabled` (bool, default true) and
  `dir` (string, default `.swarmforge/metrics`, resolved against project
  root). Invalid config disables telemetry silently; it never fails the gate.
- Stdlib only; no new dependencies; no tool installs.

## Integration points

- `gate.py:main` / `run_commands` (`swarmforge/gates/gate.py:159,214`): time
  the full command sequence with `time.monotonic()`; emit one `gate` event on
  every exit path (pass, command failure, timeout, setup failure, config
  failure). `tool_output_bytes_exposed` mirrors what `emit_failure`
  (`gate.py:137`) prints: `min(size, max_bytes)`. Import `metrics.py` by file
  path (`importlib.util.spec_from_file_location`) so no packaging is needed;
  absence of the module is a silent no-op.
- `swarm_handoff.bb`: after the outbox file is installed, record
  `handoff_submitted` with the handoff id, recipient count as
  `handoff_count`, result `submitted`.
- `ready_for_next_task.bb` / `ready_for_next_batch.bb`: after moving work to
  `in_process` and writing `dequeued_at`, record `task_accepted` /
  `batch_accepted` (batch: `handoff_count` = file count).
- `done_with_current_task.bb` / `done_with_current_batch.bb`: after writing
  `completed_at` and moving to `completed`, record `task_completed` /
  `batch_completed` with `duration_ms` when `dequeued_at` is parseable.
- Do not modify `hooks/pre-complete`, `hooks/pre-handoff`, `handoffd.bb`, or
  daemon delivery paths.

## Acceptance criteria

1. `python3 -m pytest test/python -q` passes; `bb test` passes.
2. A passing gate run produces exactly one `gate` JSONL line and no extra
   stdout/stderr; a failing gate run keeps its existing stderr contract
   byte-compatible and records `result: "fail"` with correct
   `tool_output_bytes_exposed`.
3. Concurrent appends (spawn ≥8 processes writing simultaneously in a test)
   yield only complete, parseable JSON lines.
4. `SWARMFORGE_TELEMETRY=0` yields zero writes and zero directory creation.
5. A metrics dir made read-only does not change gate exit codes or helper
   output.
6. Every recorded event validates against the schema above; `tokens` subkeys
   are present and null.
7. Ruff clean under the fast gate; no behavior change to existing tests.

## Out of scope

Provider token counting, aggregation/reporting CLIs, rotation, remote export,
instrumenting `handoffd.bb` or tmux paths.
