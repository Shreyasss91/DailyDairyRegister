# DailyDairyRegister — Product Requirements Baseline

**Status:** Consolidated requirements baseline for owner review  
**Repository:** `Shreyasss91/DailyDairyRegister`  
**Scope of this change:** Requirements capture only. This commit does not authorize or implement the application.

## 1. Product purpose

DailyDairyRegister is a local Windows application for preparing a date-ranged daily work register for a MESCOM O&M Section Assistant Engineer. It generates a **draft** register from configurable masters, calendars and scheduling constraints. The officer must review the generated entries, correct them where necessary, and explicitly finalise each month.

The application must never imply that a generated suggestion is proof that work was actually performed. Generated entries remain draft suggestions until reviewed and finalised by the user.

## 2. Platform and technical direction

The agreed direction is:

- Run locally on Windows and bind to localhost; no public hosting or multi-user service is required.
- Prefer offline operation. An online dependency is acceptable only if a required capability would otherwise become disproportionately complex.
- Frontend: HTML, CSS and vanilla JavaScript. React is not wanted.
- Backend direction: Python with FastAPI.
- Primary data store: SQLite.
- PDF: browser HTML/CSS print rendering.
- Excel: XLSX import/export.
- Manual full-application-data backup and restore are required. Automatic backups are not required.
- Git tracks source code, not real operational registers or personal/office data.
- SQLite stores internal register revisions and application data. This revision history is distinct from Git history.

These are the selected technology directions, not evidence that any implementation exists.

## 3. Office identity and register layout

Default office identity:

- Utility: MESCOM (utility identity should be configurable)
- Designation: Assistant Engineer
- Office: O&M Unit-4, CSD2, SMG

The signature block appears at the bottom of the final printed page of each monthly section.

## 4. Date range and calendar model

- The user selects From Date and To Date.
- Every calendar date in the selected range appears in the register, including Sundays, weekly offs, General Holidays and leave dates.
- For a range spanning multiple months, produce one combined PDF with separate monthly sections. A partial first or last month contains only the selected dates; intervening months contain all dates.
- Excel output is one workbook per month.
- Weekly-off defaults: every Sunday and the second and fourth Saturday. These settings must be configurable.
- Calendar rules must be date-based and deterministic.

### 4.1 Holidays

- Only Karnataka **General Holidays** count as operational holidays.
- Restricted Holidays are ordinary working days. Ignore Restricted Holiday dates and names entirely: do not import, display, classify or generate entries for them.
- Initial General Holiday baseline coverage: calendar years 2025 and 2026.
- The user-supplied holiday list replaces the Karnataka default list by default. An explicit Merge mode is also available.
- Do not apply Kodagu-district local holidays to the Shivamogga section.
- If a General Holiday overlaps a weekly off, show both reasons.
- Explicit date overrides can turn a General Holiday or weekly off into a working day, or an ordinary working day into a holiday.
- An override must not silently erase an underlying holiday/weekly-off reason or a leave record. Preserve source facts and represent the effective calendar classification separately.
- Conflicting calendar, leave and event facts must be surfaced as warnings for user resolution; never silently discard one.

Holiday register wording:
- Republic Day, Independence Day and Gandhi Jayanti: exact pattern `Celebrated [festival] in office`.
- Other General Jayanthi holidays: `Celebrated [Person] Jayanthi in office`.
- Other General Holidays: `Holiday – [Holiday name]`, unless the user supplies a specific description.
- Restricted Jayanthis are ignored along with all other Restricted Holidays.

### 4.2 Monday safety meeting

- Include the safety meeting every Monday, including a Monday that is a holiday or leave date.
- Default exact wording: `Conducted safety meeting for employees`.
- Permit an individual-date editable exception.
- This entry does not consume the regular weighted-activity quota.

## 5. Leave and dated events

### 5.1 Leave

- Initial leave types: Casual Leave and Full Pay Leave.
- Additional leave types must be configurable.
- No half-day leave in the initial scope.
- Holidays and weekly offs are excluded from leave-day calculations.
- Leave entries remain recorded when they overlap a holiday, weekly off, event or other special entry; show a conflict rather than silently removing data.

### 5.2 Dated events and meetings

- Users enter event dates individually; recurring event rules are not required initially.
- Multiple dated events may occur on the same date.
- Every entered event must appear in the main register.
- The app may rewrite/paraphrase event wording.
- A separate dated-event calendar view is useful.
- Events and meetings do not consume the regular weighted-activity quota.
- Preserve an event on a leave/holiday date and show a conflict warning.
- Example event types include safety workshops at Division Office, school safety-awareness programmes, monthly review meetings, subdivision meetings, Jana Samparka Sabhe and training sessions.

