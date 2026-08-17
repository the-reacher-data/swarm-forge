# Roles and contracts

Load this reference before dispatching or emulating workflow roles.

## Routing

| Role | Contract |
|---|---|
| `lead-engineer` | Clarify material decisions, draft and refine the proposal, route local specialists, plan bounded work, and make final acceptance. Do not begin with implementation. |
| `proposal-critic` | Challenge assumptions, missing outcomes, unverifiable criteria, hidden scope, and simpler alternatives before implementation. Remain read-only. |
| `implementer` | Execute one bounded approved task and produce the smallest compatible tested change. |
| `verifier` | Independently test the result against acceptance criteria and deterministic evidence without editing production files. |
| `architecture-reviewer` | Review boundaries, dependency direction, contracts, state ownership, concurrency, and compatibility. Remain read-only. |
| `security-reviewer` | Review identity, authorization, secrets, untrusted input, tenant isolation, integrity, and abuse paths. Remain read-only. |
| `simplicity-reviewer` | Detect unnecessary concepts, indirection, speculative extensibility, and avoidable blast radius. Remain read-only. |

Use repository-local specialists for domain expertise. Missing optional reviewers
must degrade to Lead → Implementer → Verifier, never to an invented role.

## Proposal critic output

```yaml
verdict: PASS | REVISE | BLOCK
summary: ...
findings:
  - severity: blocking | major | minor
    claim: ...
    evidence: ...
    impact: ...
    recommendation: ...
assumptions_challenged: []
missing_decisions: []
simpler_alternative: null
```

Use `BLOCK` only when implementation needs user authority or an unavailable fact.
Use `REVISE` when the Lead can repair the proposal within approved scope.

## Verifier output

```yaml
verdict: PASS | FAIL
acceptance_criteria:
  - criterion: ...
    status: PASS | FAIL
    evidence: ...
checks: []
findings: []
unverified: []
```

## Reviewer finding

```yaml
severity: critical | high | medium | low
confidence: high | medium | low
location: ...
evidence: ...
impact: ...
recommendation: ...
```
