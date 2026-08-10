# SPEC — SwarmForge Lean Python Fork

**Status:** Draft for technical validation
**Target:** Fork of `unclebob/swarm-forge`
**Proposed runnable branch:** `python-lean`
**Baseline for comparison:** upstream `two-pack`
**Primary goal:** preserve multi-agent engineering quality while reducing LLM token consumption and unnecessary agent turns.

---

## 1. Problem

SwarmForge's `two-pack` uses two permanent LLM roles:

```text
coder -> cleaner -> coder
```

The cleaner is asked to perform work such as coverage review, CRAP/DRY review, architecture review, mutation hardening, and cleanup. A substantial part of this work can be performed or pre-filtered by deterministic/non-LLM tools.

This creates avoidable cost:

1. A second model re-reads code that static tools can inspect.
2. Successful tool output is often sent through an LLM despite requiring no judgment.
3. Handoffs can cause the next agent to rediscover repository structure.
4. Full test/lint output can consume context.
5. Multi-agent routing happens by topology rather than by measured risk.
6. Repeated project/role instructions consume input tokens every session.

The fork MUST optimize **total task tokens**, not only visible prose output.

---

## 2. Design principle

> **Tools discover facts. LLMs make decisions.**

Do not use an LLM for work that can be reliably answered by:

- formatter;
- linter;
- type checker;
- test runner;
- coverage tool;
- import/dependency rules;
- AST/code graph;
- security scanner;
- dependency audit;
- diff analysis;
- configurable thresholds.

An LLM should be invoked only when semantic judgment is needed.

---

## 3. Non-goals

This fork MUST NOT initially:

- rewrite SwarmForge in Python;
- replace tmux/worktrees/handoff daemon;
- create a new agent framework;
- require a specific Python web framework;
- require a specific CodeGraph implementation;
- add an LLM summarizer in front of another LLM;
- run mutation testing after every edit;
- run the full test suite after every file write;
- install ten overlapping linters by default;
- make Caveman mandatory before benchmarking it;
- break non-Python SwarmForge workflows.

Keep the fork upstream-friendly.

---

## 4. Proposed topology

Default topology:

```text
                        ┌──────────────┐
                        │ PLANNER LLM  │
                        │ Codex/Claude │
                        └──────┬───────┘
                               │ compact task manifest
                               ▼
                        ┌──────────────┐
                        │   CODER LLM  │
                        │ Codex/Claude │
                        └──────┬───────┘
                               │ edits
                               ▼
                     ┌──────────────────┐
                     │ FAST LOCAL GATES │
                     │     no LLM       │
                     └──────┬───────────┘
                            │
                    stop / handoff
                            │
                            ▼
                  ┌─────────────────────┐
                  │ FULL QUALITY GATE   │
                  │       no LLM        │
                  └─────────┬───────────┘
                            │
             ┌──────────────┼──────────────┐
             │              │              │
           FAIL          PASS/LOW       PASS/HIGH
             │             RISK            RISK
             ▼              │              ▼
        CODER fixes         DONE      ARCHITECT LLM
                                             │
                                             ▼
                                      targeted review
```

There is **no permanent cleaner LLM**.

The `architect` is conditional.

### 4.1 Backend kinds and backend instances

The launcher MUST distinguish a backend kind from a configured backend
instance:

```text
backend kind      = claude | codex
backend instance  = claude-trabajo | claude-personal | codex-primary
```

Two Claude Code profiles using the same `claude` executable are separate
instances when they use different `CLAUDE_CONFIG_DIR` values. Their
configuration, credentials, usage and telemetry MUST remain isolated.

The current target environment has two Claude instances:

```text
claude-trabajo   -> ~/.aisw/profiles/trabajo
claude-personal  -> ~/.aisw/profiles/personal
```

Machine-specific paths MUST NOT be committed. A portable project file names
logical instances, while an ignored local file resolves those names to
commands and environment variables.

SwarmForge MUST NOT automatically fall back from one Claude instance to the
other. Any fallback or account substitution requires an explicit local policy.

### 4.2 Project-specific agents

The planner MAY select specialized agents supplied by the target repository.
It MUST select only from a trusted project registry; it MUST NOT invent and
execute arbitrary commands or agent definitions at runtime.

