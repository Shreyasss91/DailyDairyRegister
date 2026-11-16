# DailyDairyRegister data templates

This folder contains the source-preserving CSV templates and reference data for preparing one local office dataset.

## Quick start
1. Download and extract `DailyDairyRegister_CSV_Templates.zip` into this folder.
2. Read the extracted `README.md` before editing. It is the detailed data dictionary for each template, including approved baseline values, example-only warnings, importer compatibility and scheduling constraints.
3. Copy the required files into `data/record_A/`; edit copies there and leave these templates unchanged.
4. Use UTF-8 and ISO dates (`YYYY-MM-DD`). Preserve blanks and `#N/A`; do not invent values.
5. Files ending `_DO_NOT_IMPORT.csv` are fictitious/proposed examples and must never be staged as operational inputs.

## What's included
- DTC master roster blank import template.
- Karnataka General Holiday list for 2025 and 2026 with source metadata; third-party copies of the notifications are used and must be checked against signed official/office copies before acceptance.
- Blank approved O&M activity import template plus proposed activities clearly marked NOT APPROVED.
- Seeded area and feeder reference lists; DTC source-class mapping and six approved feeder overrides.
- DTC weighting template and worked examples.
- Monthly leave, event, additional meeting, fixed DTC visit, fixed area visit, fixed feeder visit and date-override capture templates, each with separate fictitious examples where useful.

## Current importer boundary
The application's registered tabular datasets are `dtc`, `general_holiday`, `activity`, `area` and `feeder`. The five matching CSVs are import-shaped. Classification, weighting, override reference and monthly capture files are guidance/capture material, not separate import datasets. Do not upload them under another dataset key.

## Local-data protection
`data/record_A/` is reserved for filled office records and is excluded from Git. Do not commit real employee, DTC roster, leave, event, visit commitment, local database or backup data.
