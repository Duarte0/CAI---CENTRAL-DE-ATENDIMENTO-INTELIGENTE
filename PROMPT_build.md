# Build Pass

You are running one implementation iteration for this repository through `codex exec`.

Read `@OPERATING_PRINCIPLES.md` first — it defines non-interactive mode, sources-of-truth precedence, repository inspection, file reading strategy, Graphify usage, and final verification. This prompt covers only what's specific to building.

## Deliverable

Select one eligible open issue, implement it completely, validate it, sync its docs, close it only when acceptance criteria are met, and create one focused commit. Implement exactly one issue this iteration.

Never use destructive Git commands, real production credentials, or destructive migrations against non-ephemeral data.

## Safety with existing work

Inspect the working tree first and record pre-existing changes. Don't refuse to work just because the tree is dirty — work around unrelated changes safely. If pre-existing changes overlap the files this issue needs and can't be separated safely, leave the issue open and finish `BUILD_BLOCKED`.

## Selecting one issue

Order: satisfied dependencies before blocked dependents → priority (critical → high → medium → low) → earlier plan phase before later at equal priority → lowest numeric ID as tiebreaker.

Eligible only when: status is open; all blockers/prerequisites are closed; referenced specs are sufficient to implement safely; no unresolved material product/architecture decision is required; achievable as one coherent increment.

If blocked by another open issue, apply the same selection rules to the blocker; otherwise skip and check the next. No open issues → `BUILD_COMPLETE`, no changes. Open issues exist but none eligible → `BUILD_BLOCKED` with exact blockers listed.

## Understand the contract

Read fully: the issue, every spec it references, relevant `IMPLEMENTATION_PLAN.md`/PRD/architecture sections, related source/tests/migrations/config/docs, and relevant recent history.

Before touching code, confirm with targeted searches: what's already implemented, what's missing, which tests cover it, which interfaces/callers/downstream components are affected, and what the repo's actual build/lint/typecheck/migration/test commands are. Don't assume a framework, layout, or package manager — use what's actually there.

## Dependencies and third-party docs

Prefer versions already in manifests/lockfiles. When integrating a library or service: check the installed package, local types, and existing adapters first; consult current official docs when more info is needed and network access is available; don't rely on guessed APIs when a primary source exists; don't upgrade or add a dependency the issue doesn't require; don't auto-pick the newest version; keep the lockfile consistent.

## Baseline and TDD

Run the smallest relevant existing test/validation set before implementing. Record pre-existing failures that affect interpretation — don't expand scope to fix unrelated ones.

For a defect or behavior change, write a test that fails for the intended reason *before* implementing the fix. For new foundational work, define expected behavior via tests as early as the repo structure allows. Tests must validate behavior, not mirror implementation details.

## Implementing

Stay within the issue's scope. Follow approved architecture and existing patterns. Prefer one canonical implementation over duplicate adapters/migrations/compat layers. Preserve backward compatibility when the spec requires it. Include required migrations, config examples, error handling, idempotency, concurrency controls, observability, and docs where applicable. Handle real failure paths and edge cases.

Never: leave placeholders, stubs, commented-out code, fake success paths, or unexplained TODOs; do opportunistic refactors or unrelated cleanup; silently weaken a spec to make implementation easier; change a public contract without explicit authority in the issue/spec.

Temporary diagnostic logging is fine while debugging — remove it before completion unless it's intentional operational logging the issue calls for.

If the issue can't be completed safely in scope, keep it open, preserve sound partial work, document what remains, and finish `BUILD_BLOCKED`. Never mark partial work complete.

## Problems outside the issue

Don't fix them unless they directly block this issue and the fix is small and spec-consistent. Don't create a new issue during build. If it materially affects future work, note it briefly in `IMPLEMENTATION_PLAN.md`; otherwise just report it for a later `specs`/`issues` pass.

For unrelated failing tests: if this iteration caused the regression, fix it. If it's demonstrably pre-existing, leave it and record the evidence — unless repo policy requires the full suite green before closing, in which case keep the issue open until that's met or explicitly waived.

## Validating

Run what's appropriate to the change and repo conventions: targeted tests, regression tests, lint/format, static analysis/typecheck, build/packaging, migration validation against an isolated dev/test DB, e2e tests for changed flows, safe Docker/service-level checks. Run the broader suite after targeted validation when feasible.

For frontend changes, use the repo's established browser/e2e/screenshot workflow if one exists. Cover loading/empty/success/error/responsive states where relevant. Don't hardcode screenshots to external paths or commit them unless the repo explicitly requires it.

Never claim a command passed without having run it and observed success. Never point tests or migrations at production.

## Syncing documentation

Update the selected spec's implementation status/version/notes per its convention — without rewriting approved requirements to match the implementation. Keep `specs/README.md` accurate. Update `IMPLEMENTATION_PLAN.md` to reflect the exact completed increment and any real remaining work, preserving history unless the doc's convention prunes it. Update other docs the issue requires, and Graphify via the repo's workflow.

## Closing the issue

Close only when every acceptance criterion is met or explicitly marked N/A with a documented, spec-permitted reason. Then: check off completed criteria, add a `## Resolution` section (implementation, tests, migrations, docs, key decisions), record validation commands run and their results, set status to the repo's closed value (normally `closed`).

If anything required remains incomplete, keep it open and state exactly what's left. Never close based only on code existing.

## One focused commit

After the issue is complete and validated: inspect the full diff; stage only the files belonging to this issue (implementation, tests, required doc/Graphify sync) — never `git add -A` or `git add .`; verify no unrelated or secret files are staged; commit once, referencing the issue ID and title. Don't amend, tag, push, or open a PR.

If a Git hook fails because of the implementation, fix it and revalidate. If the commit can't be created due to environment/permissions/signing, keep the issue open, leave the work in the tree, and finish `BUILD_BLOCKED`. Never bypass required hooks or security controls.

## Scope

May modify only what's needed for: the selected issue's implementation, its tests/migrations, its directly related specs and docs, `specs/README.md`, the relevant `IMPLEMENTATION_PLAN.md` entries, the issue file itself, required Graphify metadata.

Must not: implement another issue; create new issues; make unrelated architecture/dependency changes; fix unrelated bugs/tests; rewrite unrelated plan/spec sections; add status notes to `AGENTS.md`; create tags; touch remotes.

## Report

Finish with exactly one status label, then a concise report:

- `BUILD_COMPLETED` — issue, implementation summary, validation performed, docs updated, commit hash.
- `BUILD_BLOCKED` — issue, completed work, exact blocker, failed/unavailable validation, next required action.
- `BUILD_COMPLETE` — confirm no open issues remain.