Discovery candidates include:

```text
.claude/agents/*.md
.agents/skills/*/SKILL.md
Codex project agent/profile definitions supported by the installed version
swarmforge/project-agents.toml
```

Discovery MUST produce a compact catalog containing only names, descriptions,
tags and permitted backend instances. Full agent prompts are loaded only when
the selected agent starts.

Specialized agents SHOULD be lazy. Preparing a worktree is allowed at swarm
startup; starting the LLM session and loading its prompt are deferred until a
routed task requires that agent.

---

## 5. Fork strategy

### 5.1 Branching

Keep `main` as close to upstream as practical.

Create:

```text
python-lean
```

Prefer deriving the runnable workflow from upstream `two-pack`.

Do not copy unrelated changes into `main` unless they are generic enough to propose upstream.

### 5.2 Generic core extension

Add a generic optional SwarmForge lifecycle hook mechanism rather than hard-coding Python logic into the handoff scripts.

Minimum useful hook:

```text
swarmforge/hooks/pre-handoff
```

Behavior:

- if absent: existing SwarmForge behavior unchanged;
- if executable: run before `swarm_handoff.sh` queues a `git_handoff`;
- exit `0`: handoff may continue;
- non-zero: handoff is rejected and concise diagnostics are returned to the current agent;
- successful hooks SHOULD print nothing;
- hook output MUST be bounded.

Optional future hooks:

```text
swarmforge/hooks/post-receive
swarmforge/hooks/pre-complete
```

Do not add them until there is a concrete use case.

### 5.3 Python implementation stays local

Python-specific scripts live under:

```text
swarmforge/python/
```

or:

```text
swarmforge/gates/python/
```

The generic handoff layer only knows how to execute a hook.

---

## 6. Proposed repository layout

```text
swarmforge/
├── swarmforge.conf
├── backends.toml
├── project-agents.toml
├── constitution.prompt
├── constitution/
│   └── articles/
│       ├── project.prompt
│       ├── local-engineering.prompt
│       ├── local-workflow.prompt
│       └── token-economy.prompt
├── roles/
│   ├── coder.prompt
│   └── architect.prompt
├── hooks/
│   └── pre-handoff
├── gates/
│   ├── gate.py
│   ├── config.py
│   ├── changed_files.py
│   ├── changed_symbols.py
│   ├── risk.py
│   ├── context_manifest.py
│   ├── output.py
│   ├── adapters/
│   │   ├── base.py
│   │   ├── codegraph.py
│   │   └── null_codegraph.py
│   └── checks/
│       ├── ruff.py
│       ├── typing.py
│       ├── tests.py
│       ├── coverage.py
│       ├── architecture.py
│       ├── dead_code.py
│       ├── dependencies.py
│       └── security.py
└── templates/
    ├── pyproject.swarmforge.example.toml
    ├── codex-hooks.example.json
    ├── claude-settings.example.json
    └── backends.local.example.toml
```

Avoid abstraction for abstraction's sake. If adapter classes add no real value, use small functions/protocols instead.

---

## 7. Deterministic quality pipeline

### Tier 0 — edit-time, very cheap

Run only against changed Python files where possible.

```bash
ruff check <changed-files> --fix
ruff format <changed-files>
```

Optional type check only if it is fast enough for the target repository.

Purpose:

- formatting;
- import cleanup;
- simple correctness/lint rules;
- immediate feedback.

Target execution: fast enough to run repeatedly.

### Tier 1 — task/stop gate

Run before an agent declares implementation complete.

Recommended baseline:

```bash
ruff check .
ruff format --check .
mypy src/                  # OR project's configured type checker
pytest <affected-tests> -q
coverage / changed-line coverage check
```

Do not force `mypy` if the project standard is `pyright`, `basedpyright`, `ty`, etc.
The fork must discover/configure one type checker, not run several.

### Tier 2 — pre-handoff gate

Run before sending a commit to an architect/reviewer or before final completion.

Recommended baseline:

```text
lint
format
typing
affected tests
architecture rules
changed-line coverage
dependency consistency
risk scoring
context manifest generation
```

Full suite only when configured or risk requires it.

### Tier 3 — expensive automated checks

Run conditionally:

