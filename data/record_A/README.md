# Office data — record_A

This folder holds office-specific working data for one office.

## Retention

These files are tracked in Git by the data owner's decision. They were retained deliberately,
and a file's presence here is not a statement that its contents are approved for operational
use. Approval is recorded per file below, in the exact state it has reached.

The repository is public. A file committed here is visible to anyone, so publishing a file
and approving it for import are two separate decisions, and this folder records both
separately. Personal data about employees or consumers, credentials, access tokens, local
databases and backups remain unsuitable for this repository whatever approval they carry.

## Validation record — 2026-11-17

The original seven office files were read in full and checked against the current importer
contract, seeded identities, templates and the evidence recorded in Acts 15–17. Their executed
validation is the Act 21 rehearsal below. The approved activity catalogue and source crosswalk
were added in this owner-approval follow-up; their separate static contract and traceability
checks are recorded in the approval-basis file and this guide.

The Act 21 checks were executed, not inferred: `temp/dailyworkreg/act21_data_rehearsal.py`
imports each importable original file into a **throwaway database** (a temporary file that is
deleted afterwards), accepts or refuses it through the real importer and records every
assertion. Its result for those original files is **65 of 65 checks hold**. The operator's own
database was never opened. The new activity catalogue has not yet been staged through the
runtime importer or accepted in any database.

### Files, kinds and results

| File | Kind | Rows | Import rehearsal | Approval state |
|---|---|---|---|---|
| `01_dtc_master_roster.csv` | importer dataset (`dtc`) | 113 | Staged with 0 errors and no reassignment notice (every confirmed assignment names the feeder the file supplies); accepted as 113 inserts, 0 updates, 0 excluded; re-upload inserted 0 and updated 113; rejection left the master untouched | **Data quality validated** and **matches the approved baseline**. Ready for operator staging. Not accepted in the production database |
| `02_karnataka_general_holidays_2025_2026.csv` | importer dataset (`general_holiday`) | 39 | Staged with 0 errors; accepted as 39 holidays (19 in 2025, 20 in 2026); replacing with the same file is idempotent; 26 January 2026 classified as a general holiday and not a working day | **Notification transcription verified**, **matches the approved baseline**, ready for operator acceptance. Signed originals and Gazette copies are not held |
| `03_activity_catalogue_review_worksheet_DO_NOT_IMPORT.csv` | worksheet, not an importer dataset | 81 | Offered to the activity importer it produces row errors on every row; it does not import | **Not approved.** Every scheduling field is blank and every row says `NOT APPROVED — worksheet only` |
| `03_activity_proposed_examples_DO_NOT_IMPORT.csv` | proposal material, not an importer dataset | 51 | Refused at staging (`source_file_unmapped`) | **Not approved.** Byte-identical to the template's proposal file |
| `03_approved_activity_catalogue.csv` | importer dataset (`activity`) | 60 | Static check against the current activity contract: required fields present, all numbers valid, 60 Kannada descriptions supplied; runtime staging/rehearsal not yet run | **Owner-approved** for application scheduling/diary-planning scope. Ready for operator staging; not staged, reviewed or accepted in the operational database |
| `03_activity_catalogue_source_mapping.csv` | provenance crosswalk, not an importer dataset | 60 mapping rows / 81 source duties | Every ID from `SO-001` through `SO-081` mapped exactly once | Traceability only; not importable |
| `04_area_master.csv` | importer dataset (`area`) | 16 | Staged with 0 errors; accepted as 0 inserts and 16 updates against the seeded identities | **Matches the approved baseline**, ready for staging. English names remain blank by decision |
| `05_feeder_master.csv` | importer dataset (`feeder`) | 4 | Staged with 0 errors; accepted as 0 inserts and 4 updates against the seeded identities | **Matches the approved baseline**, ready for staging |
| `06_dtc_classification_reference.csv` | reference only, not an importer dataset | 5 | Not offered to any importer; every row was compared with the importer's own class mapping | **Reference verified.** Not a dataset and not importable |

### Cross-file reconciliation

- **DTC to feeder.** All 113 DTCs resolve to one of the four canonical feeders; none is
  unresolved. Distribution: F-01 64, F-02 16, F-03 29, F-04 4.
- **Owner-confirmed overrides.** SCD-5, SCD-12, SCD-107, SCD-108, SCD-109 and SCD-110 resolve
  to F-01, F-03, F-03, F-03, F-01 and F-01 respectively, matching
  `data/templates/05_dtc_feeder_overrides_reference.csv` and the feeder this file supplies on
  every one of those rows. SCD-107 and SCD-108 were recorded as F-01 in an earlier reading;
  the owner settled on 2026-11-19 that the imported file is what the office goes by. Every
  row's stored source feeder text is still exactly what the file said; no override rewrote a
  source value.
- **Class mapping.** The roster supplies 68 DEPT, 42 MSB and 3 OWN; the canonical result is
  68 DEPT and 45 SELF_DTC. The reference file's five rows agree with the importer's own
  mapping, and the importer maps DEPT to DEPT, MSB and OWN to SELF_DTC, and excludes SCF and
  SCE.
- **Derived feeder capacity.** Active DEPT DTCs only, from the accepted data: F-01 5538 kVA,
  F-02 1513 kVA, F-03 2650 kVA, F-04 600 kVA; total 10301 kVA, equal to the sum of DEPT kVA in
  the roster. These are derived, not stored: the feeder master carries no static aggregate.
