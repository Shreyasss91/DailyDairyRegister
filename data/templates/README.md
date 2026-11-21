# DailyDairyRegister — data templates and office-data guide

**Folder:** `data/templates/`  
**Local working copy:** `data/record_A/`  
**Purpose:** prepare one office's master data and month-specific operational inputs without changing the pristine templates.

> **Important status labels**
> - **APPROVED BASELINE / SEEDED** — transcribed from the current owner-approved product baseline and repository seed values. These are reference identities/defaults, not proof that every office spelling or operational configuration is still current.
> - **NOTIFICATION TRANSCRIPTION — VERIFY** — transcribed from a Karnataka Government notification reproduced by a third-party host. Check against the signed Government/Gazette/office copy before staging/acceptance.
> - **PROPOSED — NOT APPROVED** or **EXAMPLE ONLY** — fictitious drafting examples. Never import these as operational data.
> - A blank cell means **not supplied / unknown**, not zero, not “none”, and not permission to infer a value.

## 1. Safe workflow

1. Clone or pull the repository. The templates are individual files directly in this folder; there is no extraction step.
2. Read this file before editing.
3. Copy only the required CSVs into `data/record_A/`. Edit the copies, not the originals in `data/templates/`.
4. Keep UTF-8 encoding (Kannada fields require it) and ISO dates `YYYY-MM-DD`.
5. Preserve original source spelling, blank values and literal markers such as `#N/A`. Do not replace blanks with zero or invented values.
6. Keep source files, notification copies, approvals, source sheet names, extraction dates and issuing-office details available for provenance.
7. The application import lifecycle is **read → judge → stage → operator review → accept/reject**. Uploading is not acceptance. Do not edit SQLite directly.
8. This repository is public and `data/record_A/` is **not** Git-ignored. Files there can be tracked when staged. Do not publish filled office data, employee/consumer personal data, actual DTC rosters, detailed feeder/station/asset mappings, leave/events/commitments, confidential source material, local databases or backups unless the relevant data owner and organization have explicitly authorized public disclosure. Import approval is not publication approval. Stage exact reviewed paths only; avoid unreviewed `git add .` or `git add data/record_A/`.

## 2. What the current application can import

The current tabular importer registers exactly these five dataset keys:

| Dataset key | File in this folder | Status |
|---|---|---|
| `dtc` | `01_dtc_master_roster.csv` | Blank import template |
| `general_holiday` | `02_karnataka_general_holidays_2025_2026.csv` | Notification transcription, cross-checked; accept against your own copy |
| `activity` | `03_activity_catalogue_blank_import.csv` | Blank import template |
| `area` | `04_area_master.csv` | Seeded reference rows in import shape |
| `feeder` | `05_feeder_master.csv` | Seeded reference rows in import shape |

These headers are aligned with the repository's current `dailyworkreg/infrastructure/imports/datasets.py` aliases. Keep the headers unchanged. Importability is not approval: review the staging verdict, warnings, raw-source retention, and proposed canonical changes before acceptance.

The remaining files are reference/calculation/capture material. They are **not** separate importer datasets. Do not try to upload them under another dataset key.

## 3. DTC master roster

### File: `01_dtc_master_roster.csv`

Blank header:
`DTC ID,DTC Name,Class,Feeder,Feeder Code,MDM Code,kVA,Station,Date of commision`

