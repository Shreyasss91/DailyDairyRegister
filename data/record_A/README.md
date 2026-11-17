# Office data — record_A

This folder holds office-specific working data for one office.

## Public-repository warning

This repository is public, and `data/record_A/` is no longer Git-ignored. A file committed here is visible to anyone. Only stage selected files after reviewing their complete contents and confirming that public disclosure is authorized by the relevant data owner and organization.

Do not publish employee or consumer personal data, credentials, access tokens, confidential office records, detailed infrastructure information, databases, backups, or source material whose publication rights are uncertain. Application import readiness and permission to publish the source file are separate approvals.

## Review status — 2026-11-17

The files below were inspected against the current templates, importer contracts, seeded identities and the evidence recorded in Acts 15–17. This is a data-quality review, not acceptance into the operator's production database.

| File | Review result | Operational status |
|---|---|---|
| `01_dtc_master_roster.csv` | 113 rows; SCD-1 through SCD-113 contiguous and unique; 68 DEPT, 42 MSB and 3 OWN; no blank values in the six supplied columns; two repeated display-name values belong to distinct DTC IDs. The owner-confirmed SCD-107/SCD-108 feeder overrides remain visible as source/canonical differences. Feeder Code and MDM Code are not supplied. | The roster passed the Act 15 throwaway-database import rehearsal and is ready for operator review. Production acceptance was not performed. **This file contains name-like DTC labels together with transformer capacity, feeder and station information; this review does not approve it for public disclosure.** Obtain organizational publication authorization or keep it in an approved private location. |
| `02_karnataka_general_holidays_2025_2026.csv` | 39 valid ISO-date rows: 19 for 2025 and 20 for 2026; no duplicate dates or year mismatches. Its data rows match the verified template. | Matches the published notification tables as recorded in Act 16 and is ready for operator staging/review. Signed originals/Gazette copies were not held in the repository; office acceptance remains outstanding. |
| `03_activity_catalogue_review_worksheet_DO_NOT_IMPORT.csv` | 81 source-duty rows; all marked `NOT APPROVED — worksheet only`. Activity ID, Kannada, weight, monthly limits, repeat gap and weekday fields are blank. | Review worksheet only. It is not an approved MESCOM catalogue and must not be imported. Whether the BESCOM source register applies to this office is an owner decision. |
| `03_activity_proposed_examples_DO_NOT_IMPORT.csv` | 51 proposed examples; identical in content to the corresponding template file. | Proposal material only. Not approved and not importable. |
| `04_area_master.csv` | 16 canonical areas; identical to the corresponding template and the approved identities/defaults in `seed.py`. English names are blank. | Matches the seeded baseline. Do not invent English transliterations. The application seeds these identities on a fresh database. |
| `05_feeder_master.csv` | Four feeder identities; identical to the corresponding template and the approved identities/defaults in `seed.py`. | Matches the seeded baseline. The application seeds these identities on a fresh database. |
| `06_dtc_classification_reference.csv` | DEPT/MSB/OWN inclusion and SCF/SCE exclusion rows match the current baseline and importer mapping. | Reference only; not a separate importer dataset. |

### What was deliberately not changed

- Original DTC names and source feeder strings were not normalized or rewritten. Repeated display names do not establish duplicate assets when DTC IDs differ.
- No Feeder Code or MDM Code values were invented.
- No SCF/SCE rows were reconstructed; the delivered roster contains only the 113 SCD rows.
- The holiday dates/names were not edited because the office copy matches the verified template.
- No English/Kannada activity wording, activity IDs, weights, monthly frequencies, repeat gaps or weekday restrictions were invented.
- Area, feeder, classification and proposal files that exactly duplicate canonical templates/seed values were not changed.

### Missing monthly inputs

This folder currently contains no monthly leave records, special events, additional meetings, fixed DTC/area/feeder visit commitments or date overrides. It is not yet a complete month-specific operational input pack.

## Safe workflow

1. Read `data/templates/README.md` before preparing or importing files.
2. Preserve original source documents, notifications, approvals and extraction provenance.
3. Use the existing application lifecycle: **read → judge → stage → operator review → accept/reject**. Uploading is not acceptance. Do not edit SQLite directly.
4. Keep private source copies and confidential operational records in an organization-approved private location. Do not use broad staging commands without first reviewing `git status` and the full diff.
