# Task: runtime-affected-tests

Four small, separable changes. Implement as a series of focused commits, one
per scope item (tests+docs in the same commit as the code they cover); hand
back the tip. Do not modify handoff protocol, daemon delivery semantics, or
telemetry privacy guarantees.

## 1. handoffd survives launcher exit

Problem: `start-handoff-daemon!` (`swarmforge/scripts/swarmforge.bb:433`)
spawns `handoffd.bb` as a direct child of the launcher via `process/process`.
In non-interactive runs the daemon dies with the launcher's session/process
group.

Required behavior:

- The daemon keeps running and delivering after the launcher process exits,
  including when the launcher was run non-interactively (no tty).
- Detach mechanism: start the daemon in its own session — `setsid` when
  available, else `nohup` — with stdin from `/dev/null` and stdout/stderr
  redirected to the existing `handoffd.log`. Preserve the existing
  `sleep-inhibitor-prefix` composition and the pid/stop-file contract
  (`handoffd.bb` writes `handoffd.pid`, honors `stop` file and TERM).
- No change to `stop_handoff_daemon` behavior; it must still stop the
  detached daemon.

Test (in `test/swarmforge/script_test.clj`): spawn a short-lived parent shell
that starts the daemon the same way the launcher does against a temp project
root, let the parent exit, assert the daemon pid is still alive and processes
one outbox file; then stop it via the stop file and assert clean shutdown.

## 2. Deterministic CodeGraph preparation for worktrees

Only when `<project-root>/.codegraph/` exists. If the root is not initialized,
do nothing — never initialize the root, never assume consent, never install
tools.

- Hook point: `prepare-worktrees!` (`swarmforge/scripts/swarmforge.bb:269`)
  and the equivalent path when an existing worktree is (re)started.
- For each role worktree (skip `none`/`master`) lacking `.codegraph/`: if the
  `codegraph` CLI is on PATH, run `codegraph init -i <worktree-path>`
  (or `init` + `codegraph index -q`) with output suppressed and a bounded
  timeout (default 120s). If the CLI is missing, print one concise skip line
  and continue (optional-tool rule). Preparation failure is non-fatal to
  swarm startup: one concise line, continue.
- Idempotent: a worktree that already has `.codegraph/` is left untouched.

Test: temp project with a fake `codegraph` executable on PATH recording its
argv — assert it is invoked once per new worktree when root `.codegraph/`
exists, never invoked when it does not, and that a missing CLI does not fail
startup.

## 3. Gate runs only affected tests via `codegraph affected`

Extend `swarmforge/gates/gate.py`. Applies wherever the gate would run the
default `pytest -q` step (stop / pre-handoff / pre-complete default commands);
explicitly configured `commands.*` in `python-gates.toml` are never rewritten.

Selection procedure:

1. Preconditions: `<root>/.codegraph/` exists, `codegraph` CLI on PATH, and
   the feature is enabled. Otherwise: fallback.
2. Compute changed files with the existing `changed_python_files` machinery
   (`gate.py:32`).
3. No changed Python files → fallback (conservative: config/deps may have
   changed).
4. Run `codegraph affected --json --stdin -p <root>` feeding the changed
   files, with `subprocess.run(..., timeout=<affected timeout>)`. Machine
   readable only; never parse decorated output.
5. Fallback on ANY of: non-zero exit, timeout, output that is not valid JSON,
   JSON that is not a list of strings, empty result, any path that does not
   exist, is not under `root.resolve()` (same confinement rule as telemetry
   dir), or does not match `test*.py`/`*_test.py` naming.
6. Otherwise run `pytest -q <affected files>` in place of the full-suite
   pytest step.

Fallback = the previously configured behavior, i.e. the unmodified default
`pytest -q` full run. Fallback is silent on the success path — the gate's
zero-output-to-LLM-on-success contract is unchanged in every branch. Failing
tests keep the existing `emit_failure` stderr contract.

Configuration (optional, zero-config works): `[affected_tests]` table in
`swarmforge/python-gates.toml`:

- `enabled` (bool, default `true`; the feature still self-disables without
  `.codegraph/` or the CLI)
- `timeout_seconds` (positive int, default `30`)
- `depth` (positive int, optional; passed as `--depth` when set)

Invalid `[affected_tests]` config → fallback silently (do not fail the gate;
mirrors telemetry's invalid-config posture). Reuse `integer_setting`-style
validation.

## 4. Telemetry and docs

Extend the existing `gate` event (`swarmforge/telemetry/metrics.py`) with
optional keys — additive, `schema_version` stays `1`:

- `test_selection`: `"affected"` | `"full"` | `null`
- `test_selection_reason`: one of `ok`, `disabled`, `no_index`, `cli_missing`,
  `no_changes`, `error`, `timeout`, `invalid_output`, `no_tests`, else `null`
- `affected_test_count`: integer | `null`

Never record file paths, test names, or command output — counts and enums
only. Document the new keys in `swarmforge/telemetry.md` and the selection
procedure + config in gate documentation (README section or
`swarmforge/gates/` doc, matching existing conventions).

## Acceptance criteria

1. `python3 -m pytest test/python -q` and `bb test` pass.
2. Daemon-survival test proves delivery after launcher exit and clean stop.
3. Worktree prep: invoked only when root `.codegraph/` exists; missing CLI
   skips concisely; failures never abort startup; idempotent.
4. Gate tests (extend `test/python/test_gate.py`) cover: affected path runs
   pytest with exactly the returned files; each fallback trigger in step 5
   (use a fake `codegraph` executable emitting crafted stdout/exit codes);
   configured `commands.*` untouched; success remains byte-silent in both
   affected and fallback branches; telemetry records the correct
   `test_selection`/reason/count with no paths.
5. Explicitly configured command arrays in `python-gates.toml` behave exactly
   as before.
6. Ruff clean; no new dependencies; no tool installation anywhere.

## Out of scope

Root CodeGraph initialization, `codegraph sync` scheduling, non-pytest
frameworks, affected-test selection for the `fast` mode (it runs no tests),
architect involvement (changes are separable and behavior-scoped).