## 6. Regular activity generation

- Preload approximately 40–60 distinct MESCOM/BESCOM O&M section-level activities spanning technical O&M, consumer services, revenue, safety, administration, inspections and works.
- The user can edit, add, deactivate and configure activities.
- Each activity should support: category, English description, Kannada description, weight, monthly minimum and maximum frequency, minimum repeat gap, active/inactive status and (where useful) weekday restrictions.
- Default daily count: 3–6 regular activities on each eligible working day. This range is configurable.
- Generate regular weighted activities only on working days: exclude General Holidays, weekly offs and leave.
- Monday safety meetings, dated events/meetings, area visits, DTC visits and feeder visits are additional entry types and do not consume this regular-activity quota.
- Scheduling must use controlled weighted distribution, honour monthly minimum/maximum frequency, spread occurrences through the month, prevent same-day duplicates unless explicitly permitted, and respect minimum repeat gaps.
- Monthly min/max limits are prorated for partial months by eligible working days. Show the prorated limits before generation.
- Minimum repeat gap default: 2 working days; configurable.
- Constraints are strict. If a schedule is infeasible, report the infeasibility and the blocking constraints. Do not silently violate minimums, maximums, gaps or daily limits.
- Support deterministic regeneration from a saved seed and configuration, and an alternative draft generated with a new seed.

## 7. Area master and area visits

The initial supplied Kannada list contains 21 names. The owner confirmed five pairs are aliases for the same canonical area, resulting in **16 canonical areas**:

1. ಗಾಂಧಿಬಜಾರ್ (alias: ಗಾಂಧಿ ಬಜಾರ್)
2. ಲಷ್ಕರ್ ಮೊಹಲ್ಲಾ
3. ಸಾವರ್ಕರ್ ನಗರ
4. ಬಿ.ಎಚ್. ರಸ್ತೆ
5. ತಿರುಪಳಯ್ಯನ ಕೇರಿ (alias: ತಿರುಪಲ್ಯ ಕೇರಿ)
6. ಸೊಪ್ಪಿನ ಮಾರ್ಕೆಟ್
7. ಕೆ. ಆರ್. ಪುರಂ (alias: KR ಪುರಂ)
8. ಭರಮಪ್ಪ ನಗರ (alias: ಭರ್ಮಪ್ಪ ನಗರ)
9. ಎಂ.ಕೆ.ಕೆ. ಕೆ ರಸ್ತೆ (alias: MKK ರಸ್ತೆ)
10. ನಾಗಪ್ಪ ಕೇರಿ
11. OT ರಸ್ತೆ
12. ಎಲ್ಲೆರೆವನ ಕೇರಿ
13. ಕುಂಬಾರ ಗುಂಡಿ
14. ಸಿದ್ದಯ್ಯ ರಸ್ತೆ ತಿಮ್ಮಪ್ಪನಕೊಪ್ಪಲು
15. ಉಪ್ಪಾರ ಕೇರಿ
16. ಕಸ್ತೂರ್ಬಾ ರಸ್ತೆ

Area master fields: stable canonical ID, Kannada name, editable English transliteration/name, aliases, weight, monthly minimum/maximum visits and active/inactive status.

- Initial area weights are equal; user can edit them.
- Monthly minimum/maximum area visits are configurable and strict; prorate for partial months.
- Maximum two area visits per day initially; configurable.
- Area minimum revisit interval default: 7 calendar days; configurable.
- Multiple areas on a date are allowed within the daily cap.
- Area visits are field visits and count toward the combined daily field-visit limit.
- Area visits occur only on working days.
- DTC inspection is independent and does not count as an area visit.
- DTC-to-area mapping is explicitly deferred from the initial scope.

## 8. DTC master, source data and scheduling

The supplied DTC source contains 113 SCD records. Include the 113 SCD records; exclude five SCF and one SCE records. The 113 SCD records comprise 68 DEPT and 45 SELF_DTC records (42 MSB + 3 OWN). MSB and OWN are one scheduling class, `SELF_DTC`.

- DTC identifier: SCD-xx.
- Display name: source `DTC_Name`.
- Retain imported source fields, including DTC name, feeder, feeder code, MDM code, kVA, station and original DEPT/MSB/OWN classification.
- Preserve blank and `#N/A` source values; do not silently discard records or fabricate missing values.
- Preserve original imported values and store owner-confirmed feeder assignments as explicit canonical overrides. Do not overwrite source data or silently repair discrepancies.
- Source import must include a validation/reconciliation view so discrepancies and missing fields can be reviewed.

