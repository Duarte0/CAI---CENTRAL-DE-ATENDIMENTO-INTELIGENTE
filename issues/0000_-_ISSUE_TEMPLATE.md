---
id: NNNN
title: "Issue Title"
type: feature          # bug | feature | refactor | spec
status: open           # open | in-progress | closed
priority: medium       # critical | high | medium | low
created_at: YYYY-MM-DD
updated_at: YYYY-MM-DD
closed_at: ~           # fill when status → closed
related_issues: []     # []
blocked_by: []         # [] — issues that must close first
affects:               # primary paths touched
  - src/
---

## Description
<!-- What and why. Ground in `specs/`. Be direct — do not restate the title. -->

<!-- If type=bug, also fill in: -->
<!--
**Root cause:** precise technical description of the root cause.
**Reproduction:**
1. ...
2. ...
**Actual behaviour:** ...
**Expected behaviour:** ...
-->

## Implementation Plan
<!-- Concrete, ordered steps. Be specific about files, tables, and services (Supabase, Stripe, R2). -->
<!-- These are execution instructions, not acceptance criteria. -->

1. ...
2. ...
3. ...

## Tests
<!-- Tests that cover this issue. Write them before implementing (TDD). -->
<!-- For frontend features: include the e2e test path. -->

- **Unit:** `src/__tests__/...`
- **E2E:** `e2e/...`

## Acceptance Criteria
<!-- Verifiable checkboxes. Each item must be individually testable. -->
<!-- The agent closes this issue only when ALL items are ✅. -->

- [ ] ...
- [ ] No build, lint, or type-check errors.
- [ ] All tests pass (unit + e2e if frontend).

## References
<!-- Relevant specs, entities, PRs, or external documentation. -->

- Spec: `specs/`
- Entity: `specs/entities.md`

---

## Resolution
<!-- Filled by the agent on close. DO NOT edit manually. -->
<!-- What was done, decisions made, and why. -->
<!-- Include: files modified, tests added, edge cases handled. -->