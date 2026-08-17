---
name: dev-loop
description: Use when non-trivial software work is ambiguous, crosses components or repositories, changes behavior or contracts, carries material risk, or needs independent challenge and verification before acceptance.
---

# Dev Loop

Coordinate only the roles needed to turn a request into verified work. Keep the
`lead-engineer` responsible for decisions; keep critics, implementers, verifiers,
and reviewers independent.

## Start from local context

Read repository instructions first. Discover repository-local agents and skills
before choosing global roles. Prefer a local specialist for domain knowledge and
the global roles for process. Do not use SwarmForge or Paseo unless the user
explicitly requests them.

Classify the request:

- **Direct:** answer or inspect; do not start a loop.
- **Light:** explicit low-risk edit; Lead → Implementer → Verifier.
- **Full:** ambiguity, multiple files, contracts, or visible behavior; run both
  loops below.
- **High risk:** security, data integrity, migrations, concurrency, or broad blast
  radius; add the relevant reviewers after verification.

Read [`references/roles-and-contracts.md`](references/roles-and-contracts.md)
before dispatching a role.

## Proposal loop

1. Have `lead-engineer` clarify only decisions that materially affect scope,
   behavior, compatibility, cost, or risk. Use `brainstorming` when needed.
2. State the problem, desired outcome, scope, constraints, acceptance criteria,
   risks, open decisions, and verification plan.
3. Give that proposal—not the Lead's private reasoning—to `proposal-critic`.
4. Have the Lead accept supported findings, explain rejections, and resubmit the
   revised proposal to a fresh critic.
5. Stop on `PASS`, or after three rounds. Return unresolved decisions to the user.

Require human approval when discovery materially changes requested scope, public
behavior, architecture, dependencies, migration, cost, or risk. A precise request
whose scope remains unchanged already grants implementation authority.

## Delivery loop

1. Have the Lead split the approved proposal into bounded tasks with explicit
   files or symbols, non-goals, criteria, and checks.
2. Give one task to `implementer`. Require the smallest compatible change and
   appropriate tests.
3. Give the proposal, diff, criteria, and check evidence to `verifier`; never pass
   the Implementer's completion claim as proof.
4. On `FAIL`, send verified findings through one focused Implementer → Verifier
   correction. Stop after three rounds for the same root cause and return the
   blocker to the user.

## Review by demonstrated risk

After verification passes, dispatch only applicable read-only reviewers:

- `architecture-reviewer`: boundaries, contracts, state, concurrency, compatibility;
- `security-reviewer`: identity, authorization, secrets, input, tenants, integrity;
- `simplicity-reviewer`: large diffs, new abstractions, indirection, speculative scope.

When two or more dimensions apply, run those reviewers as a council in independent
contexts. Aggregate by evidence, severity, and confidence—not majority. Send only
supported blocking findings through one Delivery-loop correction, with at most two
council corrections.

## Accept

Have the Lead accept only when every criterion has fresh evidence, required checks
pass, no demonstrated blocker remains, residual risks are explicit, and the result
stays inside approved scope. Report outcome, evidence, changed files, and risks.
