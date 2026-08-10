# Repository instructions

## Deterministic first

- Let `swarmforge/gates/gate.py` own Ruff, formatting, typing and pytest checks.
- Do not paste successful check output into model context.
- Use `pre-handoff` and `pre-complete`; do not bypass them.

## CodeGraph

Use CodeGraph for structural questions: definitions, callers, callees, impact,
file structure and focused task context. Use text search only for literal text.
Start with `codegraph_context`, then make at most one focused
`codegraph_explore` call for source. Trust graph results and do not repeat them
with grep. If `.codegraph/` is absent, ask before running `codegraph init -i`.
