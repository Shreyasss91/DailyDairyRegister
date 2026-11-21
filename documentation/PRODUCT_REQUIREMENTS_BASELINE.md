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

### 4.1.1 The supplied holiday baseline, as checked

The baseline is 39 rows for calendar years 2025 and 2026. It has been read against the Government of Karnataka's own General Holiday tables and holds their dates, weekdays and names, with what is still open named at the end of this section.

What the two notifications say, and what this baseline therefore is:

- 2025 is `No.DPAR 16 HHL 2024`, dated 21.11.2024: 19 dates. 2026 is `No.DPAR 16 HHL 2025`, dated 17.11.2025: 20 dates.
- Both are headed as the General Holiday table, and both open by naming the weekly offs they stand on top of: all Second and Fourth Saturdays, and all Sundays. The weekly offs are calendar rules of their own, applied by §4, and are deliberately not rows in this file. A General Holiday that falls on a weekly off is not a second row either.
- Both carry a note listing the festivals they leave out for exactly that reason, e.g. Republic Day and Ugadi in 2025, Maha Shivaratri and Kannada Rajyotsava in 2026. Those dates are absent from the file and must stay absent: adding one would turn a weekly off into a holiday and make the register explain the same closure twice.
- The same notes name Kodagu-district local holidays and the Restricted Holiday list. Neither is imported, per §4.1. A third-party list that merges the two (Guru Nanak Jayanthi, Huthari Festival) is not the notification.
- The 2026 notification carries a note on the date of observance for Muslim Government employees. It qualifies a notified date for some staff; it does not create a universal second closure, so it is not a row. The single notified date is what the calendar uses.
- Every one of the 39 dates has been checked against the weekday its notification prints beside it, so a slip in either the date or the weekday would show as a disagreement rather than pass unnoticed.

What is still open:

- Neither notification was obtained as the signed original or the gazette copy inside this repository. The tables above were read from published copies of the two notifications and cross-checked against two independent transcriptions of each, which agree on every date, name and identifier. That is strong enough to stage and review, and it is not the same as having the original in hand.
- No corrigendum or later order amending either year was found. A negative is the weakest kind of evidence: absence of a hit is not proof that no amendment exists.
- Consequently the baseline is **ready for operator review and staging, and is not recorded as an approved baseline** until the operator accepts it against the original they hold. Staging it changes no calendar row; acceptance is the operator's act.

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

### 6.1 The catalogue the importer requires, and what has been supplied

The 40–60 activity records in §6 are **not supplied as approved data**. What exists in
this repository is the blank import template and a file of 51 drafting suggestions that
says in its own name and in every row that it is proposed and not approved. Neither is a
catalogue: the blank template holds no record, and the proposals carry no approved
numbers, no approved Kannada and no office approval.

Because a missing number used to be completed at acceptance with weight 1.0, monthly
minimum 0, monthly maximum 10 and repeat gap 2, and an identifier-less row used to be
given `ACT-###` automatically, an incomplete file could enter the master looking
complete and a re-upload of the same identifier-less row could add a second copy. The
importer now requires each row to state:

- an activity identifier, category and English description;
- a weight greater than zero;
- a monthly minimum and maximum of zero or more, with the minimum not above the maximum;
- a repeat gap of zero or more days.

A number that is blank, a placeholder such as `#N/A`, or text that is not a number is
refused and named, and nothing is defaulted. A whole number is read exactly rather than
through a floating point, because a float cannot hold every integer: above 2**53 two
different values compare as equal, so a minimum one above its maximum would pass the
comparison and the value written would be the rounded one. A whole field accepts an
integer as it stands and a float only when it is finite and already whole, so `2.0` is
two and `2.5` is refused rather than truncated. Such a value is also checked against the
range this register can store, the database's signed 64-bit integer range, and refused
with the field named when it lies outside it; that is the storage boundary and not a
scheduling limit, so no maximum frequency, repeat gap or visit count is imposed by it. A
weight too large to be a finite number is refused in the same terms instead of raising.
A Kannada description is required for the register output but its absence is reported as
a warning on that row rather than a refusal: the importer never translates, and a row
without Kannada is imported with its English text alone. A file whose columns do not
match the import contract at all is refused at staging with the missing column named,
instead of being recorded as a batch with no rows.

**What the owner must supply, and no code may supply instead:** the approved activity
list (or approval of named rows from the proposals), the Kannada wording for each, the
weight, the monthly minimum and maximum, the repeat gap, and any weekday restriction.

**Scope material found, and why it is not a catalogue.** The only section-officer duty
material the search of this machine read that names concrete duties is a BESCOM O&M supervisory register
(81 duties across 15 work areas). Its own cover says it is a supervisory tool and not an
official duty order; its frequencies are words such as "Daily" and "As required"; and it
carries no weights, no monthly limits, no repeat gaps and no Kannada text. Whether it
applies to this MESCOM office at all is an owner decision. It is therefore used only to
prepare a worksheet of blanks for review, kept out of Git, marked not approved, and
refused by the importer if anyone tries to import it.


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

**These sixteen areas are not seeded by the software.** They are the original office's own
record, and the application seeds no place at all: an office's areas arrive from its own
`04_area_master.csv` through the import workflow, which is the only authority over them. A
starting database therefore holds no area borrowed from another office, and the same is true
of the feeders in §9.

## 8. DTC master, source data and scheduling

The supplied DTC source contains 113 SCD records. Include the 113 SCD records; exclude five SCF and one SCE records. What is excluded is decided by the source class the file states in its own `Class` column, not by the letters in front of an identifier: the letters are the office's filing code, and a prefix such as `SCE` in a later office's roster is that office's identifier series rather than the excluded class. The 113 SCD records comprise 68 DEPT and 45 SELF_DTC records (42 MSB + 3 OWN). MSB and OWN are one scheduling class, `SELF_DTC`.

- DTC identifier: the office's own code — a short letter prefix and a number, such as
  `SCD-12` in the office this tool was built for or `SCE142` in the next one. The prefix
  is a filing convention, so the tool neither dictates it nor reads meaning into it; a
  cell that is not a code at all (a placeholder, a bare number, a name in the wrong
  column) is refused and named rather than imported.