- Keep every source row from the original source extract, including SCF/SCE rows. Let the import judge explicitly exclude them; do not pre-filter them.
- The expected reconciliation is **113 SCD**: 68 DEPT and 45 SELF_DTC (42 MSB + 3 OWN). Expected excluded source records: five SCF and one SCE. These are checks against the source, not target numbers to force by editing.
- `DTC ID` is the office's own code: a short letter prefix and a number, such as `SCD-12` or `SCE142`. Any prefix is accepted and the tool reads no meaning into it - which records §8 excludes is decided by the `Class` column, so a roster numbered `SCE142` throughout imports in full. A cell that is not a code (a placeholder, a bare number, a name in the wrong column) is refused with that row named.
- Accepted source classes are DEPT, MSB and OWN. DEPT maps to canonical class DEPT; MSB/OWN map to SELF_DTC while retaining source subgroup. SCF/SCE are excluded. Unknown classes are errors, not guesses.
- Preserve source DTC name, feeder text, feeder code, MDM code, kVA, station and original source class. The `Feeder` column is source feeder text, not a canonical feeder ID.
- Blank or `#N/A` kVA must not be treated as zero. It leaves the weight incomplete and should be reviewed.
- `Date of commision` is the date the transformer was commissioned, as the office's own sheet spells it. A DTC is never scheduled for that day or any day before it. A blank cell, a `#N/A` placeholder or an unreadable value means no restriction - it is not read as "not yet commissioned" - and the supplied text is kept beside the accepted date so an unreadable value stays inspectable.
- Capture source filename, issuing office, worksheet name, extraction date and source/as-of date in the import batch notes or separate provenance record.

Owner-confirmed canonical overrides applied by the current application on DTC batch acceptance:

| DTC ID | Canonical feeder | Canonical feeder ID |
|---|---|---|
| SCD-5 | MRS F-1 OLDTOWN | F-01 |
| SCD-12 | Mandli F-22 GANDHIBAZAAR | F-03 |
| SCD-107 | Mandli F-22 GANDHIBAZAAR | F-03 |
| SCD-108 | Mandli F-22 GANDHIBAZAAR | F-03 |
| SCD-109 | MRS F-1 OLDTOWN | F-01 |
| SCD-110 | MRS F-1 OLDTOWN | F-01 |

Keep the supplied feeder string unchanged. The override sets the canonical feeder separately. Every assignment above now names the feeder the roster itself supplies, because the owner settled on 2026-11-19 that the imported file is what the office goes by: an agreeing file reassigns nothing, and a contradicting one is announced during review before the batch is accepted.

## 4. Karnataka General Holidays 2025–2026

### File: `02_karnataka_general_holidays_2025_2026.csv`

