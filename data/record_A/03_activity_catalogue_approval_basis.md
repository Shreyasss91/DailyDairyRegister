# O&M Activity Catalogue — owner approval basis

**Approval date:** 2026-11-17  
**Approval authority recorded here:** Project/data owner instruction in the current work session  
**Approval scope:** Approved for use as the activity catalogue in the DailyDairyRegister scheduling application for the owner's MESCOM O&M section, subject to the scope and authority boundaries below.

## Source and transformation

The source is the 81-duty `Master Register` sheet in `BESCOM_OM_Section_Officer_Master_Duty_Register.xlsx`, together with the 81-row review worksheet and the 81-row suggested-values working file. The workbook itself describes itself as a practical supervisory aid, not an official duty order. It is a BESCOM source, not a MESCOM-issued duty order.

The approved import file contains 60 distinct, scheduling-oriented activity descriptions. All 81 source duty IDs (`SO-001` through `SO-081`) are represented exactly once in `03_activity_catalogue_source_mapping.csv`; related controls have been consolidated to avoid an unnecessarily large and repetitive catalogue. The source workbook and original review/proposal files remain unchanged.

## Scheduling parameter policy

- **Weight:** maps source priority to a relative scheduling preference: Critical 2.00, High 1.50, Medium 1.00, Low 0.75. Weight is not authority, severity certification or a visit obligation.
- **Monthly minimum:** positive only for recurring weekly/monthly controls where a modest monthly review is reasonable. Event-triggered and immediate-response duties have zero minimum so the scheduler does not fabricate an event or work order.
- **Monthly maximum:** a planning ceiling inferred from the source cadence, not a statutory target, actual case-volume estimate or instruction to perform that many tasks. Daily review controls use up to 22; weekly controls up to 5; monthly controls up to 2; event-driven controls use a bounded planning ceiling. These values should be revisited against actual workload after operating experience.
- **Repeat gap:** a scheduling separation parameter, not an official maintenance interval. It is longer for periodic reviews and zero for event-triggered duties where a fixed spacing would be inappropriate.
- **Weekdays:** left unrestricted because the source provides no defensible weekday restrictions.
- **Language:** Kannada descriptions are human-readable working descriptions drafted for this application. They are not represented as official MESCOM terminology approved by a language or departmental authority.

## Scope and authority boundaries

This owner approval is for scheduling and diary-planning use in the application. It is **not** a MESCOM departmental duty order, statutory compliance certification, safety procedure, switching instruction, Line Clear, permit-to-work, work completion certificate, financial sanction, revenue enforcement authorisation or delegation of powers.

Cross-functional activities are phrased as technical review, coordination, reporting or support. Revenue, metering, vigilance/enforcement, project, testing, MUSS and other competent units retain their respective authority. Service connection, disconnection/reconnection, expenditure, measurement, certification, switching and safety steps remain subject to current MESCOM orders, approved procedures and delegated powers.

The catalogue does not claim that the BESCOM source register is itself applicable as an official MESCOM order. The owner has explicitly authorised this adapted, section-level scheduling catalogue for the application. Any later departmental direction takes precedence and should trigger a controlled catalogue revision.

## Validation required before production import

The repository implementation must validate this file against the actual `activity` importer, stage it in a throwaway database, inspect every row, and confirm the expected 60 activity records and source mapping. Only after that validation should the operator stage and accept it in the operational application database.

## Static contract and traceability validation completed for the repository addition

The proposed import file was checked against the activity import contract in
`dailyworkreg/infrastructure/imports/datasets.py`:

- Exactly 60 records are present, with unique sequential IDs `ACT-001` through `ACT-060`.
- The header names map to the importer's `activity_id`, `category`, `description_en`,
  `description_kn`, `weight`, `monthly_min`, `monthly_max`, `repeat_gap_days` and
  `weekday_mask` fields.
- Every row supplies category, English description, Kannada description, weight, monthly
  minimum, monthly maximum and repeat gap.
- Every weight is finite and greater than zero. Every monthly minimum, maximum and repeat gap
  is a non-negative whole number, and no minimum exceeds its corresponding maximum.
- `Weekdays` is blank for every row, meaning no weekday restriction is asserted; the importer
  accepts this optional field as absent.
- The separate mapping has 60 rows and covers every source duty ID `SO-001` through `SO-081`
  exactly once, with no duplicate or missing source ID.

These are static file/contract checks, not a runtime importer rehearsal. This GitHub-only
repository addition did not stage or accept the catalogue in a database, did not open the
operator's database, and did not execute the application's test suite. The next operational
validation is to use the application's normal read → judge → stage → review workflow and
inspect the actual staged rows before any operator acceptance.

## Scheduling values are owner-approved assumptions, not source facts

The source duty register supplies descriptive duties, qualitative priorities and worded
cadences; it does not supply the numeric weights, monthly limits or repeat gaps in this file.
Those numeric values are proposed planning parameters authorised by the owner for this
application. They are deliberately conservative defaults and should be reviewed against the
section's actual workload after the first operating cycles. The source mapping is retained so
that each consolidated application activity can be traced back to the source duties it covers.