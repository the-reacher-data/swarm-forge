# Python gates

`gate.py` runs the configured deterministic checks for fast, stop,
pre-handoff, and pre-complete lifecycle boundaries. Explicit command arrays in
`swarmforge/python-gates.toml` are executed unchanged.

## Affected pytest selection

For default stop, pre-handoff, and pre-complete commands, the gate may replace
`pytest -q` with a confined list returned by CodeGraph. Selection requires an
existing `.codegraph/` directory, the `codegraph` CLI, and at least one changed
Python file. The gate invokes:

```text
codegraph affected --json --stdin -p <project-root>
```

Changed paths are supplied on stdin. Output must be a non-empty JSON list of
existing `test*.py` or `*_test.py` files whose resolved paths remain under the
project root. A timeout, command failure, malformed or unsafe result, missing
index/CLI, or no changed Python files silently falls back to the full
`pytest -q` suite.

Optional configuration:

```toml
[affected_tests]
enabled = true
timeout_seconds = 30
depth = 2
```

`depth` is omitted by default. Invalid affected-test configuration also falls
back silently. This selection is never applied to fast mode or explicitly
configured `commands.*` arrays.

## Local risk manifest and route

Successful or failed `pre-handoff` and `pre-complete` runs also make a local,
best-effort routing recommendation under:

```text
.swarmforge/artifacts/route/manifest.json
.swarmforge/artifacts/route/route.json
```

Generation happens after the configured commands finish. It is silent and
cannot change the gate's output or exit status; malformed configuration,
missing CodeGraph data, and filesystem errors are treated as unavailable
routing data. Fast and stop gates do not generate these artifacts.

The deterministic manifest records the current commit and merge base, compact
diff counts, bounded repository-relative paths, latest gate results, and an
optional affected-test count. It has no timestamp and is capped at 8 KiB.
Sensitive path patterns are excluded from its public path list, although those
paths still contribute to counts and mandatory risk classes. Neither artifact
contains file contents, diffs, environment values, or absolute paths.

The pure local router recommends one of `coder`, `done`, `architect`, or
`security-reviewer`. A failed gate always returns to `coder`; security and
migration classes then take precedence over the configured weighted signals.
Tests/docs-only changes recommend `done` unless a mandatory class matched.
This file is advisory only: it does not submit a handoff, launch an agent, or
execute artifact content.

Optional risk configuration is additive to the built-in safety patterns:

```toml
[risk]
architect_threshold = 5
max_paths = 50
security_patterns = ["infra/policies/*"]
migration_patterns = ["warehouse/ddl/*"]
public_api_patterns = ["src/sdk/*.py"]
sensitive_patterns = ["config/private/*"]

[risk.weights]
lockfile = 2
concurrency = 3
public-api = 2
size = 5
```

Weights must be non-negative integers, the threshold and path cap must be
positive integers, and pattern values must be string arrays. If any value in
the table is invalid, the entire table is ignored and safe defaults are used.
Gate telemetry includes only the optional `route` enum and integer
`risk_score`; the reasons list remains confined to `route.json`.
