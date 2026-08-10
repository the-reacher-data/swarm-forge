# SwarmForge Python Lean

A token-conscious SwarmForge workflow for Python repositories. It moves lint,
format, typing, tests and lifecycle checks into bounded deterministic tools,
while keeping LLMs for implementation and semantic decisions.

This fork lives at `the-reacher-data/swarm-forge`; `unclebob/swarm-forge` is
kept as the upstream remote.

## Topology

```text
planner              claude-trabajo   eager   main checkout
coder                codex-primary    eager   .worktrees/coder
architect            claude-personal  lazy    .worktrees/architect
security-reviewer    claude-trabajo   lazy    .worktrees/security
```

Lazy roles get a tmux session and worktree at startup, but their model process
starts only when a handoff is routed to them. There is no automatic fallback
between Claude accounts.

## Prerequisites

- Python 3.11+
- Git, tmux and Babashka
- `codex`, `claude` and the target project's Python tools on `PATH`
- CodeGraph (`codegraph install` configures supported agents globally)
- Claude profiles `~/.aisw/profiles/trabajo` and
  `~/.aisw/profiles/personal`

## Use in a Python project

Copy this branch into the project root, preserving `.codex/`, `.claude/`,
`.mcp.json`, `AGENTS.md`, `CLAUDE.md`, `swarm`, `close-swarm` and
`swarmforge/`. Then:

```sh
codegraph init -i
./swarm
```

CodeGraph initialization is explicit because it writes a project index. Codex
also asks once whether the repository hooks are trusted; review them and accept
only for repositories you trust.

## Deterministic gates

```sh
python3 swarmforge/gates/gate.py --fast
python3 swarmforge/gates/gate.py --pre-handoff
python3 swarmforge/gates/gate.py --pre-complete
```

`--fast` applies Ruff only to changed Python files. Boundary gates run the
configured lint/format, typing and pytest commands. Passing output is silent;
failures are capped and the full log is retained under
`.swarmforge/artifacts/`.

Codex hooks are in `.codex/hooks.json`; Claude hooks are in
`.claude/settings.json`. SwarmForge also runs the same gates independently at
handoff and completion, so correctness does not depend on either client.

Tune commands and limits in `swarmforge/python-gates.toml`.

## Backends and project agents

Portable instances live in `swarmforge/backends.toml`. Machine-specific
command paths and environment overrides belong in the ignored
`.swarmforge/backends.local.toml`; see
`swarmforge/backends.local.example.toml`. Never store credentials there.

`swarmforge/project-agents.toml` is the trust boundary for specialists. The
planner sees only the compact catalog produced by:

```sh
agent_catalog.py --root .
```

Add a registry entry and matching `lazy-window` only for specialists the target
repository genuinely needs. Unselected specialists consume no startup model
tokens.

## Tests

```sh
bb test
python3 -m pytest -q test/python
ruff check swarmforge/gates swarmforge/scripts/*.py test/python
ruff format --check swarmforge/gates swarmforge/scripts/*.py test/python
```

See `SPEC_swarmforge_python_lean.md` for the design, rollout and benchmark plan.