Owner-confirmed canonical feeder assignments:
- SCD-5 → MRS F-1 OLDTOWN
- SCD-12 → Mandli F-22 GANDHIBAZAAR
- SCD-107, SCD-108, SCD-109 and SCD-110 → MRS F-1 OLDTOWN

DTC priority and weight:
- DEPT has higher priority.
- MSB and OWN share the lower-priority SELF_DTC category.
- Higher-capacity DTCs receive higher weight.
- Formula: `W_DTC = kVA × class_multiplier × individual_adjustment`.
- Default class multipliers: DEPT = 1.00; SELF_DTC = 0.20.
- Class multipliers are editable.
- Individual DTC adjustment is a multiplier, not an absolute replacement weight.
- Weight determines relative frequency; each DTC also supports configurable monthly minimum/maximum visit limits.
- DTC minimum revisit interval default: 7 calendar days; configurable.
- DTC visits occur only on working days.
- A configurable overall monthly DTC target is supported.
- DTC visits count as field visits but not area visits.

## 9. Feeder master, weighting and scheduling

Initial feeder universe:
1. MRS F-1 OLDTOWN
2. Mandli F-13 MANDLI BYEPASS
3. Mandli F-22 GANDHIBAZAAR
4. Mandli F-3 NT ROAD

Feeder visits are independent of area and DTC visits.

Feeder weight is derived from the aggregate installed capacity of active DEPT DTCs assigned to that feeder:

`W_f = sum(kVA of active DEPT DTCs assigned to feeder f)`

- Exclude MSB and OWN / SELF_DTC records from feeder weight.
- Recalculate automatically when DTC kVA, class, canonical feeder assignment or active status changes.
- Disabled DTCs are excluded.
- Allow a manual feeder adjustment as a multiplier, not an absolute replacement, so the calculated capacity remains the source of truth.
- Preserve imported feeder values separately from canonical assignments and their provenance.
- Feeder monthly minimum/maximum inspection frequency is configurable, strict and prorated for partial months.
- Feeder minimum revisit interval default: 7 calendar days; configurable.
- A configurable overall monthly feeder target is supported.
- Feeder visits occur only on working days.
- Feeder visits count as field visits.

Previously calculated feeder aggregates were provisional and used pre-correction assumptions. They must be recalculated from the validated canonical DTC data; do not treat old totals as authoritative.

## 10. Combined field-visit constraints and target policy

### 10.1 Shared field-visit constraints

- Area, DTC and feeder visits share one configurable combined daily field-visit limit.
- Initial default: **3 combined field visits per working day** across area visits, DTC visits and feeder visits. This is a daily aggregate cap and remains configurable.
- Area-specific maximum: 2 area visits per working day initially, configurable.
- All three visit types occur only on working days.
- The minimum revisit interval for an individual area, DTC or feeder remains **7 calendar days**, as established in §§7–9. The next visit to the same asset must be at least 7 calendar days after its preceding visit. Do not reinterpret this as 7 working days.
- Unless an owner-configured value overrides it, the initial default monthly maximum is **3 visits per individual asset per calendar month** for areas, DTCs and feeders. This is a separate per-asset monthly cap, not another expression of the combined daily cap. Both constraints apply independently; a tighter constraint from the calendar-day revisit interval or an explicit owner setting takes precedence.
- Per-asset monthly minimums and maximums remain configurable. A configured maximum may be lower or higher than the initial default. The generator must enforce the effective maximum together with the revisit interval and daily caps.
- Monthly limits are prorated for partial months by eligible working days, with the prorated values shown before generation.
- Before generation, validate combined feasibility across monthly targets, per-asset limits, calendar-day revisit intervals, per-day limits and actual eligible working dates. Explain infeasibility rather than silently relaxing a constraint.

### 10.2 Primary DTC target mode — owner-declared coverage cycle

