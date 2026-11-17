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

Each file was read in full, checked against the current importer contract, the seeded
identities in `dailyworkreg/infrastructure/sqlite/seed.py`, the templates in
`data/templates/`, `documentation/PRODUCT_REQUIREMENTS_BASELINE.md` §7–§8, and the evidence
recorded in Acts 15–17.

The checks were executed, not inferred: `temp/dailyworkreg/act21_data_rehearsal.py` imports
each importable file into a **throwaway database** (a temporary file that is deleted
afterwards), accepts or refuses it through the real importer and records every assertion.
Its result for this revision is **65 of 65 checks hold**. The operator's own database was
never opened.

### Files, kinds and results

| File | Kind | Rows | Import rehearsal | Approval state |
|---|---|---|---|---|
| `01_dtc_master_roster.csv` | importer dataset (`dtc`) | 113 | Staged with 0 errors and 2 reassignment notices (SCD-107, SCD-108); accepted as 113 inserts, 0 updates, 0 excluded; re-upload inserted 0 and updated 113; rejection left the master untouched | **Data quality validated** and **matches the approved baseline**. Ready for operator staging. Not accepted in the production database |
| `02_karnataka_general_holidays_2025_2026.csv` | importer dataset (`general_holiday`) | 39 | Staged with 0 errors; accepted as 39 holidays (19 in 2025, 20 in 2026); replacing with the same file is idempotent; 26 January 2026 classified as a general holiday and not a working day | **Notification transcription verified**, **matches the approved baseline**, ready for operator acceptance. Signed originals and Gazette copies are not held |
| `03_activity_catalogue_review_worksheet_DO_NOT_IMPORT.csv` | worksheet, not an importer dataset | 81 | Offered to the activity importer it produces row errors on every row; it does not import | **Not approved.** Every scheduling field is blank and every row says `NOT APPROVED — worksheet only` |
| `03_activity_proposed_examples_DO_NOT_IMPORT.csv` | proposal material, not an importer dataset | 51 | Refused at staging (`source_file_unmapped`) | **Not approved.** Byte-identical to the template's proposal file |
| `04_area_master.csv` | importer dataset (`area`) | 16 | Staged with 0 errors; accepted as 0 inserts and 16 updates against the seeded identities | **Matches the approved baseline**, ready for staging. English names remain blank by decision |
| `05_feeder_master.csv` | importer dataset (`feeder`) | 4 | Staged with 0 errors; accepted as 0 inserts and 4 updates against the seeded identities | **Matches the approved baseline**, ready for staging |
| `06_dtc_classification_reference.csv` | reference only, not an importer dataset | 5 | Not offered to any importer; every row was compared with the importer's own class mapping | **Reference verified.** Not a dataset and not importable |

### Cross-file reconciliation

- **DTC to feeder.** All 113 DTCs resolve to one of the four canonical feeders; none is
  unresolved. Distribution: F-01 66, F-02 16, F-03 27, F-04 4.
- **Owner-confirmed overrides.** SCD-5, SCD-12, SCD-107, SCD-108, SCD-109 and SCD-110 resolve
  to F-01, F-03, F-01, F-01, F-01 and F-01 respectively, matching
  `data/templates/05_dtc_feeder_overrides_reference.csv`. Every row's stored source feeder
  text is still exactly what the file said; no override rewrote a source value.
- **Class mapping.** The roster supplies 68 DEPT, 42 MSB and 3 OWN; the canonical result is
  68 DEPT and 45 SELF_DTC. The reference file's five rows agree with the importer's own
  mapping, and the importer maps DEPT to DEPT, MSB and OWN to SELF_DTC, and excludes SCF and
  SCE.
- **Derived feeder capacity.** Active DEPT DTCs only, from the accepted data: F-01 5738 kVA,
  F-02 1513 kVA, F-03 2450 kVA, F-04 600 kVA; total 10301 kVA, equal to the sum of DEPT kVA in
  the roster. These are derived, not stored: the feeder master carries no static aggregate.
- **Holidays.** The 39 delivered rows equal the established notification transcription in
  `tests/test_holiday_baseline.py` row for row, by date and name, and the file is byte-identical
  to `data/templates/02_karnataka_general_holidays_2025_2026.csv`. No Restricted Holiday, no
  Kodagu-local holiday, and no date omitted by the notification for a weekly off was added.
- **Area and feeder files.** Byte-identical to their templates, and identical to the seeded
  identities and defaults.
- **Activity files.** Neither file can enter the activity master, and the master stayed empty
  in the rehearsal.

### Reviewed and deliberately preserved

- **DTC names are kept verbatim**, including two repeated display names that belong to
  different DTC IDs (`BARMAPPA NAGARA`, `KUMBARGUNDI`) and one name with a trailing space in
  the source cell (`MAHENDRA KUMAR `). The importer trims for display; the source record keeps
  the supplied text. No name was normalised or retyped.
- **No Feeder Code or MDM Code was invented.** The roster has no such columns.
- **No SCF or SCE row was reconstructed.** The delivered roster contains only the 113 SCD rows.
- **No activity wording, identifier, weight, frequency, repeat gap or weekday restriction was
  invented**, and no BESCOM source-duty identifier was turned into an application activity ID.
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
successfully; operator reviewed; accepted into the application database; approved for
operational use.

| File | Supplied | Validated | Baseline | Ready to stage | Staged | Reviewed | Accepted | Approved |
|---|---|---|---|---|---|---|---|---|
| DTC roster | yes | yes | yes | yes | rehearsal only | no | no | no |
| Holidays | yes | yes | yes | yes | rehearsal only | no | no | no |
| Area master | yes | yes | yes | yes | rehearsal only | no | no | no |
| Feeder master | yes | yes | yes | yes | rehearsal only | no | no | no |
| Activity worksheet | yes | no | no | no | no | no | no | no |
| Activity proposals | yes | no | no | no | no | no | no | no |
| Classification reference | yes | yes | yes | not importable | n/a | n/a | n/a | n/a |

"Rehearsal only" means the file was accepted in a throwaway database to prove it works. No
batch has been accepted in the operator's database, and doing so remains a separate,
explicitly authorised operation.

### What is still missing

- **The approved activity catalogue.** The 40–60 activities with Kannada wording, weight,
  monthly minimum and maximum, repeat gap and any weekday restriction are not supplied. This
  folder holds a source-duty worksheet of blanks and a proposal list, neither of which is a
  catalogue. Whether the BESCOM supervisory duty register describes this office's work at all
  is an owner decision that has not been taken. **This is a data blocker, not an
  implementation blocker.**
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
