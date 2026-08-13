# Planning Pass

You are running the planning pass for this repository through `codex exec`.

Read `@OPERATING_PRINCIPLES.md` first — it defines non-interactive mode, sources-of-truth precedence, repository inspection, file reading strategy, Graphify usage, and final verification. This prompt covers only what's specific to planning.

## Deliverable

The only output is an accurate, prioritized, implementation-ready update to `IMPLEMENTATION_PLAN.md`.

Do not implement code, write specs, create issues, or commit.

## Required analysis

Determine, with targeted searches to confirm each conclusion (don't assume a missing filename means missing functionality):

1. what's fully implemented and verified;
2. what's implemented but under-tested or undocumented;
3. what's partially implemented;
4. what's specified but not implemented;
5. what's in the plan but already complete, obsolete, duplicated, or superseded;
6. which specs are missing, incomplete, stale, or inconsistent;
7. what migrations, infra, tests, docs, or operational work remain;
8. dependencies, risks, and unresolved decisions affecting sequencing;
9. what should come next, by priority/dependency/risk/value.

Search for TODOs, placeholders, skipped/flaky tests, temporary implementations, and duplicated logic as signal.

## Updating `IMPLEMENTATION_PLAN.md`

Update the existing file in place — never create a second plan file. If it doesn't exist yet, create it only when there's enough PRD/architecture/spec/implementation evidence to make it reliable; otherwise report the missing prerequisites and stop.

Preserve useful structure and terminology unless restructuring is needed for consistency. The plan must:

- reflect current implementation state, preserving relevant completed history;
- clearly distinguish `completed` / `in progress` / `pending` / `blocked`;
- organize remaining work into phases/milestones, ordered by dependency, priority, and risk;
- give each item a clear outcome and completion criteria, referencing the related spec (or flagging that one is needed);
- record dependencies, risks, contradictions, and open decisions;
- avoid duplicate work and vague items like "improve architecture" or "add tests";
- stay concise enough to maintain as the project evolves.

Reference specs and requirements — don't copy them in. When a spec changed after implementation, find the exact delta and check whether the code already satisfies it before marking anything pending. Don't reopen completed work without concrete evidence of a gap.

## Technical decisions

You may make and briefly document decisions that are supported by the approved architecture, necessary to make the plan executable, and reversible without real product/data risk.

You may NOT decide: business behavior not in the PRD/specs, destructive migration or rollout policy, security/compliance exceptions, credentials/secrets/production values, or major stack/architecture replacements. Record these as decisions required, with their impact, and keep planning unaffected work.

## Scope

May modify: `IMPLEMENTATION_PLAN.md`, Graphify metadata if required.

Must not: touch application code, tests, migrations, specs, issues, dependencies, infra config; refactor; commit or tag.

## Report

Finish with: files inspected at a high level; what changed in the plan; important discrepancies or decisions recorded; blockers needing user input; recommended next pass (`specs`, `issues`, or `build`).

If no change is needed, make none — explain why the current plan already matches reality.