- Display name: source `DTC_Name`.
- Retain imported source fields, including DTC name, feeder, feeder code, MDM code, kVA, station and original DEPT/MSB/OWN classification.
- Retain the supplied **date of commissioning** (`Date of commision` in the office's own file) as a canonical date on the DTC record, with the supplied text kept beside it as source evidence. A blank or unreadable value is stored as no date, which the schedule reads as no restriction; it is never treated as "not yet commissioned", because that would make an untouched master unusable. The date bounds the dates the asset may be visited on, as §10.9 states.
- Preserve blank and `#N/A` source values; do not silently discard records or fabricate missing values.
- Preserve original imported values and store owner-confirmed feeder assignments as explicit canonical overrides. Do not overwrite source data or silently repair discrepancies.
- Source import must include a validation/reconciliation view so discrepancies and missing fields can be reviewed.

Owner-confirmed canonical feeder assignments:
- SCD-5 → MRS F-1 OLDTOWN
- SCD-12 → Mandli F-22 GANDHIBAZAAR
- SCD-107 and SCD-108 → Mandli F-22 GANDHIBAZAAR (settled 2026-11-19)
- SCD-109 and SCD-110 → MRS F-1 OLDTOWN

The owner's own rule, settled 2026-11-19, governs where the two disagree: **the imported
roster file is what the office goes by.** The confirmed assignments above are therefore
the feeder the roster itself names, and an import that agrees with them reassigns
nothing and reports nothing. The table is kept because a later file that contradicts
these records must be announced before the batch is accepted rather than discovered
afterwards, and the supplied text is stored beside the canonical feeder either way.
SCD-107 and SCD-108 were previously recorded as MRS F-1 OLDTOWN; that is superseded,
and `apply_approved_feeder_overrides` corrects those two rows on the next seed or
acceptance, so a database imported under the old reading converges.

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

### 8.1 The supplied roster, as imported

The office roster has been read and reconciled through the import workflow. What it contains, and what the tool now checks:

- 113 SCD rows, numbered SCD-1 to SCD-113 with no gap and no duplicate: 68 DEPT and 45 SELF_DTC (42 MSB + 3 OWN). Every row carries a capacity and a station, so this file contains no blank and no `#N/A`. The reconciliation is a check on the source, not a target to be reached by editing it.
- All six owner-confirmed assignments agree with the supplied feeder text as the roster is now supplied, so an import of it produces no reassignment notice. Two of them (SCD-107 and SCD-108, both Mandli F-22 GANDHIBAZAAR) were recorded as MRS F-1 OLDTOWN in an earlier reading and are corrected under the rule above. The supplied text is stored apart from the canonical feeder, so a future file that contradicts a confirmed record is reported during review and neither value overwrites the other.
- A row whose canonical assignment differs from what its supplied text resolves to is announced before acceptance, naming the DTC, the assignment, the assignment's feeder id and the supplied text. The operator reviews the reassignment rather than discovering it afterwards.
- A batch whose assignment names a feeder the master does not carry is refused, under the existing rule that an error blocks an import. Accepting it would store DTCs with no canonical feeder and leave every feeder's derived capacity short while reporting success. Import the feeder master first.
- Because a working day then allowed at most three field visits in total (the policy in force when this was measured, since replaced by a per-kind ceiling for DTCs), and a cycle owes one visit per active DTC, one calendar month cannot hold this roster: 113 required against roughly 25 eligible dates. A one-month DTC coverage cycle is refused as `INFEASIBLE` with the capacity named, never truncated to fit. A two-month cycle was then run over 2026-01-01 to 2026-02-28 and allocated the whole roster: 113 visits for 113 distinct DTCs, one each, across 59 day rows, with no visit on a non-working day. That is evidence for those two months, not a general claim about every longer cycle: what a cycle of three months or more allocates has not been run, and the paragraph above is the only reason to expect it to hold.

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

## 10. Daily visit limits, one per kind, and target policy

### 10.1 One daily maximum per kind of visit

The owner settled this policy on 2026-11-08, replacing the earlier single shared daily
total. Each kind of visit has its own ceiling on one working day, and the kinds are
independent: a date filled with transformer checks has not spent what the office reserved
for areas or feeders.

- **A separate maximum per working day for each of DTC, area and feeder visits.** Initial
  defaults: **3 DTC, 2 area and 1 feeder** visit per working day. The DTC default is the
  allowance the retired shared total of three effectively gave transformers, which carry the
  bulk of the demand; all of these numbers remain configurable (§24.10, §24.11).
- **The retired shared total is not a fourth default.** A request may still state one
  whole-date total, and it then applies as an extra ceiling over all kinds at once, so an
  older caller and an older snapshot keep generating the schedule they were issued with. It
  is `None` unless something states it, because a separate total applying by default would
  keep refusing months the per-kind ceilings hold - the defect this change removes.
- **Only activities carry a daily minimum.** The approved catalogue's floor per working day
  (§24.8) is the only minimum on a date. No kind of visit has one.
- **Every visit ceiling counts visits only.** The safety meeting recorded on every Monday is
  a day entry, not a visit, and it is not one of the day's activities either: it is never
  counted against any of these maxima and never credited towards the activity minimum.
- All three visit types occur only on working days.
- The minimum revisit interval for an individual area, DTC or feeder remains **7 calendar days**, as established in §§7–9. The next visit to the same asset must be at least 7 calendar days after its preceding visit. Do not reinterpret this as 7 working days.
- Unless an owner-configured value overrides it, the initial default monthly maximum is **3 visits per individual asset per calendar month** for areas, DTCs and feeders. This is a separate per-asset monthly cap, not another expression of that asset's kind's daily ceiling. Both constraints apply independently; a tighter constraint from the calendar-day revisit interval or an explicit owner setting takes precedence.
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
- If the requested cycle cannot be satisfied under the daily transformer ceiling, the per-asset monthly maximum, the 7-calendar-day revisit interval, the eligible working dates or other configured commitments, report the specific infeasibility. Do not automatically relax the cycle, revisit interval or daily ceiling.

### 10.3 DTC fallback mode — no coverage cycle declared

- If the owner has not declared a DTC coverage cycle, use a feasibility-bounded, weight-informed monthly target as a fallback.
- Calculate the feasible range from the actual working dates in the month, the transformer daily ceiling, per-asset monthly maxima and the 7-calendar-day revisit interval. The monthly target recommendation must not exceed the date-level feasible capacity. Area and feeder commitments do not narrow it: each of those kinds has its own ceiling, so an area visit does not take a transformer's slot.
- Allocate the fallback target through a deterministic weighted-fair rotation. Use prior visit history/least-recently-visited state as part of selection so that repeated generations do not systematically favour the same DTCs.
- Label this recommendation as a **capacity-based monthly suggestion**, not as a coverage commitment. Without a declared cycle, the application must explicitly state that it does not guarantee that every active DTC will be visited within a specified period.
- Display the feasible range, recommended target, active roster size, and the constraints that limit the recommendation. The user may edit the target, but the feasibility validator remains authoritative.
- Do not silently convert the fallback into an assumed one-month, one-quarter or other full-roster coverage promise.

### 10.4 Area and feeder target policy

- Area and feeder monthly targets remain configurable and feasibility-checked.
- No owner-approved default currently requires every one of the 16 canonical areas or all 4 feeders to be visited once every month. Do not infer those minimums from the asset roster size.
- Where the owner has configured per-asset minimums, maximums or a coverage cadence, the target allocator must honour them. Where these have not been configured, show the feasible range and the basis of any suggested target; do not present an assumed full-roster monthly visit as an approved requirement.
- Area, DTC and feeder target recommendations must be validated together against their own daily ceilings and against any whole-date total a request states, because two kinds may still be packed onto the same date. Do not assume the resulting category schedules can simply be added.

### 10.5 Feasibility and calendar semantics

- Let `W` denote the number of eligible working dates in the selected month and `C_k` the daily ceiling of visit kind `k` (initially `C_DTC = 3`, `C_AREA = 2`, `C_FEEDER = 1`). Each `W × C_k` is a gross aggregate upper bound on that kind's visits, not a sufficient proof that a particular target is feasible.
- Exact feasibility must be evaluated against actual dates. Each asset's visit dates must satisfy the 7-calendar-day interval, the effective per-asset monthly maximum, working-day eligibility and any asset-specific restrictions. Each kind is bounded on its own, and a whole-date total applies as well only when a request states one.
- A seven-working-day spacing grid must not be substituted for the settled seven-calendar-day minimum revisit interval. Working-day spacing may be used as a distribution/fairness heuristic only if the hard calendar-day rule is still enforced.
- Any proposed lower bound that sums separate area, DTC and feeder day counts must prove that those category visits cannot be packed on the same date. Different visit types may share a date, subject to each kind's own ceiling and to a stated whole-date total, so category-specific day counts cannot simply be added without a valid proof.
- The previously proposed analytical bound and sample table are not yet accepted as a verified feasibility theorem. Before the scheduler uses them, define each term and unit precisely, establish which per-asset minimums/coverage obligations are actually configured, account for date-level overlap among categories, and validate the result against deterministic calendar-based test cases.

### 10.6 Corrected necessary feasibility conditions and deterministic test vectors

Notation for the selected period (a full month or a partial month):

These conditions were written for the single shared daily total that §10.1 has since
replaced. Where a vector below states a shared cap, read it as the policy in force when it
was recorded; the per-kind conditions are the ones that hold now.

- `W` = number of eligible working dates in the selected period.
- `C_DTC`, `C_AREA`, `C_FEEDER` = daily ceilings per kind (initial defaults 3, 2, 1).
- `C` = a whole-date total, only when a request states one.
- `A` = actual required area visits in the selected period.
- `D` = actual required DTC visits in the selected period.
- `F` = actual required feeder visits in the selected period.

Necessary aggregate conditions. Each of the following is necessary for any feasible schedule. None is sufficient:

```
D           <= C_DTC * W
A           <= C_AREA * W
F           <= C_FEEDER * W
A + D + F   <= (C_DTC + C_AREA + C_FEEDER) * W     (implied by the three above)
A + D + F   <= C * W                               (only when a total C is stated)
```

The constraints are independent, and all of them hold:

- Each kind's daily ceiling limits that kind only. A month whose areas alone exceed `C_AREA * W` is refused naming the areas and the per-day number that would fit them, not as a shortage of a shared total the operator cannot attribute to anything.
- A stated whole-date total additionally limits all kinds together on one date.

Category overlap. Area, DTC and feeder visits may share the same working date, subject to each kind's ceiling and to any stated total. Separately computed category day counts must therefore not be added unless a valid non-overlap proof exists for that configuration.

Per-asset conditions. For every individual asset the schedule must additionally satisfy:

- every visit date is an eligible working date;
- consecutive visit dates to the same asset differ by at least 7 calendar days: `next_visit_date - previous_visit_date >= 7`, with weekends and holidays counting toward the elapsed days;
- monthly visits to the asset do not exceed its effective per-asset monthly maximum (initial default 3);
- any configured per-asset minimum or coverage-cycle obligation is satisfied;
- prior visit history is respected, including visits made in the preceding month and across a coverage-cycle boundary; neither a month boundary nor a cycle boundary resets the revisit interval.

Minimum calendar span. For an asset assigned `v` visits within a month, the minimum calendar span between its first and last visits is `(v - 1) * 7 calendar days`. For the default monthly maximum of 3, that span is 14 calendar days for three visits. The span is a lower bound on elapsed time only; it does not by itself prove feasibility in a particular month. Actual eligible working dates, holidays, leave, date overrides, previous visit history and other restrictions must still be checked at date level.

The per-asset monthly maximum is an upper bound, not an obligation. A monthly maximum of 3 caps how many visits one asset may receive; it does not require any asset to receive 3 visits. A roster of 16 canonical areas does not imply 48 area visits, and 113 canonical DTCs do not imply 113 DTC visits per month. Required visit counts arise only from configured per-asset minimums or an owner-declared coverage-cycle obligation.

Conditional area-cap example. Suppose, for illustration only, the owner required 3 visits to each of the 16 canonical areas in one month. Then `A = 16 * 3 = 48` required area visits. Under the area-specific daily maximum of 2, the minimum number of distinct area-visit dates is `ceil(48 / 2) = 24`. In a month with `W = 23` eligible working days the maximum possible area visits is `23 * 2 = 46`, so 48 required area visits are infeasible before any DTC or feeder visit is considered. This example is conditional and is not an approved monthly target; the default per-asset maximum of 3 does not create a requirement of 3 visits per area.

Deterministic test vectors. The table below states expected outcomes under the definitions above. Rows marked "necessary only" validate necessary conditions and do not establish full date-level feasibility. Remaining rows are single-date or single-asset checks and are individually decisive.

| Test | Inputs | Expected result |
|---|---|---|
| Calendar gap exact boundary | Same asset on day 1 and day 8 | Allowed (difference is 7 calendar days) |
| Calendar gap violation | Same asset on day 1 and day 7 | Rejected (difference is 6 calendar days) |
| Weekend elapsed gap | Friday and following Friday | Allowed if both dates are eligible working dates (7 calendar days) |
| Six-day weekend gap | Friday and following Thursday | Rejected (6 calendar days) |
| Asset monthly cap | Same asset visited 3 times in month, all gaps valid | Allowed (3 <= default maximum 3) |
| Asset monthly cap exceeded | Same asset visited 4 times in month | Rejected (4 > default maximum 3) |
| Cross-month gap | Last visit in prior month; next visit fewer than 7 calendar days later | Rejected (gap is not reset at a month boundary) |
| Cross-cycle gap | Last visit in prior cycle; next visit fewer than 7 calendar days later | Rejected (gap is not reset at a cycle boundary) |
| Area daily limit | 2 area visits on one day | Allowed (2 <= area maximum 2) |
| Area daily limit exceeded | 3 area visits on one day | Rejected (3 > area maximum 2) |
| Mixed-category packing | 2 areas + 1 DTC on one day | Allowed (areas 2 <= 2; DTC 1 <= 3; no total stated) |
| Mixed-category packing | 2 areas + 1 feeder on one day | Allowed (areas 2 <= 2; feeders 1 <= 1) |
| Whole-date total exceeded | 2 areas + 1 DTC + 1 feeder on one day, with a stated total of 3 | Rejected (total 4 > 3) |
| Transformer ceiling exceeded | 4 DTC visits on one day | Rejected (4 > 3) |
| Feeder ceiling exceeded | 2 feeder visits on one day | Rejected (2 > 1) |
| Aggregate capacity (necessary only) | `W = 23`, `C = 3`, total required visits = 69 | `A + D + F <= C * W` passes (69 <= 69); date-level validation still required |
| Area-cap infeasibility | `W = 23`, `A = 48` | Rejected (48 > 2 * 23 = 46) |
| Area-cap boundary (necessary only) | `W = 23`, `A = 46` | `A <= 2 * W` passes (46 = 46); date-level validation still required |
| Area visits are optional | 16 areas, per-asset maximum 3, no configured minimum | 48 required visits are not inferred; no required visit count exists |
| DTC cycle obligation | 113 active DTCs, one visit per DTC per three-month cycle | Exactly 113 required visits per cycle |
| DTC monthly allocation | 113 obligations distributed across three months | 38 + 38 + 37 = 113, capacity-aware across the months |
| DTC capacity excess | `W = 25`, `C = 3`, `D = 76`, `A = 0`, `F = 0` | Rejected (76 > 3 * 25 = 75) |
| Deterministic generation | Same seed, roster, prior history and configuration | Same output |
| Alternative generation | Different seed, same hard constraints | Different valid output is permitted |

The "necessary only" rows (aggregate capacity and area-cap boundary) pass necessary conditions and nothing more. Full date-level feasibility of any target is established by the per-asset and per-date rules above, evaluated against the actual calendar of the selected period, not by these inequalities alone.

### 10.7 Feasibility assessment outcomes and their proof conditions

The feasibility validator distinguishes three outcomes, and the distinction is part of the requirement rather than an implementation detail. A scheduler that reports "infeasible" because a heuristic attempt failed would refuse targets that are in fact schedulable.

- **FEASIBLE** requires a concrete witness: an assignment of visits to eligible working dates that satisfies every hard constraint and can be re-validated independently.
- **INFEASIBLE** requires a proof. Only the following establish it: a configured minimum above the effective per-asset maximum; an obligation above the effective per-asset maximum; a failed per-kind aggregate necessary condition (`D > C_DTC * W`, `A > C_AREA * W`, `F > C_FEEDER * W`, or the whole-date check when a total is stated); an asset for which no gap-respecting placement exists at all; or a completed exhaustive search that found no joint assignment.
- **INDETERMINATE** is reported when the search budget is exhausted before any of the above is settled. It is neither an acceptance nor a rejection, and it must be surfaced to the user as such.

The joint packing problem (placing per-asset visit counts onto working dates under each kind's ceiling and any stated total) is not solvable by greedy placement in general, because a first-fit assignment can fail on an instance that is in fact feasible. Therefore a failed construction is never sufficient evidence of infeasibility, and the validator must not use one as such.

Cost note: the exact search is tractable for small rosters and becomes expensive as the number of assets with multi-visit obligations grows. A large roster with obligations at the aggregate boundary is expected to return INDETERMINATE within a default budget. Recording a target as feasible at scale therefore requires either a tighter obligation set or a stronger proof method, and neither is assumed here.

### 10.8 Assessment period, obligation inputs and committed visits

This subsection records the input contract the validator enforces. Each clause exists because the opposite behaviour would produce a verdict that is wrong in a way a user cannot see.

**Assessment period.** Assessment is defined for one calendar month. Per-asset minimums and maximums are calendar-month limits, and a coverage-cycle obligation is resolved by the caller into the portion owed in the month under assessment. A period spanning two calendar months is rejected rather than silently judged as one month, because that would apply a single month's per-asset maximum to both months and conceal genuine per-asset overrun. A partial month within one calendar month is valid and uses its actual eligible dates. A multi-month cycle is assessed month by month: the recorded 113-DTC three-month example is 38, 38 and 37, and each portion is assessed against its own month's capacity and limits.

**Malformed obligation inputs are rejected, never dropped.** The following are input errors, not verdicts: a duplicate asset id in the roster; an obligation, cycle obligation or limit override naming an unknown asset; a negative or non-integer count. A positive obligation or a configured positive minimum attached to an inactive asset is likewise an input error, because such a requirement cannot be discharged and omitting it would report a month as feasible while owed work was never scheduled. A malformed input is reported distinctly from a mathematically infeasible but well-formed obligation set; it is never reported as feasible and never as infeasible.

**One obligation specification.** Proposed-schedule validation in its obligation-aware mode and target-obligation feasibility assessment consume a single resolved obligation specification. The resolved obligation for an asset is the largest of its configured monthly minimum, its explicit required count and its resolved cycle portion. A monthly maximum never contributes to it, because a maximum bounds and does not demand. Sharing one specification is what prevents the two paths from disagreeing about what is owed.

**Committed visits are distinct from prior history.** Visits already committed inside the month, such as locked dates during regeneration, are supplied separately from visits made before the month. Committed visits occupy daily capacity including the area-specific capacity, count toward the per-asset monthly maximum, discharge the corresponding part of the declared obligation, and constrain new placements through the seven-calendar-day interval. They are returned alongside newly allocated visits, so a feasible witness describes the whole month rather than only what was added. A committed visit outside the month or off an eligible working date is reported rather than discarded, because a schedule resting on an invalid commitment cannot be correct.

**A committed visit is validated as strictly as a proposed one, on every path.** Committed in-period visits are checked for unknown assets, inactive assets, dates outside the period, ineligible working dates, every daily ceiling that applies to them, the per-asset monthly maximum and the seven-calendar-day interval against prior history and against each other. Both the concrete-schedule validator and the feasibility assessment evaluate these through one shared hard-constraint path, so the two cannot reach different conclusions about the same input.

**An invalid commitment makes the month infeasible, and no path may bypass that.** Because committed visits are immutable input, committed visits that already break a hard constraint mean no valid complete schedule exists around them. The assessment therefore returns INFEASIBLE with structured evidence naming the constraint, asset, offending dates, limit and observed value. This check runs before every path that can return FEASIBLE, including the path where the declared obligations are already discharged by the commitments and nothing remains to allocate; that path was previously able to return FEASIBLE on a month the concrete validator rejected. An invalid commitment is reported as an infeasibility, which is distinct from a malformed obligation specification, that being a caller error reported as an input error.

**A FEASIBLE verdict is a complete valid month.** Every FEASIBLE result, including one whose witness is empty or consists only of committed visits, carries a witness that passes independent hard-constraint validation and independent obligation-aware validation. A caller may rely on that without re-deriving it.

**Obligation counts are integers, and a boolean is not one.** A visit count must be a non-negative integer. Boolean values are refused even though the language treats a boolean as an integer subtype, because a boolean is a mistaken input and accepting it as a count of one would schedule work nobody asked for.

**Structured diagnostics.** A violation carries the constraint identifier, the affected asset and type, the dates involved, the limit and the observed value as structured fields. A daily-capacity violation additionally names every asset sharing the affected date with its type. A monthly-maximum violation lists every date counted, not merely the most recent. Message text is a readable rendering and is never the source of truth, so no caller needs to parse it.

### 10.9 The commissioning date bounds an asset's visit dates (as built)

Settled 2026-11-18. A DTC did not exist before it was commissioned, so nothing may be scheduled against it for a date up to and including that date.

- **Strictly after.** An asset commissioned on 15-09-2026 is first visitable on 16-09-2026. The commissioning day itself is not a visit date.
- **No date means no restriction.** A blank cell, an unreadable value or a `#N/A` placeholder leaves the asset unrestricted. It does not make the asset unvisitable, and no date is invented to fill the gap. The value as supplied is still retained with the source record, so an unreadable date stays inspectable.
- **The rule binds every path, not only generation.** A proposed visit on or before the date is refused as `visit_before_commissioning_date`; a committed visit is refused as `committed_visit_before_commissioning_date` rather than dropped, because a schedule resting on an impossible commitment cannot be correct. The feasibility assessment proves a month infeasible as `asset_not_yet_commissioned` when the asset has fewer usable dates in the month than the visits it owes, naming the date and the number of usable dates that remain.
- **The allocator never offers such a date.** It is a roster property like `active`, so a placement candidate is filtered once, when that asset's option set is first computed, and the exact assessment's own candidate dates apply the same filter. The two paths therefore agree about which dates exist.
- **A coverage cycle defers rather than miscounts.** An asset commissioned part-way through a cycle is dealt to a month it exists in; its cycle obligation and the cycle total are unchanged. An asset commissioned after the cycle's last usable date is excluded from that cycle and reported with its date, rather than silently omitted from a plan that claims complete coverage. Where availability forces a unit out of its month's even share, the resolution records how many units were forced; with no commissioning dates in the roster the distribution remains exactly even (113 transformers over three months is still 38, 38 and 37).
- **A capacity-based suggestion never selects an unvisitable transformer**, and reports every active DTC it could not cover at all, with the reason, beside the draft.
- **Recorded with the draft.** The request snapshot carries each roster member's date (schema 6). A snapshot written before the field existed holds none, which is exactly what it was generated with.
- **Not modelled.** There is no rule by which an asset commissioned *during* a month must receive a visit in that month; the cycle obligation, not the calendar of commissioning dates, decides what a month owes.

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

1. **Target allocator proof and validation:** The policy choices in §10 are recorded, but the exact joint allocation algorithm and its mathematical feasibility proof still require deterministic validation. Define every term and unit in any lower-bound equation; use the actual eligible dates and 7-calendar-day revisit interval; account for category overlap under the combined daily cap; validate per-asset caps and cross-cycle boundaries. The candidate bound and table supplied during analysis must not be treated as proven or hardcoded until these conditions are satisfied. **Status: partially satisfied.** A joint date-level allocator now exists (`dailyworkreg/domain/allocator.py`) and every result it reports as feasible has been validated against the full hard-constraint and obligation-aware checks. It is validated empirically, not proved: no mathematical proof of global optimality or of the completeness of the constructive search is claimed, and the allocator is explicitly not required to find a globally optimal weighted schedule. The necessary conditions in §10.6 remain necessary only. Unresolved cases return INDETERMINATE rather than being reported as impossible.
3. **Initial data validation:** during implementation, re-import/validate the supplied DTC source against the 113-SCD inclusion rule, class counts, preserved source fields and explicit feeder overrides. Do not infer missing source rows from this requirements document.
4. **Holiday data validation:** load and verify the Karnataka General Holiday baseline for 2025 and 2026 only. Restricted Holidays must remain excluded.
5. **Activity catalogue content:** prepare the initial 40–60 activity records with English/Kannada text for owner review before treating the catalogue as operationally approved. **Status: open, and blocked on the owner.** A source search of this machine found no approved catalogue among the files it could read; the importer now refuses any row that omits its number, identifier or text rather than completing it (see §6.1). What is missing is source material and owner decisions — approved activity list, Kannada wording, weights, monthly limits, repeat gaps and any weekday restriction — not implementation.
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

**Current delivery state:** Requirements baseline recorded, with a validator and a joint target allocator implemented in the domain layer. No database schema, UI, API route, PDF renderer or Excel exporter is implemented. No DTC source data or official holiday data is imported.

---

## 18. Implemented allocator contract (as built)

This section records what the domain allocator actually does, so a reader does not have to infer the contract from source. It describes implementation, not new policy: every rule here derives from §10 and §16.

**Module layout.** `dailyworkreg/domain/validator.py` holds the hard constraints and the three-outcome assessment. `dailyworkreg/domain/cycle.py` resolves a coverage cycle into monthly obligations. `dailyworkreg/domain/prorating.py` applies a partial month's effective limits. `dailyworkreg/domain/allocator.py` places one month's obligations onto dates. `dailyworkreg/domain/targeting.py` runs cycle mode, the fallback mode and multi-month execution.

**Single source of truth.** The allocator owns no constraint rules. Obligations come from `resolve_obligations`, and every candidate schedule is accepted only when `validate_schedule(..., ValidationMode.OBLIGATIONS)` passes. There is no second implementation of daily capacities, monthly maxima, working-date eligibility, asset activity or revisit gaps.

**Outcome contract.** `FEASIBLE` is returned only when a complete witness has already passed obligation-aware validation. `INFEASIBLE` requires the validator's own proof or an invalid commitment, with structured evidence. `INDETERMINATE` is returned when the attempt budget is exhausted without proof and is never converted into a claim of impossibility. Malformed obligations raise `InputError` and never become a verdict.

**Ordering of the two proofs.** Construction runs before the exact feasibility assessment. Measured, the exact assessment spent about 40 seconds exhausting its node budget on a 54-asset month while construction found a valid month in 0.03 seconds. The assessment is therefore consulted afterwards, as an oracle of impossibility and as a source of a witness, so a real proof stays available without every successful allocation paying for an exhaustive search.

**Placement strategy.** Constrained-first: at each step the asset with the fewest still-viable dates is placed next, so a flexible visit cannot consume the last date a tightly constrained asset needed. Among viable dates the least-loaded then earliest is chosen, spreading visits rather than clustering them. Ordering is determined by the inputs and the seed.

**Cycle resolution.** A cycle's total obligation is distributed so monthly totals differ by at most one, and units are dealt round-robin across months so a small roster spreads through the cycle instead of stacking into its first months. The recorded 113-DTC three-month case yields 38, 38 and 37 with every DTC appearing exactly once. A cycle gives every active DTC an equal obligation; weights, history and rotation affect only the order in which scarce months are granted.

**Fallback mode.** With no cycle declared, a month receives a capacity-bounded DTC target selected by least-recently-visited, then weight, then asset id, rotated deterministically between months. It is labelled a capacity-based monthly suggestion, because without a declared cycle there is no coverage commitment to satisfy.

**Multi-month execution.** Each month is allocated against its own `DatePeriod`. Earlier months' visits are carried forward as history so the seven-day interval and fairness rotation survive the boundary. A month never consumes another month's per-asset maximum, and a failed month contributes nothing to history. Only months the caller asked to schedule are placed; every month of a cycle is still resolved.

**Partial months.** A full month uses its configured limits. A partial month requires explicitly supplied effective limits and otherwise raises `InputError`, because the requirements fix no integer rounding rule for prorating. A supplied prorated maximum or minimum above the configured value is rejected.

**Cycle-wide commitment reconciliation.** Fixed visits are inventory-ed and reconciled against the whole declared cycle before any month is allocated, so the outcome does not depend on the order in which months are processed. The declared `CycleResolution` stays the declared plan; the obligations a month is actually allocated against are reported separately as effective obligations, so the two are never confused. Each committed DTC visit consumes exactly one obligation unit: it is charged to the month it falls in when that month still owes a unit, and otherwise to the earliest month that does. For every DTC in the cycle, newly allocated visits plus committed visits equal the declared obligation, except where the commitments already exceed it, in which case every commitment is preserved and the surplus is reported as structured over-coverage evidence. A commitment is never dropped to make the arithmetic work, and an obligation is never silently reduced.

**Commitments constrain the gap in both directions.** A committed visit outside the month being placed is supplied to the validator as a gap blocker: it constrains the seven-calendar-day interval exactly as a prior visit does, while consuming no capacity and no obligation in that month. This is what allows an earlier month to place around a commitment later in the cycle instead of accepting a date the later month must then reject. A commitment is never supplied both as its month's fixed visit and as a gap blocker, so it is never counted twice.

**Commitments are validated wherever they fall.** Every committed visit is held to the same hard constraints a schedule is, including when its month is not selected for placement, because omitting a month from placement is not permission to ignore what is committed there. A fixed-visit mapping for a month outside the cycle, a visit filed under a month that does not contain its date, and a commitment failing the hard constraints all raise `InputError` with stable identifiers. A commitment that could not have happened is never treated as a valid discharge.

**Only DTC cycle members affect cycle accounting.** A cycle's obligation applies to active DTCs only. Committed area and feeder visits are preserved, consume their own kind's daily ceiling and constrain the gap, but are never compared with the cycle's per-visit obligation and never produce DTC over-coverage evidence.

## 19. Implemented draft-register generation contract (as built)

This section records what the draft-register generation layer does, so that the
allocation contract in §18 and the presentation contract here are not confused
and no reader mistakes "the draft says so" for "the allocator decided so".

**The layer consumes and presents; it never schedules.** `generate_draft()` calls
`run_cycle()` or `run_fallback()` and projects the result. It does not compute a
DTC target, distribute a cycle, evaluate a revisit interval, count capacity,
override a monthly maximum, move a visit, add a filler visit, relax an obligation
or treat a weight as an obligation. A projected month's visits are the
allocator's own visits, unaltered; the suite asserts that equality directly
rather than inspecting a rendering.

**Two modes, each with its own required inputs.** Coverage-cycle generation
requires the declared cycle and rejects the capacity-suggestion inputs
(`dtc_target`, `area_required`, `feeder_required`) instead of accepting them as
silent no-ops, because `run_cycle()` would ignore them and a caller could
reasonably believe they had been applied. Capacity-fallback generation rejects a
declared cycle, because a declared cycle makes coverage the primary policy and
the two cannot both be the target. A selection outside the declared cycle is
rejected rather than extended.

**Only complete calendar months are generated.** A partial first or last month
is rejected under `partial_month_selection_unsupported`. The range is never
expanded to a whole month, full-month limits are never applied to a shorter
period, and no floor-or-ceiling rounding is chosen. §10 leaves the prorating
rounding rule undeclared, and the domain's explicit effective-limit mechanism is
left intact for when an owner declares it.

**One row per calendar date, whatever the date is.** Every date in the selected
months produces exactly one row, in chronological order, including Sundays,
second and fourth Saturdays, General Holidays, leave and any other non-working
date. The classification and its English and Kannada reasons come
from the caller: this layer has no calendar rules of its own, imports no holiday
data and infers no weekly off. Missing date metadata for a date in range,
duplicate metadata, and contradictory metadata are rejected. Metadata for dates
outside the range is accepted and used only to classify a commitment that falls
in a cycle month that is not being generated, since such a commitment is still
checked against the real calendar; those dates are never projected as rows.

**Asset identity is stable and display text is separate.** Visits carry the
allocator's stable asset id. English and Kannada names come from the caller's
display metadata, are attached at projection time and are never translated at
runtime. Missing, unknown or duplicate display metadata is rejected rather than
substituted.

**Every outcome is preserved, and an unresolved month is never a success.** A
month the allocator found feasible is reported as allocation-complete. A month it
proved infeasible stays infeasible with its structured evidence. A month whose
bounded search ended without a verdict is reported as unresolved, which is never
presented as feasible, infeasible, a generic error or an empty completed month,
and it keeps whatever committed visits it had. A multi-month result keeps each
month's own status even when the overall status is the worst of them.

**A commitment is never lost, including in a month that failed.** An
unschedulable month reports no new visits and reports its commitments separately,
so the projection reads both fields; reading only the new visits would erase every
locked visit from precisely the month where losing them matters most.

**Over-coverage is a resolved exception, not a reason to delete anything.** When
commitments exceed a cycle obligation, every commitment is kept, the surplus is
projected with the asset identity, the declared obligation and the actual
committed count, and it is reported even when the overall allocation succeeded.

**A rejected commitment is reported as data.** An invalid commitment reaches the
caller as `InputError` carrying its stable constraint identifier, the asset
identity and the underlying violations, so no caller parses message text.

**Reproducibility is recorded, not assumed.** The result carries a snapshot of
the allocator version, generator version, mode, seed, range, months, rotation
offset, attempt budget, the declared cycle and its resolution, prior history and
inventoried commitments, plus content fingerprints of the roster, configuration,
calendar and display metadata. Fingerprints are canonical digests, not `hash()`,
whose value is process-randomised. The same request and generator version produce
an identical draft.

**Restricted Holidays are not a calendar concept here.** Requirements §4.1 makes a
Restricted Holiday an ordinary working day and requires its dates and names to be
ignored entirely. The calendar classification enumerates no Restricted Holiday
member, nothing imports or stores one, and a date that is only a Restricted
Holiday is classified working. Where the same date independently falls on a Sunday
or another approved non-working condition, that condition is classified and
displayed on its own with no Restricted Holiday information attached.

**This is a partial capability, and it says so.** A generated draft is an
allocator-backed field-visit draft. It is not a complete operational diary, and
every result names the masters that are still absent: the activity catalogue, the
official Karnataka holiday baseline, and the source DTC roster. Allocation
succeeding is not evidence that the register is complete.

**Lifecycle is out of scope here.** Only a draft is produced. There is no review,
approval, finalisation, immutable revision, correction revision, edit or lock
persistence, regeneration acceptance, backup or restore, and no draft is ever
marked reviewed or finalised.

## 20. Implemented local application contract (as built)

This section records the storage engine, the import path, the persistence of every
generation attempt and the local surface, so a later change knows what was decided
and why rather than rediscovering it.

**SQLite is the single canonical source of truth for operational master data.**
Areas, DTCs, feeders, activities, General Holidays, leave types and records, dated
events, per-date overrides and effective weekly-off rules live in the database and
nowhere else. A configuration file holds runtime settings only — database location,
server port, logging — and never a competing master. There is no import cache, no
JSON master and no second store to reconcile, because two stores for one identity is
how a register comes to disagree with the office that owns it.

**Master data enters by validated import or by operator entry.** The import path is
four stages: the source file is read, judged against the dataset's own rules and
written to staging; an operator sees what it would do; acceptance applies the whole
batch in one transaction or nothing; the batch keeps its decision and note. Reading
keeps every supplied value verbatim, including a blank feeder, a `#N/A` capacity and
a placeholder, because those are facts about what was sent and §8 requires a
reconciliation view of exactly those discrepancies. Judgement never repairs a value:
an unrecognised class is an error rather than a guess at `DEPT` or `SELF_DTC`; a
placeholder capacity is reported and never read as zero; a file naming the same
record twice is refused on every repeat after the first, with the colliding row
cited, because applying either order produces a different master. An accepted DTC
row writes its supplied fields to a separate source-record table with the batch
identifier, filename and digest, so the evidence keeps its provenance. The
owner-confirmed feeder assignments from §8 are applied on acceptance over whatever
the source said.

**General Holiday import replaces only its own year.** The default replaces the
`karnataka`-sourced rows for the imported year and leaves an operator's own entry for
that year intact; a merge is an explicit choice. This is what stops last year's list
from accumulating beside this year's.

**Every generation attempt is persisted, and none is silently discarded.** A
completed allocation becomes a numbered draft revision. An `INFEASIBLE` or
`INDETERMINATE` attempt is stored with its monthly status and structured evidence and
is marked as producing no draft, so it appears in history as a finding about the
month rather than as a register. A new attempt is a new run and a new revision:
nothing is updated in place, nothing is overwritten, and no attempt is marked
reviewed or finalised, because those are separate capabilities and not a side effect
of generating. There is no memory-only draft.

**A run records what it was given, not a summary of it.** The stored request carries a
schema version, the whole roster, the whole classified calendar, the configuration,
the cycle, the weights, commitments and history. A fingerprint answers "is this the
same master state" but cannot rebuild a request, and the office will edit masters
tomorrow: a draft that can only be explained by reading today's live records can no
longer be explained at all. A snapshot from an unknown schema version is refused
rather than half-read. Reading a stored run returns the rows and visits that were
presented; a separate replay re-derives from the snapshot. The two are compared, and
a later master edit leaves the stored draft untouched.

**No transaction is held across allocation.** Allocation finishes first; the write
covers bookkeeping rows only, in one transaction, so a failure leaves no half-written
run that could be mistaken for a complete one.

**Stored rows come before stored meaning.** The row store reads and writes plain rows
and imports nothing from the application layer; the mapping between a generated
register and those rows lives in the application layer. The first arrangement had the
two importing each other, which is a defect rather than a preference.

**Readiness is measured from the database.** The missing-content list is derived from
what is actually absent and changes when something is imported, so it cannot claim a
holiday baseline is absent after one arrived, and cannot stay silent about an empty
activity catalogue. An absent activity catalogue or holiday year does not block
field-visit allocation; it means the result is not yet a complete operational diary,
and it is reported as exactly that.

**Calendar classification is decided in one place from stored facts.** Weekly-off
rules are configured rather than compiled in. A General Holiday that falls on a weekly
off keeps both reasons, as §4.1 requires. An override that makes a holiday a working
day is honoured while the underlying holiday stays visible, because §4.1 asks for the
source fact and the effective classification separately. An operator's own
safety-meeting entry replaces the default sentence on that date rather than adding a
second meeting, and a safety meeting consumes no working-day capacity.

**Restricted Holidays are not a calendar concept here.** They are not imported,
stored, classified, displayed or generated. A date recorded as nothing is an ordinary
working day; a Sunday that is also a restricted holiday remains a Sunday with its
weekly-off reason alone; an independent approved non-working condition survives on its
own terms. This corrects the earlier draft, which carried a `RESTRICTED_HOLIDAY`
classification and therefore contradicted §4.1.

**The local surface decides nothing.** Python with FastAPI, SQLite, and HTML, CSS and
vanilla JavaScript. It binds to the loopback address only and holds no
authentication, because it has no network exposure to protect; there is no host option
because the only useful value is already fixed. A route translates a request into an
application call and a page renders the result. No route and no script reproduces a
capacity rule, a gap rule, a cycle obligation or a violation filter: a rule copied
into a handler is a rule that will disagree with the domain within a release, and a
rule copied into the browser will disagree sooner. A refusal reaches the operator as a
stable constraint with a sentence describing what to fix.

**The output is labelled an allocator-backed draft.** It is never described as
verified work, a reviewed register or a finalised register.

**Weight is not reimplemented.** The scheduling weight comes from the domain's own
`DtcWeights`: a DTC weighs its capacity scaled by its class multiplier and its
individual adjustment; a feeder weighs the aggregate capacity of its active
department DTCs scaled by its stored manual multiplier; an area carries its stored
weight. The application feeds that model and owns no second formula.

**No dependency was added for this work.** FastAPI, uvicorn, openpyxl and the
standard library were already available; no solver, scheduler or ORM was introduced.
## 21. Implemented evidence attribution contract (as built)

A diagnostic that describes which assets a failure concerns only in prose cannot be
shown correctly. This section records the contract the validator, the store and the
surface now share. It changes no constraint, limit, policy or verdict.

**Attribution is singular or plural, never invented.** A fact about one asset names
it in `Evidence.asset_id` with its roster `asset_type`. A fact about the combined
demand of several assets lists them in `Evidence.contributing_assets` and leaves the
singular fields empty, because assigning one asset to a joint proof picks a name out
of arithmetic that does not single any out. A fact about the whole problem, such as a
node budget exhausted while packing every asset at once, carries neither and says so
in `detail`; an empty attribution is the honest answer and is presented as empty.

**Which assets an aggregate proof names.** Area capacity names only the area assets
whose demand is counted: those still owed visits this month and those already holding
a committed visit in it. DTC and feeder assets are excluded, because they consume none
of the area cap. Combined capacity names every asset in that same demanded set across
all three categories. A joint packing failure names the assets that could not be
packed, which is exactly that set. The roster is never named wholesale.

**The collection is deterministic.** `contributing_assets` is deduplicated, ordered by
asset id and paired with the type from the roster in force. An id absent from the
roster is dropped rather than resolved by guesswork, so every name an operator sees is
an asset in that run. `attribute_assets()` is the single place that decides this shape,
and both `Violation.contributing_assets` and `Evidence.contributing_assets` use it, so
the two cannot drift in order or membership.

**Constructor compatibility.** `Evidence.contributing_assets` was appended last, after
`limit` and `actual`, so a caller that constructed evidence positionally keeps its
meaning. Inserting the field earlier would have moved a limit into the wrong field
silently.

**Preflight conversion preserves identity.** A committed visit that already breaks a
hard constraint is reported through the same fields the concrete validator produces,
including the contributing set, so an infeasibility from an invalid commitment is as
diagnosable as a rejected schedule.

**Persistence, and older records.** Evidence and exceptions store constraint, detail,
asset id, asset type, visit date, offending dates, contributing assets, limit and
actual. The exception table gained `contributing_assets_json` in schema migration 2
with an empty default; a row written before it reads as having no attribution, which
is the truth about that row. Decoding is lenient throughout: a missing field decodes
to empty or absent, and an asset type this build does not recognise is dropped rather
than raised, so a history written by another build remains readable. Nothing is
recovered from `detail` to fill a gap, and the request snapshot schema is untouched
because evidence serialisation and request snapshots are separate contracts.

**Presentation.** The generation response reports each month's evidence and each
exception with those fields, rendering an enum or an already-stored string identically
so the client need not know which it received. The interface prints the supplied
values in their own columns and reads no message text to recover an asset.

## 22. Implemented review, correction and regeneration contract (as built)

An issued register is a document an office works from, so it is never edited in place.
This section records the contract the store, the application layer and the surface now
share for reviewing, correcting and re-proposing one. It changes no constraint, limit,
policy or verdict, and it neither finalises a register nor produces a PDF or a
spreadsheet.

**A register, a revision and an attempt are three different things.** A *register* is
the continuity of one document across a period and a mode: the same November register
is still the same register after three corrections. A *revision* is one issued state of
it, numbered within that register from one, and immutable: a correction is a new
revision whose parent is the one corrected, never an update to a revision already
issued. An *attempt* is a generation that produced no draft at all — that is stored
with its monthly evidence and reported as an attempt, because a run that decided a
month could not be covered has no revision number to be given.

**Review is a statement about content, not a click.** A date is `pending_review` until
an operator marks it reviewed, and marking it records a fingerprint of the row they were
looking at. A review offered against content that has since changed is refused under
`stale_review` rather than recorded against a row nobody saw. Withdrawal is recorded
too: a review is an act with a time, a note and an author's intent, and a register that
cannot show when a date was looked at cannot answer a question asked of it months later.
Month review state is derived on read from the dates beneath it, never stored, so it
cannot disagree with them.

**Review and lock are independent in both directions.** A reviewed date is not locked
and a locked date need not be reviewed. A lock is the operator saying this date is
settled: a correction that would change a locked date's content is refused under
`locked_date_content_changed`, and its content survives a correction, a regeneration and
an acceptance exactly as it stood. The lock is what preserves a correction; an unlocked
correction is the operator's current preference and a regeneration may legitimately
replace it.

**A correction is judged by the register's own validator.** An entry edit, an addition, a
removal, an entry moved to another date and a visit moved to another date each produce a
new revision whose validity is established by the same validator that judged the draft,
running in obligation-aware mode against the month's own stored obligations. The edit
layer performs no date arithmetic and asserts no capacity: it presents the visits as they
would be and lets the validator decide, and a refusal carries the validator's structured
violations so the surface can name the asset and the rule. A visit moves within its own
calendar month only (`visit_move_crosses_month_unsupported`), because the validator
assesses one calendar month by contract and a move across a boundary would be judged by
neither month's context. A correction that changes nothing is refused
(`correction_changes_nothing`) instead of minting an empty revision.

**Entries carry provenance.** A `calendar` entry is re-derivable from the calendar, a
`manual` entry was entered by an operator and is never erased by a regeneration, and a
`locked` entry is preserved because its date is locked. A safety meeting replaced by an
operator is therefore theirs, not the default sentence, and the register shows which it
is. A manual *leave* entry is refused: leave is an approved absence in the leave master,
not a typed sentence that would sit beside it claiming the same authority.

**Regeneration proposes; the register does not move until somebody accepts.** The
candidate is a stored revision of the same register, recording the revision it descends
from and the seed it used, entirely pending and holding no locks, because a proposal has
not been reviewed by anyone. Until it is accepted the register keeps pointing at the
revision the office holds; a refusal, an infeasibility or a failed generation leaves that
revision exactly where it was. A candidate that would change a locked date is not offered
at all (`locked_date_content_changed`, naming the dates), and the check runs again at
acceptance, which is the moment the guarantee must hold. Acceptance carries the review and
lock state of the revision being replaced — a review only where the stored content is
provably identical, recording the revision it came from; a lock only where the content it
protected is unchanged — then moves the register's pointer and records the decision in one
transaction. A candidate already refused cannot later be accepted
(`candidate_already_decided`), and a revision no regeneration produced cannot be accepted
or refused at all.

**What the generator is given is the whole of this layer's decision.** Locked visits are
supplied as committed visits for the month containing them, merged with the revision's own
commitments on `(asset, date)` so no fact arrives twice, and never also as prior history
or as a gap blocker — the generator derives those from the same committed set. Every entry
the revision holds is supplied once, because the generator does not re-project a calendar;
dropping them would lose the operator's manual entries, and keeping only those would be an
edit layer deciding whose work survives. A locked visit returns from the allocator as a
commitment and its recorded provenance is restored afterwards, so a date preserved by a
lock is not made to look changed by it. The regeneration reaches no scheduling decision of
its own: it calls the generator and allocator that produced the first draft.

**Comparison is typed and identity-based.** Two revisions are compared by asset id and
date for visits, by entry id for entries, and by month for status. Nothing reads a
description to decide whether two rows are the same row. Each date is reported as
unchanged, added, removed, changed, visits_moved or locked_preserved, with the base and
proposed rows, the visits added, removed and paired into moves, the entries added,
removed and changed with before-and-after values, the base revision's lock and review
state for that date, and warnings where the day's classification or reasons differ
without the work changing. A month unresolved in the candidate keeps its structured
evidence in the comparison, so an unsettled regeneration is visible rather than rounded
into success.

**Presentation.** The draft screen shows review state, lock state, allocation status and
operational completeness as four separate things, because they answer four different
questions and one badge cannot carry them. Every act sends what the operator entered and
renders what the server decided; the client holds no date rule, no capacity rule and no
fitness test, and it reads no diagnostic prose to recover an asset or a cause.

## 23. Proration rounding rule (settled)

The requirement to prorate a partial month "by eligible working days" left the integer
rounding convention undeclared, and there is more than one defensible answer: rounding
down never over-schedules, rounding up never under-schedules a minimum, and each gives a
different number of visits to the same asset in the same part-month. The owner has now
declared the rule. This section records it; it does not by itself enable a partial month
(see the last paragraph).

**The rule: nearest integer, exact halves rounded upward (round-half-up).** For a
configured monthly integer count `L`:

- `W_full` is the number of eligible working dates in the complete calendar month.
- `W_selected` is the number of eligible working dates in the selected range within that
  month.

```
effective = floor((2 * L * W_selected + W_full) / (2 * W_full))
```

The arithmetic is integer throughout. Python's built-in `round()` is not used, because it
rounds exact halves to the nearest even integer, which is a different rule.

**One formula for minimums and maximums.** The same monotonic expression is applied to a
prorated minimum and to a prorated maximum, and to any explicitly prorated monthly count
target. Applying different conventions to the two ends could turn a valid
`minimum <= maximum` into a contradiction, so the rule is one rule.

**Where the day counts come from.** `W_full` and `W_selected` are counted on the
*effective* calendar: after weekly offs, General Holidays, leave records and explicit date
overrides have been applied. They are read from the projection, never re-derived by the
proration step.

**Restricted Holidays are ignored entirely**, as §4.1 requires. A date that is only a
Restricted Holiday carries no classification of its own and is counted by whatever
independently applies to it, so it can neither reduce `W_full` nor `W_selected`.

**A full calendar month keeps its configured counts.** The formula returns the configured
count when `W_selected == W_full`, and a full month does not go through proration at all.

**A zero eligible-working-day denominator is an input error**, not a zero limit: a month
in which nothing is eligible cannot be prorated from, and the error names the calendar
inputs to check.

**DTC coverage-cycle obligations are not prorated.** A cycle obligation is declared over an
owner-defined cycle, not as a monthly frequency limit, so a selected range covering part of
a month does not reduce it. Reducing it would rewrite the cycle that produced it.

**A measurement basis for proration remains unimplemented in the cycle planner.** The rule
above is implemented as arithmetic; the application does not yet derive effective limits
from it.

**Partial-month generation remains unsupported.** A partial month is still refused. Using
the rule to build a schedule for part of a month additionally requires completed-visit
accounting for dates outside the selected range and cycle-commitment continuity across the
boundary, and neither exists yet. Enabling proration without them would produce a shorter
month that looks authoritative and is not. A later bounded act implements partial-month
scheduling in full; until then the rule is arithmetic and tests, never a generated
register.


## 24. Implemented monthly finalisation and correction contract (as built)

A register's months are closed one at a time, and a closed month is never reopened. This
section records what is built, so a later change is a recorded decision rather than a
silent one.

### 24.1 Dimensions that stay distinct

Seven things are recorded and reported separately, because collapsing any two of them
would let an operator believe one state is another:

| Dimension | Where it lives |
| --- | --- |
| The register | `register`, keyed `register_id` |
| An immutable revision | `generation_run`, one row per revision, `revision_number` scoped to the register |
| The current working revision | `register.current_run_id` |
| A finalised monthly version | `month_finalisation`, one row per `(register, month, sequence)` |
| The version in force for a month | `month_state.latest_finalisation_id` |
| Review state | `date_review`, per date per revision, against a content fingerprint |
| Lock state | `date_lock`, per date per revision |
| An open correction | `month_state.open_correction_run_id` |

Finalisation is monthly, not all-or-nothing across a range: closing November leaves
December open, and December is closed by its own act with its own record. Every mutation
is transactional; a failed write leaves neither a record nor a pointer.

### 24.2 The decision to close is made in the application, once

`dailyworkreg/application/finalisation.py` is the only code that decides whether a month
may be closed and the only code that writes the record. A route translates, a page
renders. `check_finalisation(connection, run_id, month, acknowledged=...)` returns
`eligible` plus every blocker, each with a stable code, a label in an operator's words, a
detail, the dates concerned and, where an acknowledgement is needed, the opaque token.

The gates, in the order they are reported:

1. **Current revision.** A superseded revision cannot be finalised, because closing it
   would finalise content nobody is working from.
2. **Not already finalised.** A month cannot be closed twice.
3. **Allocation complete.** The month's stored status must be `allocation_complete`; an
   `INFEASIBLE` or `INDETERMINATE` allocator status blocks the month by its own code.
4. **Every date reviewed, against content that still matches.** Sundays, weekly offs,
   General Holidays and dates with no visits are included: a date reviewed when it was
   empty is a fact the office asserted. A review whose stored fingerprint no longer
   matches the content is stale and blocks the month.
5. **Operational masters present now.** Measured at closing time, not trusted from
   generation time: the DTC roster, the official Karnataka General Holiday baseline for
   each year the month touches, and the approved activity catalogue. A missing dependency
   names itself. Nothing is fabricated to satisfy a gate.
6. **The calendar still what the register describes.** A General Holiday recorded on a
   date the revision holds as a working day blocks closing; a calendar that silently
   moved under a register would make the closed document describe a day that never was.
7. **No unresolved finding.** Stored `infeasible` and `unresolved` exceptions block the
   month. `over_covered` does not block, it requires acknowledgement.
8. **Acknowledgements for the findings that must be seen.** Every calendar conflict and
   every over-covered commitment for this month needs an explicit acknowledgement, and an
   acknowledgement naming no finding is refused rather than accepted. Findings are never
   deleted; the acknowledgement is stored against the exact revision and fingerprint, and
   the finding remains visible in the record.

A month that does not cover the whole calendar month is refused as a part-month, decided
from the stored rows rather than from the request. Partial-month scheduling is not
implemented (section 23), so a part-month cannot be closed.

### 24.3 The record and its write

`finalise_month` runs the gates outside a write transaction — readiness and calendar
projection read the masters, and that is not work to hold a lock across. The write that
follows is short and re-checks the two things that would make the decision stale: the
month's content fingerprint and the register's current revision. If either moved, nothing
is written and the operator is asked to look again.

The record holds the register, month, revision, sequence, parent finalisation, the
revision it corrects (if any), the closing timestamp, the content fingerprint, the whole
month content as approved (dates, visits, entries, warnings, month status, obligations and
evidence), the acknowledged findings with what they said, the readiness measured at the
moment of closing, the master version, and the operator's note. It is written once.

### 24.4 Immutability

A finalised month is refused by every path, and the refusal is a sentence an operator can
act on:

- entry edit, add, remove and move;
- review, and review withdrawal;
- lock and unlock;
- regeneration of the revision, and acceptance of a candidate that would replace it.

The guards are **month-scoped**, not revision-scoped, so a revision that has closed
November can still be worked on in December. `finalisation.month_is_editable` answers the
question, and the database triggers in migration 4 are narrowed to the row's own month for
the same reason. Below the application, `BEFORE UPDATE` and `BEFORE DELETE` triggers abort
any change to a finalised month's rows, reviews and locks, and any change to a finalisation
record; those are the last line, not the operator-facing rule.

There is no reopen, no unfinalise and no endpoint that deletes a record or a revision.

### 24.5 Correction revisions

`create_correction_revision(connection, finalised_run_id, reason=...)` is the only way to
change a closed month. Preconditions: the source revision is finalised; that finalisation
is the month's latest (a correction of a superseded version would branch the month's
history); the reason is not empty; no other correction of the month is open; and the copy
it starts from is verified against the record's fingerprint before anything is opened.

The correction is a new revision that begins as an exact copy of the issued month. It
records what it corrects (the revision and the finalisation), the reason, and the moment.
It does not move the month's latest-finalisation pointer, so an open correction is a
**correction draft** — never the version in force. Review is carried only for dates whose
content fingerprint is unchanged, and those dates say they were carried; every other date
starts pending. The original's locks are preserved, and a date that was locked changes
only after an explicit unlock inside the correction.

Finalising a correction records the next sequence with the previous finalisation as its
parent and the original revision as what it corrects; the earlier versions stay readable
exactly as issued, marked superseded. `abandon_correction` drops an open correction and
returns the register to the record in force, leaving the abandoned revision readable —
revisions are never deleted.

### 24.6 Closing every open month at once (as built)

- `GET /api/generations/{run_id}/finalisation/preflight` reports what a bulk close would
  do and writes nothing. Every month of the revision appears, including the ones already
  closed, because the panel an operator confirms against must show the whole picture
  rather than only the months that would succeed.
- The target set is derived on the server from the revision's own months: a month is a
  target when *this revision* holds no record of it
  (`revision_state.finalisation_for_run_month`). It is deliberately not derived from the
  month-lifecycle pointer being empty, because a correction revision holds a copy of a
  month that is already issued and that month is open *on the correction*.
- `POST /api/generations/{run_id}/finalisation/bulk` closes every target in one act. The
  caller must send the month set the server derived, the content fingerprint it was shown
  for each month, and the acknowledgement tokens it was given. A missing month, an extra
  month, a month whose content has moved and an acknowledgement naming no finding are all
  refused, and nothing is written. What the operator saw is what is closed.
- The act is one SQLite transaction over every month, and it is opened **before** the
  target set is derived rather than before the first write. A content fingerprint covers
  a month's rows and nothing else: whether its dates have been read, whether the calendar
  has gained a holiday that conflicts with it, whether the masters are still ready and
  which findings remain unacknowledged are live questions, and asking them outside the
  write lets the answer change between the question and the record that reports it. Held
  inside, every one of those questions is asked against the state the records are written
  in, and a refusal leaves nothing behind. A failure on the last month still rolls back
  the first, and the fingerprint and current-revision re-checks are still repeated per
  record inside the same transaction.
- Each month's gates are asked again with the acknowledgements this request carries. The
  preflight answers "what is missing"; it cannot know what has just been acknowledged, so
  a month that becomes closable by acknowledging an over-covered commitment or a calendar
  conflict is not treated as blocked.
- Nothing is reviewed on the operator's behalf: an unread date stays unread and the month
  stays open. An already closed month is never closed again and its record is untouched.
  Correction and history semantics are those of a single close: a bulk close on a
  correction revision writes the next sequence with the correction's own lineage and
  clears the open correction.
- Refusals are structured, not sentences: `bulk_finalisation_has_no_open_months`,
  `bulk_finalisation_targets_do_not_match_this_revision`,
  `bulk_finalisation_month_changed_since_it_was_shown`,
  `bulk_finalisation_acknowledges_no_finding` and `bulk_finalisation_blocked`, the last
  carrying every blocked month's blockers so the page can name each month and each reason.
- The single-month close is unchanged in behaviour and in its refusal codes; the bulk act
  shares its preparation and its write rather than reimplementing either.

### 24.7 What the operator does, and what the page must tell them (as built)

- The panel is a state machine with one honest sentence per state, because "the button is
  disabled" has four causes an operator must be able to tell apart: the checks are still
  running, nothing is open, a month is genuinely stuck, or the acknowledgements have not
  been ticked yet. Only the last is cleared by ticking a box, and the page says which
  months are still waiting rather than "not ready".
- Eligibility is the server's answer, in two parts, and the page never infers one from the
  other. Each month reports its `blockers` (everything in the way now), its
  `hard_blockers` (what no acknowledgement can clear) and
  `eligible_after_acknowledgements`; the revision reports `can_finalise` (close now) and
  `can_finalise_after_acknowledgements` (close once the listed findings are acknowledged),
  together with `pending_acknowledgements`: one entry per finding, naming its month, its
  kind, its detail and the token that has to come back. The distinction is drawn from the
  finding tokens the server itself issued, not from a list of blocker names, so a new
  acknowledgement kind cannot be mistaken for a hard blocker by forgetting to extend it.
- A selection belongs to one month and one token. Two months can carry the same finding
  and the same token text, so ticking one must not answer the other; the panel keys
  selections by both.
- The request is one click and one POST. While it is in flight the action is disabled and
  says it is closing, a second click cannot start a second act, and the panel exists in a
  single consistent state afterwards: either the months are closed and the lifecycle is
  re-read from the server, or the refusal is shown in full — the server's sentence first,
  then each blocker with its month and dates — and the checks are marked out of date with
  a `Refresh finalisation checks` action beside them. Refreshing discards the old tick
  state and the old fingerprints; tokens from an out-of-date preflight are never
  submitted.
- A preflight that cannot be read leaves the same refresh action, so a network failure is
  a state the page recovers from rather than a panel that stays empty.

### 24.8 Placing regular activities on working days (as built)

The office's own approved catalogue (`data/record_A/03_approved_activity_catalogue.csv`,
60 records in 15 categories) is imported into the activity masters and placed on the
register by generation, so a generated month records regular work and not only field
visits. The rule lives in `dailyworkreg/domain/activities.py`, which performs no I/O and
reads no clock.