- Contains 19 dates for 2025 and 20 dates for 2026, transcribed from the Karnataka State General Holiday notification/circular references recorded in the source columns and below.
- **This transcription has been cross-checked and is still not accepted on your behalf.** Every date, name and printed weekday was read against the Government of Karnataka General Holiday tables, and each of the two notifications was compared across two independent published transcriptions that agree on all 39 rows. The copies linked below are still hosted by third parties rather than an official Karnataka domain, so check the signed notification, Gazette or your office copy, and check for later corrigenda/orders, before you accept the batch. Both notifications carry a note listing the festivals they omit because the date falls on a Sunday or the second/fourth Saturday; those dates are deliberately absent here and must stay absent, because a weekly off is a calendar rule and not a holiday row.
- 2025: Government of Karnataka No.DPAR 16 HHL 2024 dated 21-11-2024; source copy: [KEA-hosted 2025 circular PDF](https://kea.co.in/wp-content/uploads/2024/12/Circular-No-68-2024-KARNATAKA-GOVERNMENT-HOLIDAYS-FOR-THE-YEAR-2025.pdf). The CSV transcribes the General Holiday table; the copy read for this check was headed Notification-I, and the file does not rest on that label.
- 2026: Government of Karnataka No.DPAR 16 HHL 2025 dated 17-11-2025, Notification-I; source copy: [2026 notification PDF mirror](https://comply4hr.com/noti/2016/KASAR2025Nov17.pdf).
- Dates are ISO (`YYYY-MM-DD`). The importer also accepts `DD-MM-YYYY`, so an office copy in that layout imports unchanged, but keep one layout in a file: a mixed file invites a day/month misreading by whoever edits it next.
- The importer reads `Date`, `Name`, and optional `Description`. The extra calendar-year/notification/source-document columns remain as raw provenance in the staged source row.
- Description is blank because no owner-approved register wording was supplied. Do not invent Kannada translations or daily wording here.
- Only General Holidays are included. Restricted Holidays are ordinary working days in this product and must be ignored entirely: do not import, display, classify or add their names/dates.
- Do not add Kodagu district local holidays for the Shivamogga section. Do not add bank-only holidays as General Holidays.
- Weekly offs are a separate calendar rule (initially every Sunday and second/fourth Saturday). Do not duplicate a weekly off as a General Holiday merely because it is a non-working day.
- The application defaults to replacing the selected year on a General Holiday import; Merge is a separate explicit mode. Understand that year-level behaviour before accepting.

## 5. Approved O&M activity catalogue

### Files
- `03_activity_catalogue_blank_import.csv` — the only file intended for the activity importer.
- `03_activity_proposed_examples_DO_NOT_IMPORT.csv` — 51 English drafting suggestions; **not approved**, not operational data, and with no approved Kannada translations.

Blank importer headings:
`Activity ID,Category,Description,Kannada,Weight,Monthly Min,Monthly Max,Repeat Gap,Weekdays`

Target catalogue size is approximately 40–60 distinct approved MESCOM/BESCOM section-level activities across technical O&M, preventive maintenance, consumer service, revenue/energy accounting, safety, inspection/administration and works.

For every approved activity, supply:
- a stable unique `Activity ID`;
- category;
- approved English description;
- approved Kannada description;
- weight;
- monthly minimum and maximum frequency;
- minimum repeat gap in days;
- optional weekday restriction.

### What the importer enforces now

A row is refused, and named, when its `Activity ID`, `Category`, `Description`, `Weight`,
`Monthly Min`, `Monthly Max` or `Repeat Gap` is blank, is a placeholder such as `#N/A`,
or holds text that is not a number. A weight must be greater than zero; a minimum or
maximum must not be negative and the minimum must not exceed the maximum; a repeat gap
must not be negative. **Nothing is filled in for you.** Until recently a blank weight
became 1, a blank minimum 0, a blank maximum 10 and a blank gap 2, and a row with no
identifier was given `ACT-###`, which made an incomplete file look complete; that is gone.
A missing Kannada description is a warning rather than a refusal — the row imports with
its English text and no translation is invented — but the printed register then shows an
English-only activity, which the office will need to correct.

A file whose headings do not match this contract is refused when it is uploaded, with the
missing column named, so a wrong-column file cannot become an empty batch that explains
nothing.

### A worksheet, not a catalogue

If a duty list exists on paper or in a supervisory register, it can be turned into a
review worksheet: identity, work area, wording and the source's own frequency label kept
verbatim, and every catalogue number left blank. Such a worksheet is refused by the
importer for exactly the reasons above, which is what keeps a proposal from becoming a
catalogue by being uploaded. Keep it out of Git and mark it not approved.

The proposed examples are a starting point for review only. Rewrite/approve them through the appropriate office authority, translate/review Kannada descriptions, and copy only approved final rows to the blank import template. Their example weights/frequencies are not recommendations or approved defaults. The Monday safety meeting is a separate recurring rule and must not be duplicated as an ordinary activity unless separately directed.

## 6. Area master

### File: `04_area_master.csv`

**APPROVED BASELINE / SEEDED** identities from current `seed.py` and product baseline §7. Sixteen canonical areas and known aliases are prefilled; English names remain blank because no approved transliterations were available.

| ID | Canonical Kannada name | Known alias |
|---|---|---|
| A-01 | ಗಾಂಧಿಬಜಾರ್ | ಗಾಂಧಿ ಬಜಾರ್ |
| A-02 | ಲಷ್ಕರ್ ಮೊಹಲ್ಲಾ | — |
| A-03 | ಸಾವರ್ಕರ್ ನಗರ | — |
| A-04 | ಬಿ.ಎಚ್. ರಸ್ತೆ | — |
| A-05 | ತಿರುಪಳಯ್ಯನ ಕೇರಿ | ತಿರುಪಲ್ಯ ಕೇರಿ |
| A-06 | ಸೊಪ್ಪಿನ ಮಾರ್ಕೆಟ್ | — |
| A-07 | ಕೆ. ಆರ್. ಪುರಂ | KR ಪುರಂ |
| A-08 | ಭರಮಪ್ಪ ನಗರ | ಭರ್ಮಪ್ಪ ನಗರ |
| A-09 | ಎಂ.ಕೆ.ಕೆ. ಕೆ ರಸ್ತೆ | MKK ರಸ್ತೆ |
| A-10 | ನಾಗಪ್ಪ ಕೇರಿ | — |
| A-11 | OT ರಸ್ತೆ | — |
| A-12 | ಎಲ್ಲೆರೆವನ ಕೇರಿ | — |
| A-13 | ಕುಂಬಾರ ಗುಂಡಿ | — |
| A-14 | ಸಿದ್ದಯ್ಯ ರಸ್ತೆ ತಿಮ್ಮಪ್ಪನಕೊಪ್ಪಲು | — |
| A-15 | ಉಪ್ಪಾರ ಕೇರಿ | — |
| A-16 | ಕಸ್ತೂರ್ಬಾ ರಸ್ತೆ | — |

Headers: `Area ID`, `Kannada Name` (required), `English Name`, `Aliases` (semicolon-separated), `Weight`, `Monthly Min`, `Monthly Max`, `Revisit Gap`.

Prefilled values (weight 1.0, minimum 0, maximum 3, revisit gap 7 calendar days) are the repository's current seeded settings. Maximum 3 is a cap, not an obligation. No policy requires all 16 areas to be visited monthly. DTC-to-area mapping remains deferred; do not invent one.

## 7. Feeder master and DTC override reference

### Files
- `05_feeder_master.csv` — current four seeded feeder identities in importer shape.
- `05_dtc_feeder_overrides_reference.csv` — six owner-confirmed mappings; reference only, not an importer dataset.

| Feeder ID | Seeded canonical feeder |
|---|---|
| F-01 | MRS F-1 OLDTOWN |
| F-02 | Mandli F-13 MANDLI BYEPASS |
| F-03 | Mandli F-22 GANDHIBAZAAR |
| F-04 | Mandli F-3 NT ROAD |

Seeded settings: manual multiplier 1.0, monthly minimum 0, maximum 3, revisit gap 7 calendar days. The maximum is a cap, not an obligation; full-roster monthly feeder coverage is not approved.

Feeder weight is derived from the sum of installed kVA of active DEPT DTCs assigned to that feeder, multiplied by the manual adjustment multiplier. SELF_DTC is excluded. Do not enter a fabricated aggregate feeder weight. The application applies the six DTC overrides during DTC acceptance; do not upload the override reference as a feeder import.

## 8. DTC class mapping and weighting

### Files
- `06_dtc_classification_reference.csv` — reference policy already implemented by the DTC judge.
- `07_dtc_weighting_template.csv` — a working calculation/reference sheet for up to 113 DTCs, **not an importer dataset**.
- `07_dtc_weighting_worked_examples_DO_NOT_IMPORT.csv` — three fictional formula demonstrations.

| Source class | Canonical class | Retained subgroup | Active? | Class multiplier |
|---|---|---|---|---:|
| DEPT | DEPT | DEPT | Yes | 1.00 |
| MSB | SELF_DTC | MSB | Yes | 0.20 |
| OWN | SELF_DTC | OWN | Yes | 0.20 |
| SCF | — | — | No | — |
| SCE | — | — | No | — |

Formula: **DTC weight = kVA × class multiplier × individual adjustment**.

The individual adjustment is a multiplier, not a replacement weight. Use 1.00 only as the neutral/default multiplier where no separately approved adjustment exists. Any non-default adjustment must carry its reason, approval reference and effective date. Do not copy fictitious IDs from the worked examples into the real roster.

## 9. Monthly operational input capture

The files below are useful source/capture sheets, but the current tabular importer does **not** register datasets for leave, special events, meetings, fixed visits or date overrides. Keep these as working evidence; enter the actual records through the supported application UI when available. A future import requires a separately authorised implementation.

| File | One row represents | Required evidence / notes |
|---|---|---|
| `08_monthly_leave_records.csv` | One employee leave interval | Employee ID/name; CL or FPL; inclusive dates; full-day flag; approval status; approver and written approval reference |
| `09_monthly_special_events.csv` | One dated event/inspection/programme | Date/type/title; details; venue; organising authority; source/approval reference; status |
| `10_monthly_additional_meetings.csv` | One additional meeting | Date/type/title; venue; convenor; participants/sections; notice/reference; status |
| `11_monthly_fixed_dtc_visits.csv` | One confirmed DTC visit commitment | Date; canonical DTC ID; reason/scope; requester; written authority/source reference; commitment status |
| `12_monthly_fixed_area_visits.csv` | One confirmed area visit commitment | Date; canonical Area ID; reason/scope; requester; authority/reference; commitment status |
| `13_monthly_fixed_feeder_visits.csv` | One confirmed feeder visit commitment | Date; canonical Feeder ID; reason/scope; requester; authority/reference; commitment status |
| `14_monthly_date_overrides.csv` | One explicit date override | Date; Y/N effective working-day decision; reasons in both languages; written approval; approving officer; underlying calendar fact |

The matching files ending `_example_DO_NOT_IMPORT.csv` contain fictional rows solely to illustrate shape. Do not copy those rows into real data. The initial leave model has no half-day leave. Do not mark leave or a fixed visit approved/committed without the relevant authority. Multiple events/meetings can occur on one date. Do not duplicate the automatic every-Monday safety meeting as an additional meeting unless it is a distinct meeting.

## 10. Shared scheduling rules to remember

- Initial weekly offs: every Sunday and second/fourth Saturday (configurable).
- Only Karnataka General Holidays are operational holidays. Restricted Holidays are ignored entirely.
- Monday safety meeting: every Monday, including a Monday on a holiday or leave date; individual-date exception is supported.
- Regular activities: default 3–6 on eligible working days. Events, meetings, safety meeting and field visits are additional; they do not consume the ordinary activity quota.
- Area, DTC and feeder visits share a combined maximum of 3 per working day; area-specific maximum is 2 per working day.
- Minimum revisit interval for each area/DTC/feeder is 7 **calendar** days.
- Initial per-asset monthly maximum is 3; it is a cap, not an obligation.
- DTC primary coverage is an owner-declared cycle. Example: each active DTC once in a three-month cycle gives 113 total visits distributed 38/38/37. Do not assume all DTCs must be visited every month.
- Without a declared cycle, DTC target is a capacity-based weighted suggestion, not a coverage guarantee.
- No approved requirement says all 16 areas or all 4 feeders must be visited every month.
- Partial-month generation remains refused; proration arithmetic alone does not mean partial-month scheduling is implemented.

## 11. Privacy, provenance and acceptance

Keep the original source documents and approvals. The import staging tables preserve raw source rows; do not “clean” the only copy of a source workbook. Review every warning/exclusion and compare totals with the source before accepting. Never fabricate DTC names, kVA, feeder, activity approval, Kannada translation, employee leave or dated commitment. Filled office data belongs in `data/record_A/` and in the application's local database. Committing it is a decision for the data owner: this repository is public, so a recorded file is visible to anyone, and approving a file for import is not approving it for publication. Where the owner has authorized retention, say so in `data/record_A/README.md` next to the file's approval state rather than here.
