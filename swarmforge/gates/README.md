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