- **The count is the operator's, and it is a range with two hard ends.** The generation
  request carries `activities_per_day_min` and `activities_per_day_max` (an earlier caller's
  single `activities_per_day` is the range whose two ends are equal, and still produces the
  flat month it used to). The generate form starts at a minimum of 3 - the office's own "at
  least three activities a day" - and a maximum of 5. No eligible working day falls below
  the minimum, none rises above the maximum, and each day's own count is drawn between them,
  so a month varies instead of repeating one number. `0` for both leaves every day to field
  visits, and a request that does not mention the field at all records no activities - it
  does not inherit a platform default, so an older caller keeps exactly the draft it used to
  get.
- **The minimum is a floor and the maximum is a ceiling, and neither is a target.** A day
  drawn at the floor still takes work the catalogue still owes for that month, by borrowing
  its own headroom up to the ceiling; a day can never be filled past the ceiling to satisfy
  an obligation, and a ceiling wider than the catalogue is simply drawn under rather than
  demanded. What a day records is therefore between the two ends, and always at least the
  floor.
- **Hard constraints.** The floor and the ceiling per eligible working day; the catalogue's
  monthly maximum per calendar month; its monthly minimum per calendar month; its minimum
  repeat gap, counted in calendar days and carried across month boundaries; its weekday
  restriction; and never two placements of one activity on one day.