- full pytest suite;
- mutation testing (`mutmut` or project equivalent);
- dead-code scan (`vulture`);
- dependency audit;
- security scan;
- duplication analysis;
- API compatibility checks.

These checks MUST NOT run on every file edit.

---

## 8. Recommended automated tools

### Default — keep this small

| Concern | Preferred approach | Default |
|---|---|---:|
| Format + lint | Ruff | YES |
| Type safety | Existing project checker | YES |
| Unit/integration tests | pytest | YES |
| Coverage | coverage.py / pytest-cov | YES |
| Architecture boundaries | import-linter or project architecture tests | YES |
| Dependency consistency | deptry or equivalent | OPTIONAL |
| Dead code | vulture | CONDITIONAL |
| Security rules | Ruff security rules and/or Bandit/Semgrep | CONDITIONAL |
| Dependency CVEs | pip-audit | CI / CONDITIONAL |
| Mutation testing | mutmut | HIGH-RISK / NIGHTLY |
| Secrets | detect-secrets/gitleaks if already used | CI / PRE-COMMIT |

Do not add overlapping tools without a measurable reason.

### Ruff configuration

Enable rules appropriate to the project. Prefer using Ruff for checks it already covers rather than adding separate Python tools.

Examples worth considering:

```text
E/F       correctness/basic lint
I         imports
UP        pyupgrade
B         bugbear
SIM       simplify
C90       McCabe complexity
S         security rules where applicable
RUF       Ruff-specific
```

Rule selection must be project-configurable.

---

## 9. Architecture tests — high value, zero LLM tokens

This is one of the most important additions.

Architecture rules should be executable.

Example hexagonal rule:

```text
domain          -> may NOT import infrastructure
application     -> may NOT import concrete infrastructure adapters
infrastructure  -> may depend inward
api             -> may call application, not repositories directly
```

Prefer an automated import contract over repeatedly asking an architect model to detect dependency direction violations.

Example conceptual configuration:

```ini
[importlinter]
root_package = my_app

[importlinter:contract:domain-independence]
name = Domain independent of infrastructure
type = forbidden
source_modules =
    my_app.domain
forbidden_modules =
    my_app.infrastructure
```

Architecture LLM then reviews only issues that static boundaries cannot express well:

- responsibility allocation;
- inappropriate abstractions;
- leaky domain concepts;
- cohesion;
- semantic coupling;
- API design;
- significant design trade-offs.

---

## 10. Code graph integration

Code graph is an **optimization adapter**, not a hard dependency.

Required interface:

```python
class CodeIntel(Protocol):
    def changed_symbols(self, base: str, head: str) -> list[Symbol]: ...
    def callers(self, symbol: Symbol, limit: int) -> list[Symbol]: ...
    def callees(self, symbol: Symbol, limit: int) -> list[Symbol]: ...
    def impact(self, symbols: list[Symbol], limit: int) -> Impact: ...
```

Possible backend:

```text
CodeGraph CLI/MCP
```

The gate itself SHOULD prefer CLI/local execution when available because deterministic orchestration does not need an MCP conversation.

MCP is useful for the architect/coder when interactive graph queries are needed.

### Rule

Never make the LLM browse the whole repository if a structural query can identify the relevant symbols first.

### Limits

Graph results passed to an LLM MUST be bounded:

```text
max_symbols: 50
max_callers_per_symbol: 20
max_manifest_bytes: 8192
```

If truncated, include:

```json
"truncated": true
```

and store the full machine output on disk.

---

## 11. Machine-generated handoff manifest

Do not send prose handoffs containing a human/LLM recap of everything.

Generate a compact deterministic manifest.

Example:

```json
{
  "task": "pricing-service-validation",
  "commit": "0123456789",
  "base": "abcdef0123",
  "changed_files": [
    "src/app/pricing/service.py",
    "tests/pricing/test_service.py"
  ],
  "changed_symbols": [
    "PricingService.calculate"
  ],
  "diff": {
    "files": 2,
    "insertions": 34,
    "deletions": 11
  },
  "quality": {
    "ruff": "pass",
    "typing": "pass",
    "tests": {
      "status": "pass",
      "passed": 21,
      "failed": 0
    },
    "changed_line_coverage": 96.2,
    "architecture": "pass"
  },
  "impact": {
    "callers": 4,
    "public_api_changed": false,
    "dependency_boundary_changed": false
  },
  "risk": {
    "score": 1,
    "route": "done"
  }
}
```