- The primary DTC target mode is an **owner-declared coverage cycle**. The owner configures both the cycle duration and the required number of visits to each active DTC during that cycle. Example: visit every active DTC once in each three-month cycle.
- The system derives the total cycle obligation from the active DTC roster and the configured per-DTC cycle requirement. It must not default to visiting all 113 canonical DTCs every month.
- For 113 active DTCs, with each DTC required once per three-month cycle, the cycle contains 113 required visits. A balanced monthly allocation is 38, 38 and 37 visits. Where months have materially different eligible working-day capacity, distribute the monthly quotas in a capacity-aware manner while preserving the exact cycle total and every per-DTC obligation.
- Coverage means that each active DTC in the applicable cycle roster receives its required number of visits by the end of the cycle. A monthly scalar target alone is insufficient: retain per-DTC cycle obligations and completion state so that one DTC is not repeatedly selected while another is omitted.
- DTC weights continue to matter. In cycle mode, where every active DTC has the same per-cycle visit obligation, weights influence visit priority/order and placement within the cycle, not the total number of visits owed to each DTC. In fallback mode, weights influence relative visit allocation.
- Cycle boundaries do not reset the 7-calendar-day revisit interval. The scheduler must carry the last-visit date across adjacent cycles and must not place a new-cycle visit inside the prohibited interval following a prior-cycle visit.
- The cycle roster, coverage obligation, weight/configuration snapshot, actual visit history and cycle progress must be retained in the generated revision so the result can be explained and reproduced.
- If the requested cycle cannot be satisfied under the combined daily cap, the per-asset monthly maximum, the 7-calendar-day revisit interval, the eligible working dates or other configured commitments, report the specific infeasibility. Do not automatically relax the cycle, revisit interval or daily cap.

### 10.3 DTC fallback mode — no coverage cycle declared

- If the owner has not declared a DTC coverage cycle, use a feasibility-bounded, weight-informed monthly target as a fallback.
- Calculate the feasible range from the actual working dates in the month, combined daily capacity, configured area/feeder commitments, per-asset monthly maxima and the 7-calendar-day revisit interval. The monthly target recommendation must not exceed the date-level feasible capacity.
- Allocate the fallback target through a deterministic weighted-fair rotation. Use prior visit history/least-recently-visited state as part of selection so that repeated generations do not systematically favour the same DTCs.
- Label this recommendation as a **capacity-based monthly suggestion**, not as a coverage commitment. Without a declared cycle, the application must explicitly state that it does not guarantee that every active DTC will be visited within a specified period.
- Display the feasible range, recommended target, active roster size, and the constraints that limit the recommendation. The user may edit the target, but the feasibility validator remains authoritative.
- Do not silently convert the fallback into an assumed one-month, one-quarter or other full-roster coverage promise.

### 10.4 Area and feeder target policy

- Area and feeder monthly targets remain configurable and feasibility-checked.
- No owner-approved default currently requires every one of the 16 canonical areas or all 4 feeders to be visited once every month. Do not infer those minimums from the asset roster size.
- Where the owner has configured per-asset minimums, maximums or a coverage cadence, the target allocator must honour them. Where these have not been configured, show the feasible range and the basis of any suggested target; do not present an assumed full-roster monthly visit as an approved requirement.
- Area, DTC and feeder target recommendations must be validated jointly against the shared daily cap. Do not allocate each visit category independently and then assume the resulting category schedules can be added without date-level packing.

### 10.5 Feasibility and calendar semantics

- Let `W` denote the number of eligible working dates in the selected month and `C` the combined daily field-visit cap (initially 3). `W × C` is a gross aggregate upper bound on field-visit slots, not a sufficient proof that a particular target is feasible.
- Exact feasibility must be evaluated against actual dates. Each asset's visit dates must satisfy the 7-calendar-day interval, the effective per-asset monthly maximum, working-day eligibility and any asset-specific restrictions. All asset types also share the same daily cap.
- A seven-working-day spacing grid must not be substituted for the settled seven-calendar-day minimum revisit interval. Working-day spacing may be used as a distribution/fairness heuristic only if the hard calendar-day rule is still enforced.
- Any proposed lower bound that sums separate area, DTC and feeder day counts must prove that those category visits cannot be packed on the same date under the combined cap. The shared cap permits different visit types to occur on the same date, subject to the total of 3 and the area-specific maximum of 2; therefore, category-specific day counts cannot simply be added without a valid non-overlap proof.
- The previously proposed analytical bound and sample table are not yet accepted as a verified feasibility theorem. Before the scheduler uses them, define each term and unit precisely, establish which per-asset minimums/coverage obligations are actually configured, account for date-level overlap among categories, and validate the result against deterministic calendar-based test cases.

## 11. Language and text storage

- Support English, Kannada and bilingual modes.
- User can select English-only, Kannada-only or bilingual output.
- Bilingual mode places English first and Kannada directly below it in the same cell for each corresponding entry.
- Store both language descriptions in master records.
- Preload Kannada translations for user review/editing.
- Do not perform runtime machine translation during final PDF generation.