- **A weight is a preference and nothing more.** It orders the choice among the
  activities the hard constraints already permit. It cannot raise a maximum, shorten a
  gap, open a restricted weekday or fill a day that has no permitted candidate.
- **A constraint that cannot hold is refused, never relaxed.** The refusal names the
  constraint and the month, and the day where one is responsible:
  `activity_placement_has_no_candidate` (nothing to place, or a floor above the number of
  activities the catalogue holds), `activity_placement_cannot_fill_a_day`,
  `activity_daily_range_invalid` (a minimum above the maximum, or a negative count),
  `activity_monthly_minimum_unsatisfiable`, `activity_monthly_maximum_below_minimum`,
  `activity_weekday_mask_unreadable` and `activity_limits_inconsistent` (one activity
  recorded twice). Generation itself refuses with `activity_allocation_infeasible` and a
  sentence naming both ends of the range and asking for a lower floor, a wider catalogue or
  a longer range.
- **Deterministic, and the shape of the month is separate from its content.** The draw is
  seeded from the generation seed and the month and candidates are ordered by activity id,
  so the same request produces the same month and a new seed produces a different, equally
  valid one. The daily counts are drawn from their own stream, so editing the catalogue's
  weights changes which activities a day holds without also changing how many it holds.
- **A placement is a day entry.** It carries `kind=activity`, the catalogue's English
  description and its Kannada text unchanged, and `provenance=generated`, with the stable
  identifier `ACT-<date>-<activity id>`. A non-working day carries none: a Sunday, a
  General Holiday and a leave day are not days the office worked. A placement is not
  duplicated where the operator or the calendar already recorded the same activity on the
  same date, and a placement on a locked date is preserved as `locked`, exactly as a
  calendar entry there is. Schema 6 widens the entry table's provenance check for this
  value, and migration 6 rebuilds the table to do it.