Rules:

- no successful test log;
- no full stack trace unless failure requires it;
- no repeated source code;
- no full git diff by default;
- no unbounded MCP result;
- include paths to full artifacts if an agent needs to inspect them.

---

## 12. Deterministic risk router

The second LLM should be activated by risk, not by default topology.

Initial risk scoring can be intentionally simple and explainable.

Example:

```text
+3  authentication/authorization/security-sensitive file
+3  database migration/schema change
+3  dependency/lockfile change
+3  concurrency/async coordination change
+2  public API signature changed
+2  architecture boundary changed
+2  > 5 production files changed
+2  > 150 production LOC changed
+2  changed-line coverage below threshold
+2  code graph reports large blast radius
+1  complexity increased above configured threshold

-2  test-only change
-2  docs-only change
```

Routing example:

```text
gate failure       -> coder
score 0..2         -> done
score 3..5         -> architect optional/configurable
score >= 6         -> architect required
security/migration -> architect required regardless of score
```

Thresholds MUST be configurable and validated using real project data.

Do not call an LLM to calculate this score.

---

## 13. Fast Codex and Claude hooks

Agent-specific hooks are an optimization, not the source of truth.
SwarmForge's own pre-handoff gate remains authoritative because other backends may be used.

Template:

```json
{
  "description": "Lean Python local feedback",
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$(git rev-parse --show-toplevel)/swarmforge/gates/gate.py\" --fast",
            "timeout": 15,
            "statusMessage": "Running fast Python checks"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$(git rev-parse --show-toplevel)/swarmforge/gates/gate.py\" --stop",
            "timeout": 180,
            "statusMessage": "Running task quality gate"
          }
        ]
      }
    ]
  }
}
```

Implementation MUST verify the actual Codex tool matcher names supported by the installed/current Codex version.

Do not run the full test suite on every `PostToolUse`.

Claude MUST receive an equivalent project-local `.claude/settings.json`
configuration using its supported `PostToolUse` and `Stop` events. Both
adapters invoke the same project-local gate executable and differ only in
their event input/output translation.

The authoritative workflow MUST also provide both:

```text
swarmforge/hooks/pre-handoff
swarmforge/hooks/pre-complete
```

`pre-complete` is required because a low-risk task can finish without an
architect handoff.

---

## 14. Tool output budget

Tool output can consume as much context as prose.

All automated tools should use a bounded output adapter.

Rules:

```text
PASS:
    one line or no output

FAIL:
    summary
    first N relevant diagnostics
    path to full log

DEFAULT MAX:
    60 diagnostic lines
    8 KiB text
```

Example:

```text
FAIL mypy: 17 errors in 6 files
src/foo.py:31 incompatible return type
src/bar.py:88 missing argument "id"
...
[15 diagnostics omitted]
full_log=.swarmforge/artifacts/mypy-0123456789.log
```

Never paste 4,000 passing pytest lines into an agent context.

---

## 15. Context budget policy

Add a constitution article:

```text
token-economy.prompt
```

Core rules:

1. Do not re-read a file already inspected unless it changed or a specific unresolved question requires it.
2. Prefer symbol/graph search before repository-wide search.
3. Do not narrate tool calls.
4. Do not summarize successful deterministic checks.
5. Read the smallest source range needed.
6. Do not ask another LLM to summarize deterministic output.
7. Prefer diff + impacted symbols over entire files for review.
8. Never include full lockfiles, generated files, snapshots, or vendor code in context unless directly required.
9. Cap handoff context.
10. If evidence is sufficient, stop exploring.

This article itself MUST be short.

---

## 16. Caveman integration

### Decision

Caveman is **optional and measured**, not blindly enabled.

Reason:

- compressing visible prose can help;
- long coding sessions are often dominated by input context, code and tool calls;
- adding a large skill prompt can itself cost input tokens.

### Phase 1 — use the idea without skill overhead

Make SwarmForge role prompts terse:

```text
Communication:
- No narration.
- No restatement of task.
- Successful tool checks: omit.
- Failures: only actionable diagnostics.
- Handoffs: machine manifest, not prose.
- Final status: <= 8 lines unless user requests detail.
```

