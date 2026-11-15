# CLAUDE.md — DailyDairyRegister Repository Instructions

This file is the repository-level working contract for Claude Code and other implementation agents. Apply it to every task in this repository unless the repository owner explicitly authorizes a narrowly scoped exception.

## 1. Product authority and source of truth

- Treat `documentation/PRODUCT_REQUIREMENTS_BASELINE.md` as the consolidated product-requirements baseline.
- Read the relevant requirements before designing or changing related behavior.
- A requirement recorded in documentation is not proof that it has been implemented, wired, enforced, tested or accepted. Report these states separately.
- Preserve settled owner decisions. Reopen one only when new evidence demonstrates a real contradiction, defect or newly stated requirement.
- If a new owner decision changes the baseline, update the baseline as part of the same bounded act as the related implementation or validation, unless the owner explicitly authorizes a separate requirements-only act.
- Do not silently turn proposals, examples, inferred values or unresolved questions into approved product defaults. Ask the owner when a decision is required.
- Keep one authoritative source for each rule. Avoid duplicated configuration and parallel sources of truth that can drift.

## 2. Mandatory changelog rule — no exceptions for small changes

**Every repository change, however small, MUST have a corresponding entry in `CHANGELOG.md`.**

This includes, without limitation:
- application code and behavior changes;
- bug fixes and refactoring;
- tests, fixtures and validation tooling;
- configuration, dependency and build changes;
- database schema and migration changes;
- user-interface, styling and accessibility changes;
- documentation, requirements, examples and repository governance changes;
- additions, removals, renames and file-structure changes;
- small typo fixes and one-line edits;
- changes to `CLAUDE.md` or `CHANGELOG.md` itself.

Rules for applying this requirement:
1. Update `CHANGELOG.md` in the **same commit** as the change it records. Do not leave a change awaiting a later changelog catch-up.
2. Describe what changed, why it changed, and the user-visible or engineering impact. Keep the entry proportional to the change, but never omit it because the change is small.
3. Group related files in one entry when they form one coherent change. Do not create meaningless noise entries for each file when one entry accurately describes the bounded change.
4. Use an `Unreleased` section for work not yet part of a release. Organize entries under suitable headings such as Added, Changed, Fixed, Removed, Security or Engineering.
5. Do not claim a capability is complete merely because its documentation or changelog entry exists.
6. Before committing, mechanically inspect the staged diff and verify that the corresponding changelog entry is staged in the same commit. If not, do not commit until corrected.
7. If a commit hook requires changelog synchronization, that synchronization is authorized when it is required to complete the already-authorized change and remains within that change's scope.
8. Changelog entries must be factual. Include validation evidence when relevant; distinguish implemented behavior from planned work and requirements-only changes.

The initial repository bootstrap is recorded retrospectively in the first `CHANGELOG.md` commit because the changelog file did not exist when the preceding bootstrap commits were made. From that point onward, the same-commit rule is mandatory.

## 3. Architecture and implementation principles

Prefer, in genuine design choices:
- maintainability and scalability;
- configurability and low migration cost;
- architectural consistency and low coupling;
- a single source of truth;
- drift resistance;
- mechanically testable and enforceable contracts;
- explicit data ownership, provenance and lifecycle.

Avoid short-term convenience that creates hidden coupling, duplicated rules, implicit behavior or expensive migrations.

Additional constraints:
- Frontend direction is HTML, CSS and vanilla JavaScript. Do not introduce React.
- Backend direction is Python with FastAPI.
- SQLite is the application data store.
- PDF output uses browser HTML/CSS print rendering.
- XLSX is the spreadsheet interchange/output format.
- The application is local-first and localhost-only. Prefer offline operation.
- Do not introduce a network service, hosted dependency or multi-user architecture without demonstrating why the requirement cannot reasonably be met locally and obtaining owner approval where the product scope changes.

## 4. Data integrity and operational truth