- **Where it shows.** The placement reaches the draft preview, the review, the issued
  register page's one Activity cell and the `Register` sheet, because it is an ordinary
  day entry and not a second kind of content.

### 24.9 Register wording (as built)

The words a register is written in are the office's decision, recorded with the generation
that produced the month and frozen into the closed record. Three choices live in
`dailyworkreg/domain/wording.py`, which decides nothing about work and is read by the
generation request, the stored record, the document projection and the HTTP surface, so a
value cannot be spelled one way when it is stored and another when it is compared.

- **Language.** `entry_language` is `en`, `kn` or `both`. The generate page offers the
  three as a visible choice, defaulting to **English**, and the generation route records
  the answer; an absent value is the platform default, never the last one used. A language
  the register is not written in is refused with `entry_language_invalid`, both at the
  request boundary and in the domain, so an unknown value cannot reach an issued record.
  A month issued before the choice existed was bilingual, so a stored request that carries
  no language is read as `both` rather than as today's default: reprinting an old month
  reproduces the document that was filed.
- **A missing translation is not a blank cell.** A Kannada register whose record holds no
  Kannada for an item prints that item in English rather than omitting it. A feeder has no
  Kannada name in the masters, so its visits read in English in every language.
- **The choice is frozen with the month.** It is written into the month's content and read
  back by the export, so a month issued in Kannada stays Kannada after a later generation
  is asked for English. It is deliberately **outside** the month fingerprint along with the
  asset kinds (`PRESENTATION_CONTENT_KEYS`): a month closed before the choice existed must
  still be recognised as itself, or every such month would refuse the next revision of its
  register as though its content had changed.
