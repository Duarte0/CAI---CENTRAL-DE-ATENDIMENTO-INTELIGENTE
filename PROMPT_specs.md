# Specification Pass

You are running the specification pass for this repository through `codex exec`.

Read `@OPERATING_PRINCIPLES.md` first — it defines non-interactive mode, sources-of-truth precedence, repository inspection, file reading strategy, Graphify usage, and final verification. This prompt covers only what's specific to specs.

## Deliverables

1. Create or update implementation specs under `specs/`.
2. Update `specs/README.md` so it accurately indexes the spec set.
3. Update `IMPLEMENTATION_PLAN.md` only to reference resulting specs, record remaining spec gaps, or flag work now ready for the issues pass.

Do not implement application code, create issue files, or commit.

## Determine the required specification set

Compare PRD, architecture, plan, existing specs, code, tests, and history. Identify:

- planned work with no spec;
- specs that don't cover all requirements assigned to them, are too vague to implement/verify, or contradict the PRD/architecture/another spec;
- specs whose implemented behavior changed and need a precise delta;
- implemented behavior future work depends on but isn't documented;
- duplicated specs that should reference one canonical contract;
- obsolete specs to mark clearly (without destroying history);
- missing cross-cutting concerns: data integrity, idempotency, security, failure handling, observability, migrations, compatibility, test isolation.

Confirm every gap with a targeted search before writing anything. Don't create a spec for a hypothetical future feature — every new spec must trace to an approved requirement, architecture decision, plan item, or concrete undocumented behavior that pending work depends on.

## Writing specs

Follow the repo's existing template/conventions. Choose boundaries that map to coherent, independently implementable slices — not one giant phase-spec, not dozens of file-level specs.

Each spec must be precise enough for a developer to implement and verify without rediscovering intent. Include, where relevant: identifier/title/status/version, related PRD/architecture/plan/spec references, objective and non-goals, current-state context, functional requirements and business rules, technical/data/API contracts, validation/error/retry/idempotency/concurrency behavior, security/privacy/observability/compatibility requirements, required tests, explicit acceptance criteria, and open decisions.

Use normative language (`must`, `must not`) over vague language (`should handle correctly`). Don't prescribe file-by-file implementation steps unless the location is itself part of the approved contract — that decomposition belongs to the issues pass.

**Keep specs DRY**: reference a canonical stack/architecture/shared-contract spec instead of duplicating it; repeat a rule only when local context changes its meaning.

**Specs are binding contracts.** When code and spec differ:
- spec is the approved intent → keep the requirement, flag the implementation gap;
- spec is proven stale by authoritative docs → update it and state why;
- unresolved → preserve the existing contract and record the decision needed;
- partial delta → describe the exact delta, don't reopen the whole spec.

Never weaken, remove, or reinterpret an approved requirement just to match current code. Don't claim a spec is implemented just because related files exist — verify behavior and tests.

## Update `specs/README.md`

In place; create it if `specs/` exists but the index doesn't. It must: list every active spec once, use real repo naming/ordering conventions, summarize each spec's purpose, show status/priority/phase/version/dependencies where that's convention, link with relative paths, distinguish active/superseded/deprecated/template files, and explain the spec → issues → implementation workflow. No stale or broken entries. Don't let it become a second implementation plan.

## Synchronize `IMPLEMENTATION_PLAN.md`

Minimum changes only: add/correct spec references, mark previously-missing spec work as done, record remaining gaps, flag items now ready for issues, update a version/delta when required. Don't rewrite priorities, phases, or status wholesale — if you find a major planning inconsistency, document it for the next planning pass instead of redesigning it here.

## Scope

May modify: files under `specs/` (including the template and README), `IMPLEMENTATION_PLAN.md` for the limited sync above, Graphify metadata if required.

Must not: touch application code, tests, migrations, runtime config, issue files, dependencies, infrastructure; implement anything; commit or tag.

## Report

Finish with: specs created/updated/superseded/unchanged; important requirement or code divergences found; unresolved decisions and exactly what they block; changes made to `specs/README.md` and `IMPLEMENTATION_PLAN.md`; whether the repo is ready for the `issues` pass.

If no changes are needed, make none — explain why the existing spec set is already complete and consistent.