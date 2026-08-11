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
python3 swarmforge/gates/gate.py --hardening
```

`--fast` applies Ruff only to changed Python files. Boundary gates run the
configured lint/format, typing and pytest commands. Passing output is silent;
failures are capped and the full log is retained under
`.swarmforge/artifacts/`.

`--hardening` is explicitly opt-in through `commands.hardening`. It is intended
for mutation, CRAP, complexity and duplication checks and is never called by
the fast, handoff or completion hooks. Successful reports are retained as
artifacts instead of being injected into agent context.

Codex hooks are in `.codex/hooks.json`; Claude hooks are in
`.claude/settings.json`. SwarmForge also runs the same gates independently at
handoff and completion, so correctness does not depend on either client.

Tune commands and limits in `swarmforge/python-gates.toml`.

## Cross-project toolchain

Check machine prerequisites, configured Codex/Claude backends, Claude profiles,
project dependency groups, and the CodeGraph index with:

```sh
./swarm doctor [project-root]
```

Prepare a project with:

```sh
./swarm bootstrap [project-root]
```

`bootstrap` runs direct argv only. For Python projects it uses `uv add --group`
to add any missing standard packages, updating `pyproject.toml` and `uv.lock`,
then synchronizes `dev` and `hardening`. It also initializes CodeGraph when a
Git project has no index. It never installs unpinned global Python packages or
guesses how to install missing system CLIs. Missing machine prerequisites block
the command with a deterministic report.

Python projects should version the standard groups themselves:

```toml
[dependency-groups]
dev = ["pytest", "pytest-cov", "ruff"]
hardening = ["mutmut", "pytest-crap", "xenon", "pylint"]
```

Projects that already expose `dev` through `[project.optional-dependencies]`
remain supported: `bootstrap` preserves that layout and uses `--extra dev`,
while keeping `hardening` as an explicit dependency group.

`pytest-bdd` belongs in `dev` only for projects that execute Gherkin features.
Because uv shares its download cache, each repository remains reproducible
without paying the full installation cost again.

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
tokens. The registry is deliberately bounded (32 agents, short identifiers and
routing lists); transported recommendations are capped at two reviewers and
4 KiB.

`specifier`, `hardener`, and `qa` ship as opt-in role prompts and can be added
to `swarmforge.conf` with `lazy-window` plus a matching
`project-agents.toml` entry. They are not registered or started by default.
`data-engineer` and `ui-reviewer` under `swarmforge/roles/templates/` are
templates only; copy or adapt one before registering it.

`specifier` can express executable behavioral acceptance in concise Gherkin.
`hardener` consumes the target repo's configured mutation, CRAP and DRY tools;
those commands belong in that repo's `python-gates.toml`, not globally here.

## Tests

```sh
bb test
python3 -m pytest -q test/python
ruff check swarmforge/gates swarmforge/scripts/*.py test/python
ruff format --check swarmforge/gates swarmforge/scripts/*.py test/python
```

See `SPEC_swarmforge_python_lean.md` for the design, rollout and benchmark plan.
