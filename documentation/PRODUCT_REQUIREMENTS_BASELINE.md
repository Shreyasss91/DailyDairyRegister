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

### 10.6 Corrected necessary feasibility conditions and deterministic test vectors

Notation for the selected period (a full month or a partial month):

- `W` = number of eligible working dates in the selected period.
- `C` = combined daily field-visit capacity per working day (initial default 3).
- `A` = actual required area visits in the selected period.
- `D` = actual required DTC visits in the selected period.
- `F` = actual required feeder visits in the selected period.

Necessary aggregate conditions. Both of the following are necessary conditions for any feasible schedule. Neither is sufficient:

```
A           <= 2 * W
A + D + F   <= C * W
```

The two constraints are independent, and both must hold:

- The area-specific daily maximum of 2 limits area visits on any single working day, so at most two area visits can be placed per day across the period: `A <= 2 * W`.
- The combined daily cap of `C` limits all field visits together on any single working day, so all required visits cannot exceed `C * W`: `A + D + F <= C * W`.

Category overlap. Area, DTC and feeder visits may share the same working date, subject to the combined cap and the area-specific maximum. Separately computed category day counts must therefore not be added unless a valid non-overlap proof exists for that configuration. The combined inequality above already accounts for sharing and must not be replaced by a sum of per-category day counts.

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
| Mixed-category packing | 2 areas + 1 DTC on one day | Allowed (total 3 <= combined cap 3; areas 2 <= 2) |
| Mixed-category packing | 2 areas + 1 feeder on one day | Allowed (total 3 <= combined cap 3; areas 2 <= 2) |
| Combined cap exceeded | 2 areas + 1 DTC + 1 feeder on one day | Rejected (total 4 > combined cap 3) |
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
- **INFEASIBLE** requires a proof. Only the following establish it: a configured minimum above the effective per-asset maximum; an obligation above the effective per-asset maximum; a failed aggregate necessary condition (`A > 2 * W` or `A + D + F > C * W`); an asset for which no gap-respecting placement exists at all; or a completed exhaustive search that found no joint assignment.
- **INDETERMINATE** is reported when the search budget is exhausted before any of the above is settled. It is neither an acceptance nor a rejection, and it must be surfaced to the user as such.

The joint packing problem (placing per-asset visit counts onto working dates under both daily caps) is not solvable by greedy placement in general, because a first-fit assignment can fail on an instance that is in fact feasible. Therefore a failed construction is never sufficient evidence of infeasibility, and the validator must not use one as such.

Cost note: the exact search is tractable for small rosters and becomes expensive as the number of assets with multi-visit obligations grows. A large roster with obligations at the aggregate boundary is expected to return INDETERMINATE within a default budget. Recording a target as feasible at scale therefore requires either a tighter obligation set or a stronger proof method, and neither is assumed here.

### 10.8 Assessment period, obligation inputs and committed visits

This subsection records the input contract the validator enforces. Each clause exists because the opposite behaviour would produce a verdict that is wrong in a way a user cannot see.

**Assessment period.** Assessment is defined for one calendar month. Per-asset minimums and maximums are calendar-month limits, and a coverage-cycle obligation is resolved by the caller into the portion owed in the month under assessment. A period spanning two calendar months is rejected rather than silently judged as one month, because that would apply a single month's per-asset maximum to both months and conceal genuine per-asset overrun. A partial month within one calendar month is valid and uses its actual eligible dates. A multi-month cycle is assessed month by month: the recorded 113-DTC three-month example is 38, 38 and 37, and each portion is assessed against its own month's capacity and limits.

**Malformed obligation inputs are rejected, never dropped.** The following are input errors, not verdicts: a duplicate asset id in the roster; an obligation, cycle obligation or limit override naming an unknown asset; a negative or non-integer count. A positive obligation or a configured positive minimum attached to an inactive asset is likewise an input error, because such a requirement cannot be discharged and omitting it would report a month as feasible while owed work was never scheduled. A malformed input is reported distinctly from a mathematically infeasible but well-formed obligation set; it is never reported as feasible and never as infeasible.