This gives much of the behavioral value at almost zero prompt cost.

### Phase 2 — one-time prompt compression

Evaluate `caveman-compress` on:

```text
swarmforge/roles/*.prompt
swarmforge/constitution/articles/*.prompt
AGENTS.md
CLAUDE.md
```

Requirements:

- compression is performed once;
- diff is human-reviewed;
- technical MUST/SHOULD semantics must remain unchanged;
- compressed files are committed;
- no runtime LLM compression step.

### Phase 3 — MCP description compression

Evaluate `caveman-shrink` only if MCP tool schemas/descriptions are a meaningful fraction of input context.

Good candidate:

```text
CodeGraph MCP
```

But prefer CLI for deterministic gate queries.

### Phase 4 — full Caveman skill

A/B test it.

Enable by default only if:

```text
total tokens/task materially improve
AND
task success does not regress materially
AND
agent retries do not increase
```

Suggested acceptance target:

```text
>= 5% total token reduction on our agentic benchmark
<= 2 percentage-point task success regression
```

These are project acceptance thresholds, not external benchmark claims.

### Do not add CaveKit initially

It overlaps with SwarmForge's workflow/spec/handoff responsibilities.
Avoid stacking orchestration frameworks until a specific missing capability is proven.

---

## 17. Cleaner decomposition

Map the current conceptual cleaner responsibilities to tools first.

```text
coverage improvement
    -> coverage.py / changed-line coverage

CRAP / complexity
    -> coverage + complexity metric / Ruff C90
    -> LLM only for semantic refactoring choices

DRY
    -> duplication detector when configured
    -> architect only for meaningful abstraction decisions

architecture
    -> import-linter + code graph
    -> architect only for semantic design

encapsulation
    -> architect when risk requires it

mutation hardening
    -> mutmut conditionally

cleanup
    -> Ruff autofix + format + type checker

security
    -> static rules + dependency audit
    -> architect/security reviewer only on findings/high-risk changes
```

The goal is not "remove review."
The goal is "review only unresolved semantic risk."

---

## 18. Changed-test selection

Do not always run all tests during the edit loop.

Strategy:

```text
changed module
    -> direct corresponding tests
    -> code graph callers
    -> tests covering impacted package
```

If reliable impact mapping is unavailable:

```text
fast loop: targeted tests
pre-handoff: full relevant package
high risk/final CI: full suite
```

CI remains authoritative.

A false-negative test selector is worse than a slightly slower gate, so selection must fall back safely.

---

## 19. Failure behavior

Automated fixes are allowed only for operations that are intentionally behavior-preserving.

Allowed by default:

```text
ruff check --fix
ruff format
```

Do NOT automatically:

- rewrite architecture;
- delete vulture findings;
- update dependencies;
- alter migrations;
- accept snapshot changes;
- suppress typing errors;
- add `# noqa` broadly;
- mutate public APIs.

Those findings go to the coder/architect.

---

## 20. Agent responsibilities

### Coder

Owns:

- understand task;
- implement behavior;
- tests;
- fix deterministic gate failures.

Does NOT:

- manually perform lint/format analysis;
- narrate passing checks;
- invoke architect for low-risk clean changes;
- reread whole repository by default.

### Architect

Only receives routed tasks.

Owns:

- semantic architecture;
- responsibility/cohesion;
- abstractions;
- dependency direction not expressible by static contracts;
- public API design;
- significant security/design tradeoffs;
- large blast-radius review.

Input should be:

```text
task
commit
manifest
diff reference
targeted graph context
```

Not the entire coder conversation.

---

## 21. Suggested `swarmforge.conf`

Conceptual target:

```text
# Planner and implementation agent may use different providers/instances.
window planner claude-trabajo master
window coder codex-primary coder

# Conditional semantic reviewers are not started until routed work exists.
lazy-window architect claude-personal architect batch
lazy-window security-reviewer claude-trabajo security batch
```

`lazy-window` is a proposed backward-compatible extension. It prepares the
role/worktree without starting the backend process. The first routed task
starts the configured backend instance and loads that agent's full prompt.

The planner chooses only among agents allowed by the project registry. A
deterministic policy maps each agent to an authorized backend instance.

