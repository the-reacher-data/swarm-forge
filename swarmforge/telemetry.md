# Local telemetry

SwarmForge records small, local JSONL events for deterministic gates and
handoff lifecycle transitions. Telemetry is best effort: a recording failure
never changes a gate exit code or helper behavior, and successful recording is
silent.

Events are appended to `.swarmforge/metrics/events.jsonl` by default. Each line
is one JSON object with keys in this order:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer schema version, currently `1`. |
| `ts` | UTC ISO-8601 event timestamp. |
| `event` | `gate`, `handoff_submitted`, `task_accepted`, `task_completed`, `batch_accepted`, or `batch_completed`. |
| `id` | Task name or handoff/batch identifier when available; otherwise a generated UUID4. |
| `role` | `SWARMFORGE_ROLE`, or `null`. |
| `backend_instance` | Backend from column 6 of `.swarmforge/roles.tsv` for the current role, or `null`. |
| `result` | Gate status or lifecycle transition status. |
| `duration_ms` | Integer elapsed milliseconds when known, or `null`. Gate durations use a monotonic clock; completion durations use `completed_at - dequeued_at`. |
| `handoff_count` | Recipient or batch item count when known, or `null`. |
| `gate_failure_count` | `0` or `1` for gate events, otherwise `null`. Gates stop at the first failure. |
| `tool_output_bytes_exposed` | Diagnostic bytes printed for a failed gate, `0` for a passing gate, otherwise `null`. |
| `tokens` | Reserved provider counters. `input`, `output`, and `cache_read` are `null` in this version. |
| `gate_mode` | Gate mode. Present only on `gate` events. |
| `test_selection` | `affected`, `full`, or `null` when the gate does not run default pytest. |
| `test_selection_reason` | Selection result enum such as `ok`, `no_index`, `invalid_output`, or `null`. |
| `affected_test_count` | Number of selected affected tests, otherwise `null`. |

## Privacy and bounds

Telemetry never reads or records prompts, transcripts, credentials,
environment dumps, command output, or file contents. Gate diagnostics are
represented only by the number of bytes exposed. Provider token values are not
available and remain null.

Test-selection telemetry contains only an enum and count. Affected file paths
and test names are never included.

Each serialized line is limited to 4096 bytes and is dropped rather than
truncated when it cannot fit. CLI string inputs are limited to 256 characters.
Appending uses an exclusive file lock and one newline-terminated `O_APPEND`
write, so concurrent writers cannot interleave records.

## Configuration

Telemetry works without configuration. Set `SWARMFORGE_TELEMETRY=0` to disable
it without creating the metrics directory.

The optional `telemetry` table in `swarmforge/python-gates.toml` supports:

```toml
[telemetry]
enabled = true
dir = ".swarmforge/metrics"
```

`dir` is resolved against the project root. Invalid telemetry configuration
disables recording silently. Missing or unwritable destinations, malformed
records, and unavailable recorder code are also silent no-ops.

## CLI

Lifecycle helpers use the stdlib-only recorder directly:

```text
python3 swarmforge/telemetry/metrics.py record \
  --root /path/to/project \
  --event task_accepted \
  --id task-name \
  --result accepted
```

Optional numeric flags are `--duration-ms`, `--handoff-count`,
`--gate-failure-count`, and `--tool-output-bytes-exposed`. Gate records may also
provide `--gate-mode`.