- **A non-working date says what it is.** The stored reason is kept and the kind of day is
  written beside it in words a reader acts on: `Sunday (Weekly off)`,
  `Second Saturday (Holiday)`, `Fourth Saturday (Holiday)`,
  `Holiday - <name> (Holiday)`, `<leave type> (Leave)`. The Second and Fourth Saturdays are
  recorded as **holidays**, which is the office's own usage: the section is closed by rule
  on those days, and the register uses the word the office uses for a day it does not work.
  The old `Calendar:` prefix is gone, because a bare "Sunday" beside a date reads as the
  date rather than as the reason it carries no work. A kind with no label carries no
  parenthetical rather than a guessed one, and a reason carried in both lists is printed
  once.
- **A closed day the office staffed records what was allocated.** A Sunday, a Second or
  Fourth Saturday, any other weekly off and a General Holiday carry the office's own line
  after their reasons: `Allocated operational and maintenance works to be done on that day
  to staff`. A leave day and a closure that is neither a holiday nor a weekly off do not:
  those are an individual's absence and an unexplained closure, not a closed section that
  still carried work. Like a visit's narrative the line is English in every language,
  because no translation is recorded for it, and an untranslated narrative prints in
  English rather than as a blank line. It is presentation only - nothing is stored on the
  date and no obligation is created by it.