---

## 22. Configuration

Add one small project-level configuration file, for example:

```toml
# swarmforge/python-gates.toml

[paths]
source = ["src"]
tests = ["tests"]

[lint]
tool = "ruff"
autofix_fast = true

[typing]
command = ["mypy", "src"]

[tests]
fast_command = ["pytest", "-q"]
full_command = ["pytest", "-q"]

[coverage]
changed_lines_min = 90

[architecture]
enabled = true
command = ["lint-imports"]

[code_intel]
provider = "auto"
max_symbols = 50
max_callers_per_symbol = 20

[output]
max_lines = 60
max_bytes = 8192

[risk]
architect_threshold = 6
large_change_files = 5
large_change_loc = 150

[expensive]
mutation = "high-risk"
security = "high-risk"
dead_code = "manual-or-ci"
```

Commands should be configurable arrays, not shell strings, where practical.

Backend definitions use two layers:

```text
swarmforge/backends.toml
    portable instance names and backend kinds

.swarmforge/backends.local.toml
    ignored machine-local command paths and profile directories
```

Example local mapping:

```toml
[instances.claude-trabajo]
kind = "claude"
command = ["claude"]

[instances.claude-trabajo.environment]
CLAUDE_CONFIG_DIR = "/Users/example/.aisw/profiles/trabajo"

[instances.claude-personal]
kind = "claude"
command = ["claude"]

[instances.claude-personal.environment]
CLAUDE_CONFIG_DIR = "/Users/example/.aisw/profiles/personal"
```

Secrets MUST NOT be stored in either file. The launcher should execute argv
arrays with an explicit environment rather than interpolating instance
configuration into shell strings.

Example project agent registry:

```toml
[agents.security-reviewer]
backend_instance = "claude-trabajo"
mode = "lazy"
prompt = ".claude/agents/security-reviewer.md"
tags = ["security", "auth", "crypto"]

[agents.data-architect]
backend_instance = "codex-primary"
mode = "lazy"
prompt = ".agents/prompts/data-architect.md"
tags = ["sql", "etl", "schema", "migration"]
```

---

## 23. Auto-detection

The gate MAY inspect `pyproject.toml` and lockfiles to select existing project tools.

Examples:

```text
ruff configured       -> use Ruff
mypy configured       -> use mypy
pyright configured    -> use pyright
pytest configured     -> use pytest
import-linter present -> enable architecture gate
```

Rules:

- prefer the project's existing tooling;
- do not silently install dependencies during a swarm run;
- missing optional tools -> `SKIP` with one concise reason;
- missing required tools -> fail setup early.

---

## 24. Token telemetry

This is required. Otherwise the fork cannot prove its reason for existing.

Capture per task where provider/backend permits:

```json
{
  "task_id": "...",
  "backend": "codex",
  "model": "...",
  "input_tokens": 0,
  "cached_input_tokens": 0,
  "output_tokens": 0,
  "reasoning_tokens": 0,
  "agent_turns": 0,
  "handoffs": 0,
  "architect_invoked": false,
  "tool_output_bytes_exposed_to_llm": 0,
  "wall_seconds": 0,
  "gate_failures": 0,
  "result": "pass"
}
```

If exact token counters are unavailable, mark fields `null`.
Do not invent estimates and present them as exact measurements.

Store telemetry locally under:

```text
.swarmforge/metrics/
```

Do not commit it by default.

---

## 25. Benchmark plan

Compare:

```text
A = upstream two-pack
B = python-lean
C = python-lean + optional Caveman
```

Use the same:

- repository snapshot;
- task descriptions;
- model/backend;
- reasoning level;
- environment;
- tests;
- task timeout.

Task set SHOULD include:

```text
small bug fix
feature touching 1 module
feature touching multiple layers
refactor
async/concurrency change
API change
database/migration change
test-only change
architecture-sensitive change
large change
```

Minimum initial validation:

```text
20 representative tasks
```

Prefer multiple runs if budget allows because LLM runs vary.

Primary metric:

```text
total tokens per successfully completed task
```

Secondary metrics:

```text
task success rate
LLM turns
LLM handoffs
architect invocation rate
wall time
gate execution time
escaped defects
retries after deterministic failure
tool-output bytes exposed to models
```