**One obligation specification.** Proposed-schedule validation in its obligation-aware mode and target-obligation feasibility assessment consume a single resolved obligation specification. The resolved obligation for an asset is the largest of its configured monthly minimum, its explicit required count and its resolved cycle portion. A monthly maximum never contributes to it, because a maximum bounds and does not demand. Sharing one specification is what prevents the two paths from disagreeing about what is owed.

**Committed visits are distinct from prior history.** Visits already committed inside the month, such as locked dates during regeneration, are supplied separately from visits made before the month. Committed visits occupy daily capacity including the area-specific capacity, count toward the per-asset monthly maximum, discharge the corresponding part of the declared obligation, and constrain new placements through the seven-calendar-day interval. They are returned alongside newly allocated visits, so a feasible witness describes the whole month rather than only what was added. A committed visit outside the month or off an eligible working date is reported rather than discarded, because a schedule resting on an invalid commitment cannot be correct.

**A committed visit is validated as strictly as a proposed one, on every path.** Committed in-period visits are checked for unknown assets, inactive assets, dates outside the period, ineligible working dates, both daily capacities, the per-asset monthly maximum and the seven-calendar-day interval against prior history and against each other. Both the concrete-schedule validator and the feasibility assessment evaluate these through one shared hard-constraint path, so the two cannot reach different conclusions about the same input.

**An invalid commitment makes the month infeasible, and no path may bypass that.** Because committed visits are immutable input, committed visits that already break a hard constraint mean no valid complete schedule exists around them. The assessment therefore returns INFEASIBLE with structured evidence naming the constraint, asset, offending dates, limit and observed value. This check runs before every path that can return FEASIBLE, including the path where the declared obligations are already discharged by the commitments and nothing remains to allocate; that path was previously able to return FEASIBLE on a month the concrete validator rejected. An invalid commitment is reported as an infeasibility, which is distinct from a malformed obligation specification, that being a caller error reported as an input error.

**A FEASIBLE verdict is a complete valid month.** Every FEASIBLE result, including one whose witness is empty or consists only of committed visits, carries a witness that passes independent hard-constraint validation and independent obligation-aware validation. A caller may rely on that without re-deriving it.

**Obligation counts are integers, and a boolean is not one.** A visit count must be a non-negative integer. Boolean values are refused even though the language treats a boolean as an integer subtype, because a boolean is a mistaken input and accepting it as a count of one would schedule work nobody asked for.

**Structured diagnostics.** A violation carries the constraint identifier, the affected asset and type, the dates involved, the limit and the observed value as structured fields. A daily-capacity violation additionally names every asset sharing the affected date with its type. A monthly-maximum violation lists every date counted, not merely the most recent. Message text is a readable rendering and is never the source of truth, so no caller needs to parse it.

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

**Only DTC cycle members affect cycle accounting.** A cycle's obligation applies to active DTCs only. Committed area and feeder visits are preserved, consume combined and area daily capacity and constrain the gap, but are never compared with the cycle's per-visit obligation and never produce DTC over-coverage evidence.

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

### 24.6 What is not modelled

- Partial-month generation, and therefore the use of the section 23 proration rule to
  build a schedule. A part-month is still refused.
- Reopening a finalised month, in place or by any other act.
- PDF and Excel output of a finalised version; backup and restore.
- An activity-selection scheduler. Entries on a date are rendered and corrected; which
  activity belongs on a date is still the operator's entry, not a computed choice.
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

### 25.3 What is still not guarded

- A row deleted and reinserted elsewhere inside one statement is beyond a trigger's reach;
  the guarantee is per row and per month.
- A database opened with `PRAGMA writable_schema` or with triggers dropped by hand is
  outside the guarantee, as with any SQLite trigger.
- Nothing here detects a tampered record: the fingerprint stored with it identifies the
  content that was issued and is compared by the application, but verifying a whole
  database against its own records is a separate act.