- **A field visit reads as work done.** An asset name alone says where somebody went, not
  what the register is a record of, so a visit is written as a sentence naming the checks
  for the kind of asset the revision's own roster recorded: `Inspected <name> for
  transformer loading, oil level and earthing, and checked the consumer supply` for a DTC,
  a patrolling sentence for a feeder, an area sentence for an area. The asset kinds are
  frozen with the month (`asset_types`), a record that does not say which kind it visited
  reads a neutral sentence instead of a guessed verb, and the narrative is presentation
  only: the visit, its date and its asset remain the record. The narrative sentence is
  English in every language; the language setting decides how the asset is named.
- **A DTC prints its roster number; an area and a feeder print their names.** `SCD-44` is
  how transformer work is filed, reported and defended, so a DTC entry carries it in
  brackets beside the name, e.g. `Inspected DIST TRAINING COLLEGE [SCD-44] for transformer
  loading, oil level and earthing, and checked the consumer supply`. An area or a feeder is
  named by its name, and `A-15` or `F-01` stays in the record alone: those are how the
  record finds the row again, not how the office names a street or a feeder in a document
  it files. `DocumentVisit` and the stored record carry `asset_id` either way, so a printed
  visit remains traceable to its installation.
- **A month closed before this change reads differently.** Its record carries no language
  and no asset kinds, so it is read as the bilingual register it was and its visits read
  the neutral sentence. Reissuing it in another wording is a correction revision
  generated under this contract, not an act the platform performs on a closed month.

### 24.10 The daily numbers the generate tab sets (as built)

A working day carries a separate ceiling for each kind of visit and a range for its regular
activities, and those are independent of one another: a date of transformer checks has not
spent the area or feeder allowance, and an activity never takes a visit's place. All of them
are stated in the generation request, because a generation used to be refused by numbers the
operator could neither see nor change.

- **A maximum per kind, not one total.** `dtc_visits_per_day`, `area_visits_per_day` and
  `feeder_visits_per_day` on the request; *DTC / Area / Feeder visits per working day -
  maximum* on the form; defaults 3, 2 and 1. Internally these are the validator's
  `dtc_daily_cap`, `area_daily_cap` and `feeder_daily_cap`. The request names them in the
  operator's words because a form that carried a constraint's identifier would be the browser
  restating a rule.
- **The retired total.** `field_visits_per_day` is still accepted and read as a ceiling on
  the whole date, which is what it always was, so a request or snapshot written before the
  kinds were separated generates the schedule it was issued with. It is no longer defaulted,
  and `combined_daily_cap` is `None` unless something states it: with three independent
  ceilings a day's total is their sum, and a fourth bound applying by default kept refusing
  months the ceilings hold. That was the defect this change removes - a 68-visit month
  against a stated total of 2 per working day, refused although no kind was over its own
  ceiling.
- **Only activities have a minimum.** The minimum per working day belongs to the approved
  catalogue alone (§24.8), and the form says so. No kind of visit carries a floor: the
  office owes transformer, area and feeder visits through the cycle obligations and the
  monthly minimums, not through a daily floor.
- **Why the numbers had to move.** They were platform constants read from nowhere, although
  §10 already recorded them as configurable with initial defaults. An office whose own masters
  ask for more monthly visits than the ceilings allow had a month refused with no way to act
  on it. `DEFAULT_DTC_DAILY_CAP` is 3 because the retired total of three effectively gave
  transformers that allowance, and transformers carry the bulk of the demand: on the office's
  own files a month owes 67 visits across 22 working dates, 37 of them transformer checks.
- **What is refused.** A negative ceiling of any kind (`daily_visit_cap_invalid`), and all
  three at zero, which would leave no date able to hold a visit. Nothing is silently clamped,
  and an absent value keeps the platform default rather than becoming zero.
- **A capacity refusal names the kind and the number that would fit the month.**
  `aggregate_dtc_capacity_exceeded`, `aggregate_area_capacity_exceeded`,
  `aggregate_feeder_capacity_exceeded` and, when a total is stated,
  `aggregate_combined_capacity_exceeded`, each end with "at least N visits a working day
  would fit them", computed as the ceiling of that kind's demand over the eligible dates, so
  the operator is told the number to enter instead of dividing the shortfall themselves. A
  kind is proved on its own, so a month whose areas alone exceed the area ceiling is refused
  naming the areas rather than as an unattributable shortage of total capacity.
- **Recorded with the draft.** Each ceiling lives in the request snapshot's `config`;
  schema 5 adds the per-kind fields beside the total. A schema 1 to 4 snapshot is still read,
  its total keeping its meaning as a whole-date ceiling while the kinds it never bounded
  separately take the platform defaults. The activity range has its own fields since
  schema 4, and a schema 1 to 3 single count is read as the range whose ends are equal.

### 24.11 The Monday safety meeting is not one of the numbers (as built)

The office asked for the meeting held every Monday to stay out of every daily count, and it
does, at each layer that counts anything.

- **It is an entry, not a visit.** `safety_meeting_entries` projects one `DayEntry` of kind
  `safety_meeting` onto every Monday, holidays and leave days included (§4.2). Visit ceilings
  are evaluated over `Visit` objects only, so the meeting cannot be charged to them: a March
  Monday holds its transformer duty exactly as a Tuesday does, and a test asserts it on the
  month the office measured.
- **It is not one of the day's activities.** `allocate_activities` counts the placements it
  makes from the approved catalogue, so a Monday recording the meeting and four activities
  records four, not five, against the ceiling and not three against the floor. The meeting
  cannot discharge an activity minimum either: a catalogue that cannot meet the floor is
  refused with the whole shortfall named, whatever the calendar holds that day.
- **It still prints.** Exclusion from the counts is not exclusion from the register. The
  meeting is one of the date's items, printed beside the visits and the activities in the
  fixed order of `DISPLAY_ITEM_ORDER`, because a register that dropped what the office did on
  a Monday would be wrong in the other direction.
- **The form says so.** The generate tab states in one sentence that the kinds are
  independent and that the Monday meeting is not counted against either number, because a
  rule that only lives in code is one the office has to guess.

### 24.12 A long generation says how far it has got (as built)

- **Why it exists.** A ten-month register is one click and can take a minute. Without a
  report, a request that is working and a request that has wedged look identical on screen:
  a button that was pressed and a page that has stopped changing. The bar exists to answer
  one question - is it moving - so what it says has to be true, ordered, and impossible to
  confuse with an outcome.
- **It is a run with an id, not a long request.** `POST /api/generations/jobs` answers 202
  with the run's id and its first state, and `GET /api/generations/jobs/{job_id}` answers
  how far it has got. The work runs on its own thread against its own database connection,
  because a request's connection is closed when its response is sent. The plain
  `POST /api/generations` route remains for a script or a test that wants the result itself.
- **One run at a time.** A second request while one is going is refused with
  `generation_already_running` and the id of the run in progress, so the page can attach to
  that run's bar instead of leaving the operator with a refusal and no view of the work. Two
  runs would compete for the same database and the same bar.
- **The progress is the server's.** The stages are reading the masters, placing field visits
  month by month, placing the approved activities, writing the draft, done; the percentage is
  computed in one function (`progress.percent_for`) so two places cannot disagree about what
  60% means, and it never decreases even when a report arrives out of order. During a hard
  month the report carries the attempt count, which is what shows that a month is still being
  worked on rather than stuck.
- **Watching changes nothing.** The allocator and the cycle runner take optional reporting
  hooks and read nothing back from them: `AllocationRequest.on_attempt`,
  `run_cycle(on_month=, on_attempt=)`, `run_fallback(on_month=, on_attempt=)` and
  `generate_draft(request, progress=)`. Tests generate the same month and the same
  three-month cycle twice, once watched and once silent, and require the rows, the visits and
  the plan to be identical - a register that changes when the operator looks is not a
  register.
- **The bar is a state readout, not a decision.** The page sends nothing about progress,
  computes no percentage and decides no ordering: it draws the server's percentage with the
  server's sentence and adds the elapsed clock, because a bar that has not moved for a minute
  is not stuck if the clock beside it is still counting. A finished run keeps its result in
  the record, so a poll that arrives after completion still answers.
- **A refusal is an ending, not a silence.** A run that is refused ends `failed` with the
  constraint and the sentence the office reads, and the page shows it through the same banner
  a direct refusal uses. The records live in memory only and the oldest are dropped: the only
  reader is the page that asked for the run, and a restart must not resurrect a half-written
  register as though it were still being written.

### 24.13 What is not modelled

- Partial-month generation, and therefore the use of the section 23 proration rule to
  build a schedule. A part-month is still refused.
- Reopening a finalised month, in place or by any other act.
- Prorating the catalogue's monthly activity minimums and maximums for a partial month,
  which the requirements ask for once partial months exist (§6).
- A variety rule for the daily activity range beyond the seeded draw, and no per-weekday
  count: the requirement's "three to six" is implemented as one floor and one ceiling for
  every eligible working day, drawn per day from the seed.
- An activity's applicability to particular areas, feeders or DTCs: the catalogue has no
  such column, so a placement is office-wide.
- A daily minimum on any kind of visit. Only the catalogue has one, which is the owner's
  decision; a transformer, area or feeder floor per working day is not modelled.
- A recorded reason for a closed day's allocated work beyond the one sentence
  `STAFF_ALLOCATION_NOTE`. Which works were allocated, and to whom, is not recorded.
- A Kannada narrative sentence for a visit, and Kannada names for feeders, which the
  masters do not store. The language setting governs names and activity text, not the
  sentence a visit is written in.
- A per-month or per-register language other than the one recorded at generation. Changing
  a closed month's language is not an act the platform offers; a different wording means a
  correction revision generated in that language.
- Authentication or multi-operator attribution. The note is the only human record.

## 25. Persistence guarantees beneath a finalised month (as built)

The application layer refuses to change a closed month before any of it reaches SQLite.
The database is also expected to refuse it, because a route can be changed, a script can
be written and a future refactor can forget. Section 24.3 records the guarantees the
application makes; this section records what the storage guarantees on its own.

### 25.1 Three operations, not two

Over each of `generation_row`, `generation_visit`, `generation_entry`, `date_review` and
`date_lock`, an insertion is covered as well as an update and a deletion, because a row
appended to a month that has already been closed leaves the approved record describing
something other than what is stored. Each trigger names the month of the row being
inserted — `visit_date` for a visit, `row_date` for the rest — and the revision it belongs
to, and aborts with `finalised_revision_is_immutable` only when a `month_finalisation` row
exists for that revision and that month. Consequently:

- An insertion into a closed month of a revision that was finalised for it is refused.
- An insertion into a month of the same revision that has not been closed is ordinary
  work, which is the whole point of finalising a month at all.
- An insertion into a successor correction revision is allowed, because it carries a
  different revision and a correction is how a closed month is changed.

`month_finalisation` itself is written once, at finalisation, so it carries the update and
delete triggers and no insert trigger: closing a month is an insertion into it.

### 25.2 A migration already applied is repaired by a new migration

An applied migration is history. Migration 4 reached databases in two shapes while it was
being written — keyed by `run_id` alone, and with the month in the trigger predicate in one
version and out of it in another — and a database that applied the earlier text keeps that
shape for ever, because a migration is not re-run when its source text later changes.
Migration 5 corrects it in place and is written to be harmless on a database that is
already correct.

It asks the database which shape it holds rather than trusting the version number, which
says migration 4 ran and not which text ran: the test is a unique index over exactly
`run_id`. When that index is present the record table is rebuilt — renamed, recreated with
`UNIQUE (run_id, month)` and `UNIQUE (register_id, month, sequence)`, every column of every
row copied across, the old table dropped, the row count verified — because SQLite cannot
alter a constraint in place. Identity, sequence, content snapshot, acknowledgements,
readiness, master version and note survive because the primary key is copied rather than
regenerated, and the month pointer survives because it names a `finalisation_id` that the
copy preserves. The finalisation triggers are dropped and reinstalled in both cases, in the
month-scoped form and covering insertion, so the earlier trigger shapes converge on one
set.

Migration 5 runs inside the existing migration transaction, so a failure leaves a database
at schema 4 with its data intact rather than half-repaired, and it is repeatable: on a
correct database it rewrites the triggers to identical text and changes nothing. Migration
4 is untouched, so a fresh installation replays 4 and then 5 and reaches the same shape as
an upgraded one. `SCHEMA_VERSION` is 5.

**Migration 6 dropped three guards and restored none; migration 8 puts them back.**
Migration 6 rebuilt `generation_entry` to widen its provenance check, and dropping a table
drops the triggers that live on it. The step written to reinstall them filtered the trigger
statements for that one table with the substring `" ON generation_entry "`, and the text is
`BEFORE UPDATE ON generation_entry` followed by a newline, so the filter matched none of the
statements and installed nothing. Every database that upgraded across schema 5 therefore
reached schema 7 without `trg_finalised_generation_entry_insert`, `_update` and `_delete`,
and nothing reported it: the guards are only counted when a backup, a recovery copy or a
restore is being prepared, so the first symptom an office saw was a backup refused with
`backup_protections_are_missing`. Between those two points a direct write could append,
change or delete a row of an issued month; the application layer still refused it, the
database did not.

Migration 8 is that repair: it drops every finalisation trigger by name, creates the
complete set from `_finalised_content_triggers()` inside the migration transaction, then
verifies that each expected name is present and refuses the whole migration if one is not,
so a database cannot be left half-guarded and silent. It touches no row.
`_apply_migration_6` now reinstalls the whole set after its own rebuild as well, so a
database migrating from schema 5 does not depend on migration 8 to be correct.
`SCHEMA_VERSION` is 8.

### 25.3 What is still not guarded

- A row deleted and reinserted elsewhere inside one statement is beyond a trigger's reach;
  the guarantee is per row and per month.
- A database opened with `PRAGMA writable_schema` or with triggers dropped by hand is
  outside the guarantee, as with any SQLite trigger.
- Nothing here detects a tampered record: the fingerprint stored with it identifies the
  content that was issued and is compared by the application, but verifying a whole
  database against its own records is a separate act.

## 26. Implemented document export contract (as built)

A finalised month can leave the tool as a spreadsheet and as a printed page. Both read the
record that was issued and neither re-derives anything, because the question an export
answers is "what did the office approve", not "what would the platform arrange today".

### 26.1 The record is the only input

- `dailyworkreg/application/export.py` is the single reading of a finalisation into a
  presentable document, and both formats consume it. A second mapping would be a second
  interpretation of what was approved.
- It calls `finalisation.finalisation_record` and reads the stored `content_json`. It
  reaches no master table, no calendar projection, no allocator and no generator. A test
  asserts that the export path contains none of those calls, so a later change cannot
  reintroduce the dependency quietly.
- The identifier supplied names the version. An unknown identifier is refused under
  `finalisation_unknown`; there is no fallback to the month's latest version, because a
  fallback is how a download of one document delivers another.
- Every stored date is present, in the stored order, including Sundays, General Holidays,
  leave days and dates carrying nothing at all. A date with no visit reads as the
  register's own mark rather than disappearing, and a date with several visits or several
  entries keeps all of them, in the order they were stored.
- Each date is reduced to one Activity cell, built by `DocumentDay.activity_text` in
  `application/export.py`, and that one value is what both the workbook and the page
  render. The order is fixed: field visits, then activities and other entries, then the
  calendar reasons, then the line a staffed closed day carries, then the recorded warnings;
  each group keeps its stored order and the separator is a comma and a space. A field visit
  is printed as a sentence naming the work, with the asset's name and - for a DTC only - its
  roster number; a stored Kannada name or title is printed beside its English text and is
  never translated, transliterated or dropped. A calendar reason carries its classification
  once beside the first reason (`Sunday (Weekly off)`) and a warning is prefixed
  `Warning:` so neither can be mistaken for work performed. A date with nothing recorded
  reads `—`. Because the value is computed once and consumed twice, the two documents
  cannot disagree about what a date says; a test compares them row by row.
- Labels are formatting, not content: a day kind, an entry kind and a visit provenance are
  rendered through fixed label tables, and the stored token is never rewritten in the
  record. Nothing is filled in where the record holds nothing, and no column is calculated
  from another.

### 26.2 The workbook

- `GET /api/finalisations/{finalisation_id}/export.xlsx` returns a real XLSX file with the
  OOXML media type and a filename built from the month and the issued sequence only, so
  the header carries no user-supplied text and no path separator.
- Two sheets. `Register` is the office's register: a centred heading, then exactly three
  columns — `Sl No.`, `Date`, `Activity` — and one row per stored calendar date, with the
  serial restarting at 1 in every monthly workbook. The `Activity` cell is the same value
  the printed page shows, so the two documents never disagree; a date cell is a real date
  value displayed `yyyy-mm-dd`, and an empty date keeps its row and reads `—`. Nothing is
  placed beside those three columns. `Metadata` identifies the document: month, register,
  issued version, the revision it closed, the finalisation id, when it was closed, whether
  it is in force or superseded, the content fingerprint, the master version at
  finalisation, what it corrects or supersedes, the locked dates, the operator's note, the
  findings acknowledged when it was issued, the master readiness measured at finalisation,
  the month's recorded and declared obligations, the stored evidence, and how many of its
  visits were committed before generation rather than allocated.
- A stored string that begins with `=`, `+`, `-` or `@` is written as text rather than as a
  formula, so an imported activity title or a note is data and never code. The joined
  Activity cell is subject to the same rule.
- The workbook's own metadata timestamps are taken from the record rather than from the
  clock, so the same issued version downloads as the same file.

### 26.3 The printed page

- `GET /finalisations/{finalisation_id}/print` returns a standalone document, not a view
  inside the application: it has no navigation, no tab bar and no application chrome, so
  printing cannot carry the tool's interface into the register.
- The register is headed the way an office file is headed: `DAILY DAIRY REGISTER`, then the
  month and year in capitals, both centred, and nothing else. The identification of the
  issued version is not in the masthead.
- The register table has exactly three columns — `Sl No.`, `Date`, `Activity` — and exactly
  one row per stored calendar date, in the stored order. There is no second table, no daily
  section and no per-visit or per-entry heading: a date's items are joined into its one
  cell. The column headings repeat on every content page, a row is kept off a page break
  where the engine allows, and a long activity description wraps rather than being clipped.
- The identification is one page of its own, so it can never begin halfway down the final
  page of dates. It is a **sheet of its own** - a sibling of the register sheet in the
  markup, not a section of it - so the break it relies on is the same one that starts a new
  month, which browsers and the PDF engine both honour; a break declared on a block that
  follows a table is not honoured reliably by the engine the PDF is written with. Only one
  break is declared: a sheet break and a section break stacked would leave a blank page
  between them. On screen it reads as a second page-like card. It carries the
  register, the month, the issued version and its standing, the revision closed, the
  finalisation id, when it was closed, the content fingerprint, the master version at
  finalisation, the month and allocator status, the correction relationship where one
  exists, the locked dates, how many field visits were recorded and how they were placed,
  the operator's note, the findings acknowledged when the version was issued, the master
  readiness measured at finalisation, the recorded and declared obligations, and the stored
  evidence. It is printed once; the metadata is not repeated under the last page of dates.
- `@media print` hides the toolbar and the controls, drops the screen's page frame, and
  states A4 portrait with print margins. The browser's own print dialog remains one way to
  produce a PDF; §26.7 describes the PDF the tool now writes itself.

### 26.4 Bulk monthly package

- `GET /api/registers/{register_id}/exports.zip` packages the latest in-force issued
  version of each finalised month in that register. Month membership and version identity
  come from the month lifecycle pointers; the route does not select a revision by date,
  sequence guess or live register state.
- `excel/` contains one XLSX workbook for each included month, using the same document
  projection and workbook renderer as the individual monthly download.
- `print-ready/` contains one combined HTML document with the included monthly sections
  in calendar order. The print template accepts one document or an ordered collection; the
  combined form inserts a page break before every following month. Printing once produces
  one combined PDF with separate monthly sections when the operator chooses Save as PDF.
- `README.txt` identifies the included issued versions and lists months without an issued
  finalisation that were omitted. A draft or unfinalised month is never substituted. If
  the register has no issued months, the endpoint refuses the package.
- The ZIP and combined print view are read-only projections of immutable finalisation
  records. No rows, month pointers, reviews or revision state are created or changed.

### 26.5 Standings and history

- A version is exported as it stood when it was issued. If a later correction issues a new
  version, the earlier one still exports its own snapshot and is labelled superseded, and
  the newer one is labelled in force. Each is reached by its own identifier.
- A change to the live master data after finalisation cannot reach an issued document; a
  holiday added inside a closed month cannot either. Both are asserted by exporting the
  same version before and after the change and requiring identical output.
- Exporting is a read: it writes no row, moves no pointer and leaves the record, its
  snapshot and its fingerprint exactly as they were.

### 26.6 PDF written on this computer

- `dailyworkreg/infrastructure/rendering/pdf.py` turns a printed register into a PDF with
  a Chromium installed on this machine, driven by Playwright. Nothing leaves the computer:
  the page is set as content in a local browser and the browser's own PDF writer produces
  the file. No service, no network call and no external renderer is involved.
- The page description is the template's, not a second copy in Python. The renderer asks
  Chromium to prefer the CSS page size, so the A4 portrait, the margins, the repeated
  column headings and the break rules printed here are the ones the operator sees in the
  browser. A layout change is made once, in the template.
- One browser serves one download. A bulk package opens a single browser and one page and
  renders each month in it, because starting an engine per document costs seconds and
  buys nothing. The browser is opened through a context manager and closed on the way out,
  so a failed render cannot leave one running.
- A missing engine is a setup step, not a fault in the register.
  `python -m playwright install chromium` installs it once per computer, outside the
  checkout and outside the virtual environment. When it is absent the request is refused
  with `pdf_renderer_unavailable` and a sentence naming that command; when a document
  fails to lay out it is refused with `pdf_rendering_failed`. Both answer 503, because
  neither is a bad request. No partial file is ever written: half a package would read as
  a complete one.
- `GET /api/finalisations/{finalisation_id}/export.pdf` returns one issued month as a PDF
  named from the month and the issued sequence, and the printed page remains available for
  the browser's own print dialog. Both read the same document projection as the workbook.

### 26.7 One-click packages, one format at a time

- `GET /api/registers/{register_id}/exports/pdf.zip` holds `pdf/` with one genuinely
  rendered PDF for each issued month, plus `README.txt` naming every included month, its
  issued version, its finalisation and its file, and naming each month omitted for having
  no issued finalisation.
- `GET /api/registers/{register_id}/exports/excel.zip` holds `excel/` with one workbook for
  each issued month — same `Register` and `Metadata` sheets as the single-month download —
  and the same README.
- Both select months the way the mixed package does: the latest in-force issued version of
  each finalised month, from the month lifecycle pointers. A register with no issued month
  is refused with `register_has_no_issued_months`.
- The mixed `exports.zip` keeps its contract and its filenames. The two new packages exist
  because a download that promises PDFs and holds an HTML file sends the operator looking
  for a fault in their own reader; the mixed package is left in place for older links.
- All three are read-only projections of immutable finalisation records. No row, pointer,
  review or revision state is created or changed by a download.

### 26.8 What is not modelled

- No PDF is written where no local browser is installed, and the renderer is not bundled:
  the operator installs Chromium once, and the refusal names the command.
- No export of a draft or of an unfinalised revision: there is nothing approved to export,
  and the identifier of a revision is refused as unknown.
- No watermark, signature block, digital signature, masthead logo, letterhead or
  pagination design beyond what the browser prints.
- No export history or download log.

## 27. Implemented backup and restore contract (as built)

This section records what the tool actually does, so a later change reads a decision rather
than inferring one. It adds no policy. Requirements §13 is unchanged: a backup is not part
of the register lifecycle, and restoring one does not reopen, un-finalise or re-issue any
month.

### 27.1 The database is the whole of the operational state

The only operational state is the SQLite database resolved by
`infrastructure/sqlite/database.py`: the `--db` argument, then `DAILYWORKREG_DB`, then
`%LOCALAPPDATA%\DailyDairyRegister\dailyworkreg.db` (or the XDG directory, or the home
directory, on other platforms). The only other filesystem write anywhere in
`dailyworkreg/` is the creation of that directory.

There is therefore no second store to back up: no cache file, no settings file, no export
directory, no temporary draft. A backup of the one database is a backup of everything —
masters and their revisions, staged imports and their provenance, every register, revision,
row, visit, entry, review, lock and audit row, every issued finalisation with its stored
snapshot, fingerprint, acknowledgements and readiness, every month pointer, and the
`schema_migration` rows that say which shape the file is.

Two consequences are recorded rather than implemented: a backup is not encrypted, and
nothing is retained by the tool. Encryption, key management and retention are separate
decisions and were not taken here.

### 27.2 The package format

One file, a DEFLATE archive, holding exactly two entries:

| Entry | Content |
| --- | --- |
| `manifest.json` | UTF-8 JSON, `sort_keys`, describing the package |
| `database.sqlite` | the consistent copy of the database |

The manifest carries `format` (`dailyworkreg-backup`), `format_version` (1),
`created_at` (UTC, ISO 8601 with microseconds), `application` (`name`,
`schema_version`), `payload` (`name`, `bytes`, `sha256`, `journal_mode`) and `contents`
(sixteen non-secret totals: registers, revisions, finalisations, finalised months, rows,
visits, entries, reviews, locks, DTCs, areas, feeders, activities, holidays, import
batches, staged rows).

The format is versioned so a future change is a new version rather than a reinterpretation
of this one. A reader refuses a `format_version` it does not know; it does not guess.

The file name is derived from the tool's own facts — `daily-dairy-register-backup-<UTC
timestamp>-schema-<n>.zip` — never from anything an operator typed, so no filename can
carry a path separator, a control character or a claim about what is inside. The name is
not trusted on the way back in: the manifest and the payload decide, and the entry names
are matched against the format exactly.

A manifest is a claim, not a credential. The payload's SHA-256 proves the *database* is
unaltered; it says nothing about the description beside it, and an archive can be rebuilt
with the same payload and a different manifest. So every claim the manifest makes about
the database is compared with the database before the file is called acceptable:

- `application.name` must be this tool's, so another product's archive is not mistaken for
  a backup of this office;
- `application.schema_version` must be a whole number of at least 1, and must equal the
  schema version actually read from the payload — a package that claims to have been made
  at an older or newer shape than it holds is refused
  (`backup_manifest_does_not_match_the_database`) rather than believed;
- `contents` must be an object of counts, every key one this format defines, every value a
  non-negative whole number (a boolean is not a count, and neither is a string or a list),
  with no count missing and no count invented;
- every declared total must equal what the database actually holds, and a disagreement
  names the totals that differ, both declared and actual.

A malformed description is a refusal with a stable identifier, never a conversion error:
the summary an operator reads is produced defensively, so a boolean where a number belongs
reaches them as "the description of this backup is not usable" rather than as a crash. The
comparison happens after the payload has been read and before the staged copy is written,
so a refused package leaves nothing beside the live database.

### 27.3 A consistent copy without disturbing the desk

The copy is made with `sqlite3.Connection.backup` from the live connection. That API reads
one consistent snapshot while other readers and a writer continue to work, which is why a
backup can be taken from inside a request without stopping the tool. The result is
detached from the write-ahead log: the snapshot is opened, `journal_mode = DELETE` is
enforced and any sidecar left by an earlier attempt is removed, so the payload is a single
self-contained file.

What this guarantees, and what it does not:

- Committed work is in the copy, including work committed before the backup request was
  served.
- A transaction that was still open when the copy was taken is not in the copy. It is not
  half in it either: the snapshot sees a committed state.
- The live database keeps working during and after the backup, and taking a backup writes
  no row to it.
- The payload is an ordinary SQLite database that other tools can open; it is not a
  proprietary container.

Before the package is offered, the snapshot is inspected: integrity, foreign keys, schema
version, required tables, the immutability triggers, the structure of the issued records,
readable snapshots and present fingerprints. A copy that fails any of these is not offered
as a backup under a different name; the backup fails and says why.

The seventeen finalisation protections are part of that bar, not a nicety. A database that
holds every row but has lost a trigger can no longer keep a finalised month final, so it is
not handed back as a verified backup and is not accepted as a recovery copy: the refusal is
`backup_protections_are_missing` and it names the triggers that are absent. Readability is a
weaker question and is asked separately, which is what lets a payload written at an older
schema reach its migration even though it cannot yet carry the protections of this one; the
same bar is applied to the staged copy after migration, so nothing is installed without
them.

The copy helper itself is accountable for two things beyond the copy. It never commits,
rolls back or otherwise moves a transaction the caller began: SQLite's backup reads a
committed-only view, so taking one while a caller's change is open would describe a state
nobody agreed to, and committing it there would be a write made on the caller's behalf. It
refuses instead (`backup_database_has_an_open_change`, through `CopyRefused`), leaves the
transaction exactly as it found it, and the backup fails closed — no package, no success. An
ordinary request reaches this code with a fresh connection outside any transaction, so the
everyday flow is unchanged. For the same reason the helper refuses to write over a file that
already exists unless it is explicitly told to; a recovery copy can therefore never be
destroyed by a later attempt reusing its name.

Everything happens in a private temporary directory that is removed whether the backup
succeeds or fails, so no database, no archive and no leftover file is written into the
source tree or beside the live database.

### 27.4 Validation is a decision about a file, made before anything is replaced

`POST /api/backup-validation` accepts one uploaded package and returns either the facts an
operator needs to decide, or a refusal with a stable identifier. The order is deliberate:
a newer schema is refused before the payload is examined, because reading it with an older
build is exactly what must not happen; then the archive is read with a byte cap; then the
manifest; then the entry names; then the checksum; then the payload is opened (read-only,
`mode=ro`) and inspected.

It checks, in this order: readable as an archive; manifest present and shaped — including
the application name, a whole-number schema version, and a `contents` map of non-negative
whole numbers with no unknown key; format name and version; payload present; no unexpected
entry, no duplicate entry, no directory, no symlink, no absolute or climbing path; declared
and actual sizes under the limits; payload SHA-256 against the manifest; the file is a
SQLite database; `PRAGMA integrity_check`; `PRAGMA foreign_key_check`; schema version not
newer than this build; required tables present; the issued records structurally consistent
(each finalisation names a run that exists, its parent exists, its correction names a run
that exists, every month pointer names a record **of that month and that register**, and
only one correction branch is open per month); every declared total equal to what the
database holds; every `content_json` parses and every fingerprint is non-blank. The
immutability triggers are then required on the prepared copy, after migration, so an
older payload is not rejected for the protections it never had, and nothing is installed
without all seventeen (`backup_protections_are_missing`, naming them).

An older supported schema is migrated **on the staged copy** with the existing migrations,
and the migrations that ran are reported to the operator. The live database is not migrated
and not touched, the backup file is not modified, and migration history is not edited.

Limits are read from `DAILYWORKREG_BACKUP_MAX_BYTES` (256 MiB),
`DAILYWORKREG_BACKUP_MAX_PAYLOAD_BYTES` (1 GiB) and a fixed 64 KiB manifest cap. Exceeding
one is a refusal (`backup_package_too_large`), checked while streaming so an oversized
upload is not written to disk in full.

The staged copy is written to `<database>.restore-<token>.staged` in the same directory as
the live database — same volume, therefore replaceable by rename — and its hash is
recorded. Stale staged copies from an interrupted attempt are pruned, except the one an
outstanding confirmation refers to. A package refused after its staging copy was written is
removed again, so a refused upload leaves nothing beside the live database for someone to be
talked into installing later.

### 27.5 The replacement is gated, recoverable and verified

`POST /api/restore` requires two things the validation did not supply: the word `REPLACE`
and a token that is still outstanding in this process. Neither is derivable from the file,
so selecting a file cannot replace a database.

Then, with the exclusive side of `MaintenanceGate` held:

1. The staged file's hash is re-checked against the hash recorded at validation, so a file
   changed after it was accepted is refused (`restore_replacement_failed`, "the validated
   copy is no longer on disk or has changed").
2. Open connections are given up to 30 seconds to close; new requests are refused with
   `restore_in_progress` meanwhile. A timeout releases the barrier and refuses the restore
   rather than replacing a database somebody may still hold.
3. `PRAGMA wal_checkpoint(TRUNCATE)` runs and the `-wal`, `-shm` and `-journal` sidecars
   are removed. A sidecar that cannot be removed is a refusal, not a warning, because a
   remaining log would be replayed onto the new file.
4. A rollback copy is taken at `<database>.pre-restore-<UTC stamp to the microsecond>.db`
   and inspected; if it is not a healthy database the restore stops there and the copy is
   removed, because a file that failed its own checks is not a recovery copy. The name is
   chosen before a byte is written and never reused: the timestamp carries microseconds and
   a name already taken is pushed to the next free numeric suffix, so a second restore can
   never overwrite the first one's copy. Both copies stay readable and the answer names the
   one belonging to this restore.
5. The staged file is installed with `os.replace`, which is atomic on one volume and on
   Windows refuses while a handle is open — the failure mode the barrier exists to avoid.
6. The result is verified: schema version, integrity, foreign keys, all 17 triggers, the
   structure of the issued records, and then the database is opened through the ordinary
   `open_database` path to read every register, every issued version and the counters.
7. On any verification failure the previous database is put back from the rollback copy and
   the answer says so: `restored: false`, `restore_verification_failed`, and the name of
   the recovery copy.

A failed replacement leaves the live database as it was and reports the rollback copy. A
restore that cannot be verified is never reported as a success. The rollback copy is left
in place for the operator to delete; the tool has no retention policy and deletes nothing.

### 27.6 What the operator sees

The Backup tab is three steps, in the order they must happen:

1. **Create a backup** — one button. The answer shows the file name, the creation time, the
   size, the format and schema versions, the integrity and reference checks, the totals,
   and a download link. The backup is held in memory by the running process, so the
   download must happen before the application is closed; the page says so.
2. **Check this file** — choose a file, then check it. The answer shows what the file is,
   what it holds, whether it needs bringing forward, and any warning. Nothing is revealed
   before the check succeeds.
3. **Replace the database** — the confirmation block appears only after a file was
   accepted, and the word to type is the one the server reported, not one printed in the
   page. The result shows what was verified, the name of the recovery copy, and a reload
   button, because the page is holding data from the database that has just been replaced.

The client holds no rule: it does not compare schema versions, does not read the archive,
does not decide whether a file is restorable, does not know a snapshot from a fingerprint,
and does not parse a message to decide what happened. Refusals are shown as the server's
sentence.

### 27.7 What is preserved, proven

The tests build the state an office has and compare whole rows and stored snapshots before
and after a round trip, not counts:

- masters (`dtc`, `area`, `feeder`, `activity`, `general_holiday`), staged imports with
  their provenance (`import_batch`, `import_staging_row`), registers, revisions, rows,
  visits, entries, reviews, locks, audit rows and decisions;
- three issued finalisations across two months from one revision plus a correction, their
  `content_json`, fingerprints, `acknowledged_json`, `readiness_json`, sequences, parent
  and correction links, and the month pointers that name them;
- the 17 triggers, still installed and still refusing an insertion into a closed month and
  a change to a finalised record;
- the exports: a superseded version still exports itself, the printed page is byte-identical
  to what it was before the restore, and a correction made afterwards leaves both earlier
  versions exactly as they were.

The workbook is compared by what it holds rather than by its bytes, because the XLSX
archive carries entry timestamps written at the moment of the export; byte identity between
two writes of the same version therefore holds only within one timestamp tick. That
limitation is recorded here as a known property of the XLSX writer rather than fixed in this
change, which was authorised to export, not to alter the export.

### 27.8 What is not modelled

- No encryption at rest, no passphrase, no key management. A backup is a readable SQLite
  file inside a zip, and the operator is responsible for where it is kept.
- No signing: the manifest is checked against the database it describes, which catches a
description that disagrees with the payload, but nothing proves who produced either. A
backup is trusted because it was produced here and kept by the office, not because it
carries a credential; signing would need key management, which is a decision not taken.
- No retention or rotation: the tool keeps nothing, deletes nothing it did not write, and
  has no schedule.
- No incremental or partial backup, no table selection, no merge restore.
- No restore on top of a running second copy of the tool, no remote or shared database, and
  no restore from a file the tool did not produce.
- No overwrite of the live database without the confirmation word, and no route that
  accepts a filesystem path instead of an upload.
- No audit trail of restores beyond the recovery copy's name in the answer and the server
  log; there is no restore history table.

### 27.9 Starting a new office in the same installation (as built)

One installation holds one office. Handing the computer to a different office means
removing every record the present office entered, and it happens as one act rather than by
deleting table by table.

**Two scopes, each its own action.** *Clear everything* leaves the tool exactly as it was on
its first start. *Clear everything, but keep the calendar* keeps the General Holiday list
(`general_holiday`), the leave types (`leave_type`) and the three weekly-off settings
(`calendar.sundays_off`, `calendar.second_saturday_off`, `calendar.fourth_saturday_off`),
because those are the state's facts rather than the office's and any office in Karnataka
works from the same ones. Everything else goes in both scopes, including the office's own
leave records, dated events, date overrides, staged imports, masters, drafts, reviews,
locks, closed months and every version issued from them.

**The mechanism is the restore path, not a second one.** `application/reset.py` builds a
new database from this build's own schema at a staging path beside the live file, copies the
kept rows into it by column, and hands it to `backup_restore.restore` - so a reset inherits
the digest check, the verified recovery copy, the same-volume rename, the post-swap
verification and the database-in-use refusal that a restore already has. An interrupted
reset therefore leaves either the old database or the new one, never a mixture.

**What it refuses and what it states.** `POST /api/reset` requires the confirmation word
`RESET` (matched case-insensitively, whitespace trimmed) and one of the two scope names, and
refuses with `reset_refused` and a sentence otherwise; nothing is removed on a refusal.
`GET /api/reset/preview` counts what the selected scope would remove before the decision.
The reset runs inside `MaintenanceGate.exclusive()`, so a request holding the database open
makes it wait and a running replacement makes it refuse. A backup of the database as it
stood is taken first and held for download (until the process ends), and the replacement
keeps its own recovery copy beside the live file, named in the answer. Export files already
written to disk are left alone: they are the previous office's issued documents, and
removing them is a third decision the operator did not make.

**The next start does not put the old office back.** The application seeds the two leave
types and **no place at all** — not one area, not one feeder — because a place is the office's
own record and arrives from its own master files. So a starting database cannot hand the new
office sixteen areas belonging to the previous one; that failure is removed at the source
rather than papered over by the reset. The seed is keyed by `seed.approved_identities` and the
reset still writes that key with `CLEARED_SEED_VALUE`, so the next start does not even
re-insert the leave types an operator may have deleted on purpose. `SEED_SETTING_KEY` is the
only setting either scope writes; clearing the key is what would let the seed run again.

**Also forgotten with the data.** A finished generation's in-memory record is dropped
(`GenerationJobs.forget_finished`), because it names a draft no table holds any more, and a
pending restore staged against the previous database is discarded.

