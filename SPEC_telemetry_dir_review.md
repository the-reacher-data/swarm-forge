# Review request: telemetry-dir-confinement (security)

## Finding (independent review)

`swarmforge/telemetry/metrics.py:67` resolves the optional `[telemetry].dir`
setting as `root / directory`. `pathlib` joining accepts absolute paths
(`Path(root) / "/etc/x"` → `/etc/x`) and does not normalize `..` segments, so
project-controlled configuration in `swarmforge/python-gates.toml` can direct
telemetry writes to arbitrary filesystem locations outside the repository
(directory creation via `mkdir(parents=True)` at `metrics.py:156` plus append
at `metrics.py:157`).

## Questions for security review

1. Must telemetry directories be confined strictly beneath the project root?
2. If yes, what is the minimal correction? Expected shape: resolve the joined
   path and verify it is within `root.resolve()` (e.g. `Path.is_relative_to`),
   treating any escape — absolute path, `..` traversal, or symlink escape if
   deemed in scope — as invalid configuration that silently disables
   telemetry, consistent with the existing invalid-config behavior in
   `_telemetry_dir`.

## Requested deliverable

The minimal tested correction: the confinement check in
`swarmforge/telemetry/metrics.py` plus focused tests in
`test/python/test_telemetry.py` covering absolute `dir`, `..` traversal, and
the unchanged happy path. No behavior change for valid relative directories;
silent-success and never-interferes constraints from `SPEC_telemetry_mvp.md`
still apply. State explicitly whether symlink escape is in scope for the MVP
or deferred, with rationale.