## 12. Output requirements

- PDF and Excel are both required.
- PDF is the primary print output; Excel is the editable output.
- Paper: A4 landscape.
- Month layout uses two date blocks: 1–15 and 16–month-end. Keep this block structure even for February.
- Readability takes precedence over a strict two-page-per-month limit. Add pages when required rather than compressing text illegibly.
- Main register columns: date, day and comma-separated activities/entries.
- Bilingual output uses English followed by Kannada on the next line in the same cell.
- Signature block appears at the bottom of the final page for each monthly section.
- A separate optional monthly summary may show area visits, DTC visits, feeder inspections, activity counts, events and meetings. It is outside the daily register.

## 13. Review, finalisation and revision history

Workflow states:
1. Generated Draft
2. Reviewed
3. Finalised

- Every date in a month must receive explicit review before that month can be finalised.
- Users can edit individual entries, add/remove entries, lock dates and regenerate unlocked dates.
- Regeneration preserves locked dates/entries and presents a comparison before the new draft is accepted.
- Finalised records are immutable. Corrections create a new revision; they do not mutate the prior finalised revision.
- Date-wise revision comparison is required.
- Every generated version stores a complete reproducibility snapshot, including activity/area/DTC/feeder masters, weights, holiday/leave/event calendars, scheduling constraints, random seed, generator version, manual edits and lock states.
- Re-run with the saved seed/configuration to reproduce the same schedule; a new seed produces an alternative draft.
- Store register revisions in SQLite, independently of Git.

## 14. Backup, restore and data boundaries

- Provide manual full backup and restore of application data, including masters, calendars, register versions, revisions and configuration.
- Automatic backup scheduling is not required.
- Source code belongs in Git.
- Operational register data and office-specific working data must not be committed to Git by default.
- Backup/restore must preserve referential integrity and revision history.

## 15. Explicitly deferred or out of initial scope

- DTC-to-area mapping.
- Recurring dated-event definitions.
- Half-day leave.
- Automatic backups.
- React frontend.
- Public hosting or multi-user access.
- Runtime machine translation during final output generation.
- Treating generated suggestions as verified records of work performed.

## 16. Open decisions and pre-implementation validation

The following points remain open and must not be silently converted into approved defaults:

1. **Target allocator proof and validation:** The policy choices in §10 are recorded, but the exact joint allocation algorithm and its mathematical feasibility proof still require deterministic validation. Define every term and unit in any lower-bound equation; use the actual eligible dates and 7-calendar-day revisit interval; account for category overlap under the combined daily cap; validate per-asset caps and cross-cycle boundaries. The candidate bound and table supplied during analysis must not be treated as proven or hardcoded until these conditions are satisfied.
3. **Initial data validation:** during implementation, re-import/validate the supplied DTC source against the 113-SCD inclusion rule, class counts, preserved source fields and explicit feeder overrides. Do not infer missing source rows from this requirements document.
4. **Holiday data validation:** load and verify the Karnataka General Holiday baseline for 2025 and 2026 only. Restricted Holidays must remain excluded.
5. **Activity catalogue content:** prepare the initial 40–60 activity records with English/Kannada text for owner review before treating the catalogue as operationally approved.
6. **Area and feeder coverage defaults:** The owner has not selected a monthly minimum or explicit coverage cycle for all areas or all feeders. Do not assume one visit per canonical area or feeder per month. Obtain the owner’s choice before making full-roster monthly coverage a required target.

These are requirements/data validation gates, not reasons to block unrelated scaffolding indefinitely. The DTC cycle/fallback modes and the initial per-asset monthly maximum are now requirements decisions, but the exact joint target allocator must pass the date-level validation in item 1 before it controls generation. Keep the existing open-decision numbering as-is; the missing item number 2 is a pre-existing numbering gap and is intentionally not renumbered in this act.

## 17. Change-control interpretation

This document records the product requirements and owner decisions gathered to date. A requirement being written here does not mean it has been implemented, tested or enforced. Keep these states distinct:

- requirement/decision captured;
- detailed design completed;
- implementation completed;
- enforcement/wiring completed;
- validation passed;
- capability accepted/closed.

Any later change to a settled decision should identify the new evidence or requirement that justifies reopening it. Implementation should preserve the decisions above unless the owner explicitly changes them.

---

**Current delivery state:** Requirements baseline recorded. No application code, database schema, UI, scheduler, PDF renderer or Excel exporter is implemented by this change.
