# Refactoring Issue Pass

You are running one structural-refactoring issue-creation iteration for this repository through `codex exec`.

Read `@OPERATING_PRINCIPLES.md` first. It defines non-interactive execution, source-of-truth precedence, repository inspection, file-reading strategy, Graphify usage, scope discipline, and final verification. This prompt specializes the `issues` pass for structural refactoring.

## Deliverable

Create at most one implementation-ready refactoring issue under `issues/` for the highest-priority eligible structural improvement that is not already covered by an existing issue.

This pass discovers and specifies the work. It does not perform the refactor.

Do not modify application code, tests, migrations, configuration, dependencies, infrastructure, specs, or `IMPLEMENTATION_PLAN.md`. Do not commit.

If no eligible refactoring remains, create no file and finish with `ISSUES_COMPLETE`.

If candidates exist but all require a behavior decision, missing specification, or architectural approval, create no file and finish with `ISSUES_BLOCKED` describing the exact blockers.

## Definition and boundary

Refactoring means structural reorganization that preserves externally observable behavior.

The issue must not introduce or intentionally change:

- product behavior or business rules;
- public API contracts, component props, routes, events, or CLI interfaces;
- database schema or persistence semantics;
- authorization, security, or data-retention policy;
- retry, idempotency, concurrency, or failure semantics;
- user-visible design or workflow, except for changes proven necessary to preserve the existing behavior.

If the desired change alters behavior, creates a feature, fixes a defect, or requires a material architecture decision, do not create a refactoring issue. Report it for the appropriate `bugs`, `plan`, or `specs` pass.

## Repository and architecture inspection

Before editing:

1. Inspect the working tree and repository structure. Preserve pre-existing changes.
2. Read every applicable `AGENTS.md` and the relevant sections of `README.md`, `PRD.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, `specs/README.md`, the issue template, and related issues/specs.
3. Use Graphify only if it is installed and the repository documents a workflow for it. Confirm important findings against the actual files.
4. Infer the established architecture from the real codebase: layers, dependency direction, naming, module boundaries, co-location patterns, and existing service/repository/component conventions.
5. Search before concluding that a responsibility is duplicated, misplaced, unused, or too large. Inspect callers, imports, tests, configuration, and recent history.

Use large or complex files, mixed responsibilities, duplicated logic, incorrect module placement, unstable dependency direction, difficult-to-isolate tests, and repeated adapters as candidate signals. A file's line count alone is not sufficient justification.

Do not assume a framework, package manager, source directory, test command, or architecture pattern that the repository does not demonstrate.

## Sources of truth

When sources disagree, apply the precedence from `@OPERATING_PRINCIPLES.md`:

1. Repository instructions and `AGENTS.md`.
2. Source code, migrations, and configuration for current structure and behavior.
3. Tests for behavior currently verified.
4. `PRD.md` for approved product requirements.
5. `ARCHITECTURE.md` for approved technical direction.
6. `specs/` for implementation contracts.
7. `IMPLEMENTATION_PLAN.md` for sequencing and status.
8. Existing issues and Git history as supporting evidence.

Use the architecture already approved by the repository. Do not use refactoring as a pretext to replace the stack, introduce a new architectural layer, upgrade dependencies, or normalize the entire codebase.

## Selecting the refactor

Search existing issues by ID, title, plan item, affected component, smell, and concept. A differently worded issue with the same outcome is a duplicate.

Evaluate candidates in this order:

1. risk reduction for correctness, maintainability, deployability, or test isolation;
2. refactors required by an approved plan, architecture decision, or pending dependency;
3. reduction of duplicated or conflicting behavior paths;
4. high cognitive load or misplaced responsibility with clear architectural evidence;
5. smallest coherent slice that can be implemented, tested, documented, and committed in one build iteration.

An eligible refactor must have:

- a concrete structural problem demonstrated by the repository;
- a target boundary or organization supported by existing architecture and conventions;
- explicit invariants that must remain unchanged;
- a validation strategy capable of detecting behavioral regressions;
- no unresolved material product or architecture decision;
- no open issue already covering the same outcome;
- a scope small enough for one build iteration.

Do not create a broad issue such as “refactor the backend” or “clean up the frontend.” Split it at a coherent responsibility boundary and leave follow-up work for later iterations.

## Writing the issue

Follow the repository's existing issue template and naming convention exactly. If no convention exists, use the convention defined by `PROMPT_issues.md`; do not invent a second format.

The issue must include, where applicable:

- stable sequential ID and outcome-oriented title;
- `type: refactor`, status, priority, phase, and date according to repository convention;
- direct references to the relevant plan item, spec ID/version, PRD, architecture decision, and related issues;
- the verified structural problem and evidence from code, imports, dependency relationships, tests, or history;
- the intended target boundary, responsibilities to extract/move/consolidate, and affected components;
- explicit invariants: behavior, public contracts, routes, persistence, security, retries, concurrency, and compatibility that must remain unchanged;
- in-scope and explicitly out-of-scope work;
- an ordered implementation outline grounded in existing patterns, without speculative filenames or unrelated cleanup;
- required tests and validation commands, including browser/e2e/screenshot validation when the affected surface is frontend and the repository requires it;
- unchecked acceptance criteria proving structural improvement and behavioral equivalence;
- required documentation, Graphify, plan, and spec synchronization after implementation;
- the requirement to close the issue only after validation and one focused commit.

Acceptance criteria must verify both the intended structural result and the absence of behavior changes. Do not pre-check any criterion.

## Scope

May modify:

- exactly one new file under `issues/`;
- Graphify metadata only when the repository's established workflow requires it for that issue.

Must not:

- perform or partially perform the refactor;
- fix bugs, add features, change behavior, or redesign architecture;
- modify application code, tests, migrations, configuration, dependencies, infrastructure, specs, or `IMPLEMENTATION_PLAN.md`;
- create multiple issues;
- close, rewrite, or renumber existing issues;
- commit, tag, push, or open a pull request.

## Final verification and report

Before finishing, verify that the issue is structurally bounded, behavior-preserving, not a duplicate, references valid contracts, has unchecked acceptance criteria, and no prohibited file was changed.

Finish with exactly one status label followed by a concise report:

- `ISSUE_CREATED` — issue path, structural problem, target boundary, evidence, related contract/spec, priority, and dependencies.
- `ISSUES_COMPLETE` — why no uncovered, behavior-preserving refactor is eligible.
- `ISSUES_BLOCKED` — exact architectural, specification, decision, dependency, or evidence blockers and the candidates skipped.