---

## 26. Initial success criteria

Proposed validation targets:

```text
>= 30% reduction in median total tokens / successful task vs upstream two-pack
>= 50% of low-risk tasks completed by coder without second LLM
<= 2 percentage-point regression in benchmark task success
0 known quality gates bypassed to obtain token savings
architect invoked on 100% of configured mandatory-risk classes
handoff manifest <= 8 KiB in normal operation
passing tool output exposed to LLM ~= 0
```

These thresholds are hypotheses. Adjust after baseline measurements.

---

## 27. Implementation phases

### Phase A — baseline

Before changing behavior:

1. fork SwarmForge;
2. run upstream `two-pack`;
3. collect token/turn/handoff metrics;
4. save benchmark tasks.

Deliverable:

```text
docs/baseline.md
```

### Phase B — deterministic gate

Implement:

```text
Ruff
type checker adapter
pytest
coverage
bounded output
changed-file detection
pre-handoff hook
pre-complete hook
Codex hook adapter
Claude hook adapter
```

No CodeGraph yet.

### Phase C — conditional architect

Implement:

```text
risk score
route decision
machine manifest
architect only on high risk
backend instance registry
one lazy specialized agent
compact planner agent catalog
```

Measure again.

### Phase D — architecture automation

Add:

```text
import-linter/project architecture contracts
dependency checks
public API/risk detection
```

### Phase E — CodeGraph

Add adapter and impact manifest.

Measure whether it reduces:

```text
file reads
search calls
input tokens
architect context
```

Do not retain it if it adds complexity without measurable value.

### Phase F — Caveman experiment

Test separately:

```text
terse native prompts
caveman-compress
caveman-shrink
full Caveman
```

Keep only components that improve total task economics.

### Phase G — expensive checks

Add mutation/security/dead-code checks only after routing is stable.

---

## 28. Acceptance tests for the fork itself

The implementation MUST include automated tests for:

### Backward compatibility

```text
no pre-handoff hook -> original handoff behavior
```

### Gate blocking

```text
failing required check -> git_handoff is not queued
```

### Quiet success

```text
all checks pass -> near-zero stdout
```

### Bounded failure

```text
10,000-line tool failure -> agent receives <= configured output limit
```

### Routing

```text
low-risk clean diff -> architect not required
high-risk clean diff -> architect required
security-sensitive diff -> architect required
```

### Tool absence

```text
optional tool missing -> SKIP
required tool missing -> setup/gate failure
```

### Manifest

```text
manifest deterministic for same commit/config
manifest within size cap
manifest contains no raw successful test output
```

### Hook safety

```text
hook timeout -> safe failure
hook crash -> safe failure
```

---

## 29. Security requirements

- Never execute commands extracted from LLM prose.
- Gate commands come only from trusted repository configuration.
- Prefer argv arrays over `shell=True`.
- Validate repo-relative paths.
- Bound tool runtime.
- Bound tool output.
- Do not expose `.env`, credentials, key files, or secret-bearing config in manifests.
- Do not send dependency lockfile contents to LLM unless necessary.
- Do not auto-trust repository-local Codex hooks without user review.
- External MCPs are optional; local CLI is preferred for deterministic code intelligence.

---

## 30. Important design decision: orchestrator gate vs agent hook

Use both, with different purposes:

```text
Codex PostToolUse/Stop hooks
    = fast developer/agent feedback

SwarmForge pre-handoff hook
    = authoritative workflow quality gate
```

Why:

- SwarmForge supports multiple backends.
- Agent-native hooks may vary.
- A model should not be able to accidentally skip the handoff gate.
- The orchestrator knows when work crosses agent boundaries.

---

## 31. What to implement first

The first PR SHOULD be deliberately small:

```text
1. generic `pre-handoff` hook support
2. Python gate runner
3. Ruff + pytest + configured type checker
4. bounded output
5. tests
6. `python-lean` role/constitution prompts
```

Do NOT include in PR #1:

```text
CodeGraph
Caveman
mutmut
Semgrep
complex risk scoring
telemetry dashboard
```

Those should arrive only after the basic mechanism is measured.

---

## 32. Expected final workflow

