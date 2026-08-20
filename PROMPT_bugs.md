# Bug Discovery Pass

You are running one bug-discovery iteration for this repository through `codex exec`.

Read `@OPERATING_PRINCIPLES.md` first. It defines non-interactive execution, source-of-truth precedence, repository inspection, file-reading strategy, Graphify usage, scope discipline, and final verification. This prompt specializes the `issues` pass for confirmed defects.

## Deliverable

Create at most one implementation-ready bug issue under `issues/` for the highest-priority confirmed defect that is not already covered by an existing issue.

Do not implement a fix, modify application code, add tests, modify specs, rewrite `IMPLEMENTATION_PLAN.md`, or commit.

If no eligible bug exists, create no file and finish with `ISSUES_COMPLETE`.

If bugs exist but all are blocked by a missing specification, unresolved product decision, or insufficient evidence, create no file and finish with `ISSUES_BLOCKED` describing the exact blockers.

## Repository and evidence inspection

Before editing:

1. Inspect the working tree and repository structure. Preserve pre-existing changes.
2. Read every applicable `AGENTS.md` and the relevant sections of `README.md`, `PRD.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, `specs/README.md`, the issue template, and related issues.
3. Use Graphify only if it is installed and the repository documents a workflow for it. Confirm important findings against the actual files.
4. Search before concluding that behavior is missing, broken, or duplicated.
5. Inspect relevant source, tests, migrations, configuration, logs, and recent history as needed.

Use failures, regressions, incorrect state transitions, unhandled errors, data-integrity violations, spec deviations, unsafe retries, broken permissions, and reproducible edge cases as bug signals. Treat TODOs, placeholders, comments, and suspicious patterns only as leads; they are not bugs without evidence.

Do not assume the source is under `src/`, nor assume a framework, package manager, test command, or directory layout. Infer them from the repository.

## Sources of truth

When sources disagree, apply the precedence from `@OPERATING_PRINCIPLES.md`:

1. Repository instructions and `AGENTS.md`.
2. Source code, migrations, and configuration for current behavior.
3. Tests for behavior currently verified.
4. `PRD.md` for product and business requirements.
5. `ARCHITECTURE.md` for approved technical direction.
6. `specs/` for implementation contracts.
7. `IMPLEMENTATION_PLAN.md` for sequencing and status.
8. Existing issues and Git history as supporting evidence.

Classify each discrepancy as a confirmed bug, stale documentation, intended future work, or unresolved decision. Do not turn an unresolved decision into a bug issue.

## Selecting the bug

Search existing issues by ID, title, plan item, spec, symptom, and concept. A differently worded issue with the same outcome is a duplicate.

Evaluate confirmed candidates in this order:

1. data loss, security, authorization, integrity, or production-blocking impact;
2. critical/high priority before medium/low;
3. regressions and user-visible failures before internal defects;
4. earlier plan phase and satisfied dependencies before later work;
5. smallest coherent slice that can be implemented, tested, documented, and committed in one build iteration.

An eligible bug must have:

- clear evidence that the current behavior is incorrect;
- an expected behavior grounded in an approved spec, PRD, architecture decision, or regression test;
- enough information to implement and validate the correction without inventing business behavior;
- no unresolved material product, security, or architecture decision;
- no open issue already covering the same outcome;
- a scope suitable for one build iteration.

If the expected behavior is ambiguous or no adequate contract exists, do not create the issue. Report that the candidate requires a `specs` pass or user decision.

## Writing the issue

Follow the repository's existing issue template and naming convention exactly. If no convention exists, use the convention defined by `PROMPT_issues.md`; do not invent a second format.

The issue must include, where applicable:

- stable sequential ID and outcome-oriented title;
- `type: bug`, status, priority, phase, and date according to repository convention;
- direct references to the relevant plan item, spec ID/version, PRD, architecture decision, and related issues;
- verified observed behavior and expected behavior;
- reproduction steps, affected scope, and evidence from searches/tests/logs;
- likely root cause, clearly distinguishing evidence from a hypothesis;
- dependencies and relevant data, migration, compatibility, security, and observability constraints;
- in-scope and explicitly out-of-scope work;
- concrete implementation guidance without prescribing speculative filenames or unrelated cleanup;
- required regression tests and validation commands;
- unchecked acceptance criteria covering positive behavior, negative/error paths, idempotency/concurrency or data integrity when relevant;
- required documentation, Graphify, plan, and spec synchronization after implementation;
- the requirement to close the issue only after validation and one focused commit.

The issue describes the contract and verification needed by `build`; it must not contain the fix itself or pre-check acceptance criteria.

## Scope

May modify:

- exactly one new file under `issues/`;
- Graphify metadata only when the repository's established workflow requires it for that issue.

Must not:

- implement or partially implement the fix;
- modify application code, tests, migrations, configuration, dependencies, infrastructure, specs, or `IMPLEMENTATION_PLAN.md`;
- create multiple issues;
- close, rewrite, or renumber existing issues;
- commit, tag, push, or open a pull request.

## Final verification and report

Before finishing, verify that the issue is not a duplicate, all references and identifiers are valid, acceptance criteria are unchecked, and no prohibited file was changed.

Finish with exactly one status label followed by a concise report:

- `ISSUE_CREATED` — issue path, confirmed bug, evidence, related contract/spec, priority, and dependencies.
- `ISSUES_COMPLETE` — why no uncovered confirmed bug is eligible.
- `ISSUES_BLOCKED` — exact evidence, specification, decision, or dependency blockers and the candidates skipped.