- **Holidays.** The 39 delivered rows equal the established notification transcription in
  `tests/test_holiday_baseline.py` row for row, by date and name, and the file is byte-identical
  to `data/templates/02_karnataka_general_holidays_2025_2026.csv`. No Restricted Holiday, no
  Kodagu-local holiday, and no date omitted by the notification for a weekly off was added.
- **Area and feeder files.** Byte-identical to their templates, and identical to the seeded
  identities and defaults.
- **Activity catalogue.** The new 60-row `03_approved_activity_catalogue.csv` is owner-approved
  for the application's scheduling/diary-planning scope. Its separate crosswalk maps all 81
  source duty IDs exactly once. Static file/contract checks passed; the new file has not yet
  been staged or accepted through the application. The original 81-row worksheet and 51-row
  proposal file remain unchanged and explicitly not approved; neither is an import file.

### Reviewed and deliberately preserved

- **DTC names are kept verbatim**, including two repeated display names that belong to
  different DTC IDs (`BARMAPPA NAGARA`, `KUMBARGUNDI`) and one name with a trailing space in
  the source cell (`MAHENDRA KUMAR `). The importer trims for display; the source record keeps
  the supplied text. No name was normalised or retyped.
- **No Feeder Code or MDM Code was invented.** The roster has no such columns.
- **The commissioning column is read as the office supplies it, and two of its values are
  not real dates.** `Date of commision` was added by the office on 2026-11-19. 110 of the
  113 rows carry a date that reads; SCD-111 and SCD-112 carry `31-09-2026`, and September
  2026 has 30 days, so the value cannot be a date. Those two rows are imported with **no
  commissioning date**, which the schedule reads as no restriction, and the report names the
  row and the reason (`September 2026 has 30 days, so there is no 31`). The file was not
  edited: whether the office means `30-09-2026` or `31-08-2026` is theirs to decide, and
  guessing here would move a visit. SCD-113 carries `31-10-2026`, which is valid.
- **No SCF or SCE row was reconstructed.** The delivered roster contains only the 113 SCD rows.
- **Original activity source material is preserved.** The 81-row worksheet and 51-row proposal
  file remain unchanged and not approved. Under the owner's explicit instruction, a separate
  60-row application catalogue was authored with IDs `ACT-001` through `ACT-060`; the
  crosswalk preserves every original `SO-001` through `SO-081` source-duty identity exactly
  once. The numeric scheduling values are owner-approved planning assumptions, not facts
  supplied by the BESCOM source workbook.
- **No English transliteration was invented** for the 16 areas.
- **No holiday date, name or notification detail was edited**, because the file already matches
  the verified transcription.
- **Encoding and formatting** are UTF-8 with a byte-order mark and CRLF line endings, as the
  office spreadsheets export them; the importer reads them as UTF-8 with the mark stripped.
  They were not rewritten, because rewriting the delivered bytes would break the comparison
  against the file the office holds.

### Approval states, stated exactly

The following states are distinct and are not used interchangeably: source data supplied;
data quality validated; matches the approved product baseline; ready for staging; staged
successfully; operator reviewed; accepted into the application database; owner-approved for
the intended application use.

| File | Supplied | Validated | Baseline | Ready to stage | Staged | Reviewed | Accepted | Approved |
|---|---|---|---|---|---|---|---|---|
| DTC roster | yes | yes | yes | yes | rehearsal only | no | no | no |
| Holidays | yes | yes | yes | yes | rehearsal only | no | no | no |
| Area master | yes | yes | yes | yes | rehearsal only | no | no | no |
| Feeder master | yes | yes | yes | yes | rehearsal only | no | no | no |
| Activity worksheet | yes | no | no | no | no | no | no | no |
| Activity proposals | yes | no | no | no | no | no | no | no |
| Approved activity catalogue | yes | yes (static contract) | n/a | yes | no | no | no | yes — owner-approved |
| Source mapping crosswalk | yes | yes | n/a | not importable | n/a | n/a | n/a | provenance only |
| Classification reference | yes | yes | yes | not importable | n/a | n/a | n/a | n/a |

"Rehearsal only" means the file was accepted in a throwaway database to prove it works. No
batch has been accepted in the operator's database, and doing so remains a separate,
explicitly authorised operation.

### What is still missing

- **Operational activity import and review.** The 60-row catalogue is now owner-approved and
  committed, with all 81 source duties traceable in the crosswalk. The numeric values are
  application-planning assumptions rather than source facts. Static contract checks have
  passed, but the file has not yet been staged, reviewed or accepted through the application.
  That runtime staging/review/acceptance remains the next validation and operational step.
  The source BESCOM workbook is still a supervisory template, not a MESCOM-issued duty order.
- **Monthly operational inputs** — leave records, special events, additional meetings, fixed
  DTC, area and feeder visit commitments, and date overrides. The templates for these exist in
  `data/templates/` and this folder contains none of them yet.
- **Publication authorisation** for the files that describe office infrastructure, where that
  has not already been given.
- **Signed originals or Gazette copies** of either holiday notification.

## Safe workflow

1. Read `data/templates/README.md` before preparing or importing files.
2. Preserve original source documents, notifications, approvals and extraction provenance.
3. Use the existing application lifecycle: **read → judge → stage → operator review →
   accept/reject**. Uploading is not acceptance. Do not edit SQLite directly.
4. Keep private source copies and confidential operational records in an
   organization-approved private location. Do not use broad staging commands without first
   reviewing `git status` and the full diff.
