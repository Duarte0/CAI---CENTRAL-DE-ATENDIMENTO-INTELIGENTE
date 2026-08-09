# Issue Creation Pass

You are running one issue-creation iteration for this repository through `codex exec`.

Read `@OPERATING_PRINCIPLES.md` first — it defines non-interactive mode, sources-of-truth precedence, repository inspection, file reading strategy, Graphify usage, and final verification. This prompt covers only what's specific to issue creation.

## Deliverable

One implementation-ready issue for the highest-priority eligible slice of pending work not already covered by an existing issue.

Do not implement code, rewrite the plan, modify specs, or commit.

## This pass runs repeatedly — design for that

- Never create a duplicate issue. Before selecting a candidate, search existing issues by plan item, spec ID, title, and concept — treat differently-worded issues with the same outcome as duplicates.
- Size each issue to be completed, tested, documented, and committed in one build iteration — coherent, not trivial file-level edits.
- If the top candidate is blocked or not spec-ready, record it in the report, skip it, and evaluate the next one. Don't stop the whole pass over one blocked item.

## Selecting the next issue

Evaluate pending plan items in order: prerequisites before dependents → earlier approved phase before later → higher priority before lower → risk-reduction/foundational work before what depends on it → smallest coherent slice with a verifiable outcome.

A candidate is eligible only when: it's pending or partial; its dependencies are complete (or fit in the same slice); an approved spec defines enough behavior and acceptance criteria; no open/in-progress issue covers the same outcome; the work isn't already implemented and verified.

If the plan item is implemented but missing a specific validation/doc/migration/compat requirement, scope the issue to just that gap.

If the candidate is too large for one iteration, pick the earliest independently valuable slice and note the rest as follow-up dependencies — don't create those issues now.

If nothing eligible remains: no file, finish `ISSUES_COMPLETE`. If work exists but nothing is eligible (missing specs/decisions): no file, finish `ISSUES_BLOCKED` with the exact gaps.

## Writing the issue

Follow the repo's existing template/conventions exactly. If no template/directory exists, only invent one when a clear convention can be derived from existing docs — otherwise `ISSUES_BLOCKED`.

Filename: next sequential ID following existing files (never reuse IDs from closed/archived entries). Default pattern if none is documented: `issues/NNNN - <concise-kebab-case-title>.md`. New issues default to `status: open` unless the repo's convention differs.

Include: stable ID + outcome-oriented title; type/status/priority/phase/date per convention; direct references to the plan item and spec ID/version; dependencies (issues, specs, migrations, infra, decisions); the verified gap as context; objective and expected outcome; in-scope and explicitly out-of-scope; concrete implementation guidance grounded in the spec and current code; likely affected components (not certain file changes); data/migration/compat/security/observability/rollout notes when relevant; required validation; testable acceptance-criteria checkboxes (unchecked); required doc/Graphify updates on completion; the requirement to close via `IMPLEMENTATION_PLAN.md` sync and one focused commit.

Reference the canonical spec — don't copy it into the issue.

**Implementation guidance** must describe the change sequence, contracts/invariants that must hold, important edge cases, and useful validation commands — concrete enough to act on without re-doing discovery, but without speculative filenames/commands/architecture. No unrelated cleanup. If it needs a PRD/architecture/spec-level decision, it's not eligible yet.

**Acceptance criteria** derive from the spec, scoped to this slice only. Cover expected + negative behavior, data integrity, error/retry/idempotency/concurrency handling, security/config constraints, test expectations, doc sync, and relevant validation commands. Never pre-check any box.

## Scope

May modify: exactly one new file under `issues/`, Graphify metadata if required.

Must not: touch `IMPLEMENTATION_PLAN.md`, `specs/`, application code, tests, migrations, config; create more than one issue; close/rewrite existing issues; commit or tag.

## Report

Finish with exactly one status label, then a concise report:

- `ISSUE_CREATED` — path, selected plan item, related specs, priority, dependencies.
- `ISSUES_COMPLETE` — why all planned work is already covered.
- `ISSUES_BLOCKED` — exact missing specs/decisions, and any candidates skipped.