```text
User task
   │
   ▼
CODER
   │
   ├── edit
   │    └── fast hook: Ruff changed files
   │
   ├── test targeted behavior
   │
   └── commit
          │
          ▼
    PRE-HANDOFF GATE
          │
          ├── lint
          ├── type
          ├── tests
          ├── coverage
          ├── architecture
          ├── graph impact
          └── risk score
                  │
       ┌──────────┼─────────────┐
       │          │             │
      FAIL      LOW RISK      HIGH RISK
       │          │             │
       ▼          ▼             ▼
     CODER       DONE        ARCHITECT
                                │
                         semantic review only
```

---

## 33. Agent instruction

When implementing this spec:

1. Inspect upstream SwarmForge before modifying it.
2. Preserve its existing handoff protocol unless a change is required.
3. Prefer one generic extension point over Python-specific changes in core scripts.
4. Keep all quality checks non-LLM.
5. Keep outputs bounded and machine-readable.
6. Add tests before changing workflow behavior.
7. Do not add a dependency without documenting what decision it replaces.
8. Do not implement all optional ideas at once.
9. Benchmark before claiming token savings.
10. If an assumption in this spec conflicts with current upstream behavior, preserve upstream behavior and document the deviation.

---

## 34. Validation questions the implementing agent must answer

Before coding, return a concise design review answering:

```text
1. Where exactly should the generic pre-handoff hook be inserted?
2. Can it be added without changing the handoff file format?
3. How will failures be returned to the sender?
4. How will we ensure hook output cannot flood model context?
5. How will low-risk completion bypass the architect cleanly?
6. What exact token/usage data can Codex and Claude expose in our environment?
7. Which existing SwarmForge tests should be extended?
8. Which Python tools can be auto-detected from pyproject.toml?
9. Which CodeGraph implementation/CLI best fits local Python repos, if any?
10. Does Caveman reduce TOTAL tokens in our benchmark, not just prose output?
11. How are backend-instance permissions and account isolation validated?
12. Can a lazy agent start from a queued handoff without loading its prompt at swarm startup?
```

The agent should propose a minimal PR plan after answering these questions.

---

## 35. Research basis / links for agent verification

Verify these against their current upstream versions before implementation:

- SwarmForge: `https://github.com/unclebob/swarm-forge`
- SwarmForge `two-pack`: `https://github.com/unclebob/swarm-forge/tree/two-pack`
- Codex hooks: `https://developers.openai.com/codex/hooks`
- Caveman: `https://github.com/JuliusBrussee/caveman`

Important current observations when this spec was authored (2026-08-10):

- SwarmForge runnable workflows are branch-based and `two-pack` currently uses `coder` + `cleaner`.
- SwarmForge handoffs are file-based and validated by helper scripts.
- Codex currently supports lifecycle command hooks including `PostToolUse` and `Stop`, with repo-local hook configuration.
- Caveman's own documentation distinguishes large prose savings from much smaller savings in long agentic coding runs; benchmark it on this workflow.
- Caveman also exposes one-time memory/prompt compression and an optional MCP-description compression mechanism.

---

## 36. Definition of done

The fork is ready for broader use when:

```text
[ ] upstream two-pack baseline recorded
[ ] generic pre-handoff hook implemented and tested
[ ] python gate works with at least one real project
[ ] Ruff/type/tests/coverage integrated
[ ] output bounded
[ ] low-risk tasks can finish without architect
[ ] high-risk tasks route deterministically
[ ] architecture contracts supported
[ ] token metrics collected
[ ] A/B benchmark completed
[ ] optional CodeGraph measured
[ ] optional Caveman measured
[ ] Codex and both Claude profiles can coexist in one swarm
[ ] Claude profile credentials/configuration remain isolated
[ ] repository-specific agents are discovered through a trusted registry
[ ] unselected lazy agents consume no model startup tokens
[ ] README explains setup and trade-offs
[ ] no claim of savings is made without benchmark data
```

---

# Core thesis

The fork should not try to build a smarter swarm.

It should build a **smaller swarm with better sensors**.

```text
deterministic tools -> facts
risk router         -> decide whether review is needed
LLM coder           -> implementation
LLM architect       -> semantic judgment only
```

Every removed unnecessary LLM turn is likely worth more than aggressively shortening one model's prose.