- Generated register entries are **draft suggestions**, not verified evidence that work occurred.
- Preserve imported source values and their provenance. Store canonical corrections/overrides separately; never silently rewrite source facts.
- Preserve missing, blank and sentinel source values such as `#N/A`; surface data-quality issues for review rather than fabricating or silently dropping data.
- Never silently discard conflicting calendar, leave, event, inspection or register information.
- Finalised register revisions are immutable. Corrections create a new revision.
- Keep operational register data and office-specific working data out of Git by default. Commit source code and non-sensitive seed/reference data only when explicitly appropriate.
- Backup and restore must preserve referential integrity and revision history.

## 5. Scheduling and generation invariants

- Treat configured minimums, maximums, revisit gaps and daily limits as hard constraints unless the owner explicitly changes the contract.
- If constraints cannot all be satisfied, report infeasibility and the constraints responsible. Never silently relax them.
- Keep weighted preference separate from hard constraints. A weight influences relative allocation; it does not override a minimum, maximum, revisit interval or daily cap.
- Preserve deterministic generation using the stored configuration, seed and generator version. Alternative drafts may use a new seed.
- Re-generation must preserve locked dates/entries and present a comparison before acceptance.
- Keep regular activity quotas separate from safety meetings, dated events and field visits, as defined by the requirements baseline.
- Do not implement target suggestions using invented fixed values. The target-suggestion policy must be explicitly designed and validated against the combined constraints before it controls generation.

## 6. Calendar semantics

- Only Karnataka General Holidays are operational holidays for the initial Shivamogga scope.
- Restricted Holidays are ordinary working days and must be ignored entirely, including their names.
- Do not import Kodagu-district local holidays into the Shivamogga calendar.
- Preserve overlapping reasons (for example, a General Holiday falling on a weekly off).
- Date overrides must not erase the underlying source calendar fact or silently delete leave/events.
- Apply weekly-off, holiday and leave eligibility rules exactly as defined in the requirements baseline.

## 7. Change authorization and execution boundaries

For each implementation act:
1. State the exact capability or defect being addressed.
2. Identify the authorized files/components and keep the diff bounded.
3. Inspect existing code and tests before changing behavior.
4. Preserve settled contracts; do not perform unrelated cleanup or speculative redesign.
5. Implement the capability, its tests and any required documentation/changelog synchronization together.
6. Run relevant tests and validation. Do not claim tests passed unless they were actually run.
7. Review the final diff for scope, regressions, accidental operational data and changelog coverage.
8. Report what was delivered, what validation ran, what remains, and the next implementation this act unlocks.

Do not treat a governance decision, written requirement, design proposal, implementation, enforcement, validation and acceptance as interchangeable states.

## 8. Testing and quality

- Add or update tests with behavior changes.
- Prefer deterministic tests for scheduling, calendar calculations, weighting, revision lifecycle and data import.
- Test boundary conditions, conflicting inputs, infeasible constraints and partial-month ranges.
- Test invariants at the layer that owns them; do not rely solely on UI validation for domain integrity.
- Avoid tests that merely mirror implementation details when a contract-level assertion is possible.
- Do not weaken or delete a test simply to make a change pass. Explain any justified test-contract change.

## 9. Git and commit discipline

- Keep commits focused on one coherent, authorized act.
- Before committing, inspect `git status`, staged paths and the complete staged diff.
- Ensure `CHANGELOG.md` is updated and staged in the same commit for every change, without exception.
- Never commit real daily registers, personal data, credentials, tokens, local databases or machine-specific secrets.
- Do not force-push, rewrite shared history, or push to a different branch without explicit authorization.
- Commit messages must be detailed, explanatory and specific. Explain:
  - what changed and why it was needed;
  - how the change works or what contract it establishes;
  - when or under which workflow/transition it applies;
  - what remains unchanged or was intentionally not done;
  - the authorization/scope boundary;
  - validation performed and its evidence;
  - rollback or future-transition context where relevant.
- Do not claim a commit is pushed unless the push actually succeeded.

## 10. Reporting

For each completed act, report:
- delivered capability or recorded decision;
- exact files changed;
- tests/validation run and results;
- commit SHA and branch, if committed;
- whether changes were pushed;
- remaining items classified as prerequisite/dependency/validation, independent follow-up, optional improvement or accepted limitation;
- the next implementation that is now unblocked.

Keep reports precise. Do not overstate implementation maturity or completion.
