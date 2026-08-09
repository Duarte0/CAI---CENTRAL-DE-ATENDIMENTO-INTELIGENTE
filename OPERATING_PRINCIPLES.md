# Operating Principles

Shared rules for every `codex exec` pass (specs, plan, issues, build). Each pass prompt links here instead of repeating this content — read it before starting work.

## Non-interactive mode

This is a non-interactive execution. Complete all safe work without asking the user questions.

When information is incomplete:

- use established repository conventions for small, reversible details;
- derive decisions when supported by the PRD, architecture, specs, code, tests, or Git history;
- never invent: business rules, credentials, production values, destructive migration behavior, security exceptions, or irreversible/major architectural decisions;
- record unresolved material decisions explicitly (in the spec, plan, issue, or final report — whichever this pass owns);
- block only the directly affected item; continue all unaffected work;
- do not treat a historical approval note or document status label as a hard gate unless an authoritative project document explicitly requires it.

Do not assume subagents or interactive clarification are available. Work through consolidated, targeted repository inspection.

## Sources of truth

When sources disagree, resolve using this precedence, and record the discrepancy plus its impact wherever this pass's deliverable lives:

1. `AGENTS.md` and explicit repository instructions — govern *how* work is done.
2. Source code, migrations, config — what is *actually* implemented.
3. Tests — currently *verified* behavior (may be incomplete or stale).
4. `PRD.md` — intended product behavior and business requirements.
5. `ARCHITECTURE.md` — approved technical direction and constraints.
6. `specs/` — implementation contracts (may need correction).
7. `IMPLEMENTATION_PLAN.md` — sequencing and status (may be outdated).
8. `issues/` and Git history — supporting evidence only, not authoritative.

When sources conflict, determine whether it's (a) an intended future change, (b) stale documentation, (c) an implementation defect, or (d) an unresolved decision — then record the conclusion rather than silently picking a side.

## Repository inspection

Before editing anything:

1. Inspect the repo structure and working tree. Don't assume `src/`, `backend/`, or any fixed layout — find the real one.
2. Read every applicable `AGENTS.md`.
3. Read what's present and relevant: `README.md`, `PRD.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, `specs/README.md`, the spec/issue template, relevant specs/issues, recent Git history.
4. Search before concluding something is missing or done — confirm with a targeted grep/find, not assumption.

Preserve all pre-existing user changes. Never reset, revert, overwrite, or reformat unrelated work. If unrelated files are already modified, leave them untouched.

## File reading strategy

Match the read to the file's size — never load more than you need:

- **< 400 lines**: read directly.
- **400–1000 lines**: read the relevant section/range only, or request a summary + key snippets instead of the full body.
- **1000+ lines**: split into logical layers (e.g. by module/section) and read each layer separately; never dump the whole file into context at once.

Never re-read a file already in context — reuse what you have.

## Graphify

Use Graphify only when it's installed and the repo already supports it.

- Use it to navigate relationships between requirements, code, tests, and docs.
- Follow the repo's documented Graphify workflow — never invent commands.
- Confirm important findings against the actual files; Graphify informs, it doesn't replace inspection.
- Never fail a pass solely because Graphify is unavailable, stale, or incomplete.
- Update Graphify-managed metadata only when the repo's established workflow requires it for the files this pass touches.

## Scope discipline

Each pass has an explicit "may modify" list in its own prompt — treat it as exhaustive. Outside that list:

- do not implement application code unless the pass is `build`;
- do not create commits or tags unless the pass is `build`;
- do not discard or rewrite unrelated sections of a file you're allowed to touch;
- if a major inconsistency surfaces outside this pass's scope, document it for the right pass instead of fixing it here.

## Final verification (every pass)

Before finishing, confirm:

1. Every change traces to real evidence (a requirement, a spec, an issue, actual code) — not invention.
2. Cross-references, identifiers, statuses, and links are internally consistent.
3. No prohibited file was touched.
4. The diff contains no accidental or unrelated edits.

Then finish with the concise report format defined in the pass-specific prompt.