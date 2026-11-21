# The DailyDairyRegister wiki

This is the operator's guide to the tool: how to install it, how to start it, what each screen
does, the order the work has to be done in, and what every message means. It is written for the
Assistant Engineer who runs the office diary, and for whoever maintains the computer it runs on.

Read this page in order the first time. After that, use the table of contents.

---

## Contents

1. [What this tool is, and what it is not](#1-what-this-tool-is-and-what-it-is-not)
2. [Getting it running](#2-getting-it-running)
3. [Where your data lives](#3-where-your-data-lives)
4. [The order the work is done in](#4-the-order-the-work-is-done-in)
5. [Screen by screen](#5-screen-by-screen)
6. [A full walkthrough: a new office to a first issued month](#6-a-full-walkthrough-a-new-office-to-a-first-issued-month)
7. [The rules the tool enforces](#7-the-rules-the-tool-enforces)
8. [Exports and printing](#8-exports-and-printing)
9. [Backup, restore and handing over](#9-backup-restore-and-handing-over)
10. [Messages you will see, and what to do](#10-messages-you-will-see-and-what-to-do)
11. [Troubleshooting](#11-troubleshooting)
12. [The files in `data/templates`, and which ones the tool reads](#12-the-files-in-datatemplates-and-which-ones-the-tool-reads)
13. [For whoever maintains the installation](#13-for-whoever-maintains-the-installation)

---

## 1. What this tool is, and what it is not

DailyDairyRegister prepares the office **Daily Diary Register** and the O&M work that fills it:
visits to distribution transformers (DTCs), areas and feeders, the regular activities the
approved catalogue names, the General Holiday calendar, leave, dated events, and the weekly
safety meeting. It plans a month, shows you the plan as a draft register, lets you review and
correct it, and issues it as a closed month that can be printed or exported.

Three things are worth saying plainly before anything else, because they shape every screen:

- **A generated register is a draft suggestion, not evidence that the work was done.** The tool
  writes what a plausible month looks like under your own constraints. Recording that the visits
  actually happened is done in the paper register and in the field, not here.
- **It is one office, on one computer, with one operator.** There is no login, no multi-user
  access and no network service. The application listens on `127.0.0.1` only and must never be
  exposed to the office LAN, a reverse proxy or a tunnel. Anybody who can reach the page can
  change the register.
- **A closed month is never reopened.** A mistake found after closing is corrected by issuing a
  new version of that month, and the old version stays on the record. That is a deliberate
  choice: a register that can be silently edited is not a register.

The application is local and offline by design. Nothing is sent anywhere. The only thing it
downloads, once, is the Chromium browser it uses to write PDFs on this computer.

---

## 2. Getting it running

### 2.1 What you need

- Windows 10 or Windows 11.
- **Python 3.11 or newer.** The repository declares this minimum and needs it.
- **Git for Windows**, to get the code and to update it later.
- PowerShell, which comes with Windows.
- About 1 GB of free disk for Python, the packages and the PDF browser. The register data itself
  is small (a few megabytes for years of registers).

Check what you have. In PowerShell:

```powershell
py -0p
py -3.11 --version
git --version
```

`py -0p` lists every Python installed with the launcher and where it lives. If `py -3.11` is not
found, install Python 3.11 or newer from python.org and tick **Add python.exe to PATH**.

### 2.2 First installation, step by step

Open PowerShell and run these five steps once. Replace the folder with wherever you keep your
project checkouts; the example uses `E:\Projects`.

```powershell
New-Item -ItemType Directory -Force E:\Projects | Out-Null
Set-Location E:\Projects
git clone https://github.com/Shreyasss91/DailyDairyRegister.git
Set-Location .\DailyDairyRegister
```

Then create the tool's own Python environment and install it. This is the part that fetches the
application's packages; it needs the internet and takes a few minutes.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

What this does, in words:

- `.venv` is a private Python for this application, inside the checkout, ignored by Git. It
  cannot break, or be broken by, anything else on the computer.
- `pip install -e .` installs the application itself in editable mode, which registers two
  commands in `.venv\Scripts\`: `dailyworkreg` (start the tool) and `dailyworkreg-print` (join
  already-exported month PDFs).
- The `[test]` extra adds `pytest` and `httpx`, which are only needed to run the regression suite.
  If you will never run the tests, `pip install -e .` alone is enough and the tool works the same.

### 2.3 The PDF engine, one time per computer

The tool writes PDF files itself, using a Chromium browser that stays on this computer. Install
it once:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

It is downloaded into your own user profile, not into the checkout, so updating the code never
removes it. Deleting your profile folder does, and then this command is run again.

**Skipping this step breaks nothing except PDF downloads.** Every other feature works. A PDF
request then refuses with a sentence naming this exact command instead of producing a file that
will not open, and the browser's own **Print / Save as PDF** button still works.

### 2.4 Starting and stopping the tool

From the repository folder:

```powershell
.\.venv\Scripts\dailyworkreg.exe
```

Then open the page it names: **http://127.0.0.1:8010**.

The window that stays open is the application. Leave it open while you work; closing that window
or pressing `Ctrl+C` in it stops the tool. Nothing is lost: everything is saved to the database
the moment you confirm it, so stopping is always safe.

There is no login. The page is the tool.

If the port is already in use, start it on another one:

```powershell
.\.venv\Scripts\dailyworkreg.exe --port 8020
```

To see which database file this installation would use, without starting it:

```powershell
.\.venv\Scripts\dailyworkreg.exe --print-db
```

### 2.5 A copy on another database file

Useful for a rehearsal on last month's copy, or for testing an update before trusting it:

```powershell
.\.venv\Scripts\dailyworkreg.exe --db "D:\rehearsal\dailyworkreg.db" --port 8020
```

`--db` wins over everything. The `DAILYWORKREG_DB` environment variable does the same thing when
set for the session:

```powershell
$env:DAILYWORKREG_DB = "D:\rehearsal\dailyworkreg.db"
.\.venv\Scripts\dailyworkreg.exe --port 8020
```

A rehearsal copy is a separate office as far as the tool is concerned. Opening it does not touch
the real register.

### 2.6 Updating the tool later

```powershell
Set-Location E:\Projects\DailyDairyRegister
.\.venv\Scripts\python.exe -m pip install --upgrade pip
git pull
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

Then stop the running application and start it again. On start it applies any schema migrations
that came with the update, which is why the restart matters. **Take a backup first** from the
Backup tab: a backup is a single file and it is the only thing that makes an update reversible.

`git pull` never touches the database, because the database is not in the checkout.

### 2.7 Moving to another computer

1. On the old computer, take a backup from the Backup tab and download it.
2. On the new computer, install as in 2.2 and 2.3.
3. Start the tool, open the **Backup** tab, choose that file under *Restore from a backup*, check
   it, and confirm.

The backup holds everything: masters, imports, every revision, every review, every closed month
and every issued version. Copying the `.db` file across by hand also works, but the homepage's
restore checks the file first, which is why it is the better route.

---

## 3. Where your data lives

| What | Where | Notes |
| --- | --- | --- |
| The database | `C:\Users\<you>\AppData\Local\DailyDairyRegister\dailyworkreg.db` | Outside the Git checkout on purpose, so `git clean` or a re-clone cannot delete it. `dailyworkreg --print-db` prints the exact path for your installation. |
| A backup you take | Your Downloads folder | It exists only until the application is closed, so download it in the same session. |
| The recovery copy left after a restore | Beside the live database, named with a timestamp | It is the database as it stood before the restore. Delete it once you are satisfied. |
| The recovery copy left after a reset | Beside the live database | The previous office's database. Download the offered file as well, and delete the copy when satisfied. |
| Exported registers | Your Downloads folder | Not touched by a reset: what was issued is issued. |
| Your source files | `data/templates/` for the blank templates, and wherever the office keeps its own | The tool only ever reads the file you point it at. |

Only the database matters. Everything else on that list can be regenerated by the tool, except
the exports you have already issued and delivered, which are yours to keep.

---

## 4. The order the work is done in

The tabs are arranged in the order the work happens, and the tool enforces part of that order
because some files cannot be read before others.

```
Overview    ->  what is missing, and how much is stored
Imports     ->  areas and feeders, then the DTC roster, then the catalogue, holidays, leave
Masters     ->  correct or add a single record by hand
Calendar    ->  weekly offs, General Holidays, leave, dated events, per-date overrides
Generate    ->  plan a period
Draft       ->  read the plan, review it, correct it, close months, export
History     ->  every attempt ever made, including the ones that failed and why
Backup      ->  take a copy, restore one, or start a new office
```

Two orderings are not negotiable:

- **Areas and feeders before the DTC roster.** A transformer sits on a feeder and is reached
  through an area, and the roster row names the feeder by the name the feeder master carries. A
  roster read before those two files is refused, and refused again if it names a feeder the
  feeder master does not have.
- **A month can only be closed after its dates are reviewed.** Review is the step that says a
  person has looked at the day; the tool will not issue a register nobody has read.

And one ordering that is practical rather than enforced: read the catalogue and the holiday list
before generating, because a month generated without them is a month whose activities and
holidays have to be corrected by hand afterwards.

---

## 5. Screen by screen

### 5.1 Overview

Two tables.

**Readiness** is a checklist for the period you last asked to generate. Each line says what it
needs, whether it is present, and how much is there: *"154 active DTC(s) are stored"*, *"no area
is stored"*, *"no activity is stored, so regular activity entries cannot be generated; the
approved catalogue has not been imported"*, and one line per year for the official Karnataka
General Holiday baseline. A line marked as blocking will stop a draft; the others are gaps that
narrow what can be planned.

**Stored master data** counts what the database holds, master by master. It is the quickest
answer to "did my import actually land?".

Press **Refresh readiness** after any import.

### 5.2 Imports

Every import is two steps, and the two steps exist so that a wrong file cannot change anything
until you have seen what it would do.

1. Choose the dataset, choose the file, press **Read and judge**.
2. Open the batch, read the per-row verdicts, then press **Accept into master data** — or
   **Reject**.

Nothing reaches the master data before acceptance. A batch with even one row that cannot be
imported cannot be accepted, because accepting the rest would silently drop that row; you either
correct the source file and read it again, or reject the batch. Warnings do not block acceptance:
they are things you should know, not things that break.

The file may be `.csv`, `.tsv`, `.xlsx` or `.xlsm`. Column headings are matched leniently — case,
spacing and punctuation do not matter, and common alternative names are accepted — so the
office's own sheet does not have to be rewritten to fit.

The six datasets, and what each one needs:

| Dataset | Use it for | Columns it cannot work without |
| --- | --- | --- |
| **Areas** | the area master | Kannada name (or *Name KN*, *Kannada*) |
| **Feeders** | the feeder master | Feeder name |
| **DTC roster** | the transformer roster | DTC id, DTC name, Class |
| **Activity catalogue** | the approved list of regular activities | Category, Description |
| **General Holidays** | the official Karnataka holiday list | Date, Holiday name |
| **Monthly leave records** | the office's monthly leave sheet | Leave type, From date |

Columns it reads when they are present are listed in [section 12](#12-the-files-in-datatemplates-and-which-ones-the-tool-reads).

**The rule that is enforced at read time.** The DTC roster is refused, with nothing created and
nothing changed, when:

- **no area is stored yet** — *"this roster names transformers, and no area is stored yet. Read
  the area master (04_area_master.csv) first, then read this roster again"*;
- **no feeder is stored yet** — the same sentence naming the feeder master;
- **it names a feeder the feeder master does not carry** — *"the feeder master does not carry 1
  name(s) this roster gives: 'CHOWTRY FEEDER 9'… Read the feeder master that belongs with this
  roster, or correct those rows in the roster, then read it again"*, listing up to five names and
  counting the rest.

The reason is not bureaucracy. A transformer resolves to a feeder only through that master, so a
roster read first is stored with every transformer unassigned, every feeder's capacity comes out
short, and the only cure is to read the roster again. Refusing at the door is one message instead
of an afternoon.

A **blank feeder cell** is not a refusal. It is a gap in the office's own sheet, so the row is
accepted with a warning that this transformer will not be reached through any feeder's capacity.
Fill it in the sheet and read it again when the answer is known.

Accepted batches are listed with their row, error and warning counts, and opened by **Open**.
Every source row is kept verbatim, including the rows that were left out, so a discrepancy you
were asked to review is still there to look at afterwards.

### 5.3 Masters

Bulk entry belongs in Imports. This tab enters or corrects **one record at a time**: choose DTC,
Area, Feeder or Activity, press **Show**, edit the row in the form and press **Save record**.

Two rules that hold everywhere on this tab:

- An asset that is **not active** stops being scheduled but is never deleted. That is how a
  removed transformer stays on the record.
- Nothing here reseeds or resets anything else. A save touches one record.

### 5.4 Calendar

This is where the office's time is defined, and it is worth getting right before generating,
because a month planned against a wrong calendar is wrong everywhere.

**Weekly offs.** Sunday, second Saturday and fourth Saturday, each a checkbox, saved together
with **Save rules**. Any combination is allowed. These are the baseline pattern; a single date
that differs is an override, not a rule change.

**The day table.** *From* and *To*, then **Show calendar** lists each date with its classification
(working day, weekly off, General Holiday, leave, override) and the reasons in words. Conflicts
appear in a warning banner above the table: a leave recorded on a holiday, an event on a weekly
off. **A conflict is shown and kept, never silently resolved** — the office decides, and the tool
leaves both facts where they are.

**General Holidays.** Date, name, optional printed wording, **Save holiday**. Each stored holiday
has a **Delete** button. Only Karnataka **General** Holidays belong here. Restricted Holidays are
ordinary working days in this tool and must not be entered, names and all. Overlapping reasons
are fine and expected: a General Holiday falling on a Sunday is both, and the register says so.

**Leave records.** One date and one leave type each, with a **Delete** per row. Leave arrives two
ways:

- typed here, one date at a time; or
- read in bulk from the office's monthly sheet (dataset *Monthly leave records*), where one sheet
  row covers a range and writes one record per date in it.

The leave type must already exist — `CL`, `FPL` and any type the office adds. An import never
invents one. A sheet row whose approval status reads as rejected, cancelled or withdrawn is left
out with its reason, because a leave the office did not grant is not a day it was closed. A
half-day entry is written as a full day with a warning, because this calendar has no half day: a
date is either worked or not.

**Dated events.** Date, title, kind (event, activity or safety meeting), **Save event**. Events
never consume the regular activity quota; they are additional. The Monday safety meeting is
entered automatically on every Monday — including a Monday that is a holiday or a leave date — so
do not add it again by hand unless it is genuinely a second, different meeting.

Per-date overrides are also part of this tab's model: a date's effective working-day decision can
be stated explicitly, and it never erases the holiday, weekly off or leave underneath it. Both
facts stay on the record.

### 5.5 Generate

This form plans a period and produces one draft register. Nothing here is final and nothing here
is published; pressing **Generate** creates a new revision of the draft.

| Field | What it means |
| --- | --- |
| **From / To** | The period to plan. A period spanning several months is planned month by month, and each month can be closed on its own later. |
| **Mode** | **Coverage cycle** — you declare that every active DTC is visited once across a cycle of N months. **Capacity fallback** — no cycle is declared, so the DTC target is a capacity-weighted suggestion rather than a coverage promise. |
| **Seed** | A number that makes the run repeatable. The same inputs and the same seed produce the same draft. A different seed is how you ask for a different draft of the same month. |
| **Cycle start month / Cycle months / Visits per asset** | The declared cycle: where it begins, how long it is, and how many times each asset is visited within it. Typical: start `2026-07`, 4 months, 1 visit per asset. |
| **Regular activities per working day — minimum / maximum** | The floor and the ceiling for ordinary activities on every working day. The day's own count is drawn between them. Set both to 0 to leave the days to field visits only. |
| **DTC / Area / Feeder visits per working day — maximum** | **One ceiling per kind**, independent of the others: a day of transformer checks does not spend the area allowance. A date may hold up to each of them. |
| **Entry language** | English, Kannada, or both, for the register's own sentences. Kannada falls back to English where no Kannada text is stored. |
| **DTC target (fallback)** | Only read in capacity-fallback mode. |
| **Generate** | Runs it. A progress bar reports which month it has reached, so a ten-month period does not look like a page that has stopped answering. |

A few consequences worth knowing before you press it:

- The activity minimum is a **hard floor**: every working day records at least that many approved
  activities. If the catalogue cannot supply them on a date, the tool says so
  (`activity_placement_cannot_fill_a_day`) rather than quietly recording fewer. The fix is a wider
  catalogue, a lower minimum, or a longer repeat gap.
- The per-kind maxima are hard ceilings, not targets. A plan that cannot honour both the cycle and
  the ceilings reports infeasibility and **names the constraint responsible**; it never relaxes a
  limit to look successful.
- A generator will not visit a DTC before the day it was commissioned.
- The register never plans a partial month: a period that starts or ends mid-month is refused,
  because the monthly minimums would be prorated against days that are not there.

### 5.6 Draft

Everything that happens after a plan exists happens here, in this order: read it, review it,
correct it, close the month, export it.

**Reading it.** The head states the period, mode, revision, seed and whether the draft is
allocation-complete. Below it: the exceptions the allocator recorded, the month evidence, then the
register itself, one row per date, with the day's classification, its visits, its entries, its
warnings, its review state, its lock state and any corrections.

**Reviewing.** Each date carries a review state. A date is marked reviewed individually, or in
bulk with the button at the top, which reports how many pending dates it marked and how many were
already reviewed. Review is what a month's closure checks: an unreviewed date is a blocker.

**Locks.** A date can be locked, which holds it still against a later regeneration. Locking is how
you protect the days you have personally arranged before asking for a different draft of the rest.

**Correcting a day.** Entries can be edited, added, removed or moved between dates, and a whole
visit can be moved to another date. Every correction is recorded as a correction, not as an
overwrite: the tool remembers that a person put this entry here.

**Asking for a different draft.** *Generate candidate* runs the allocator again with a new seed
and stages the result as a candidate beside the current revision. The two are compared, date by
date and entry by entry, before anything is accepted; *Accept candidate* makes it the current
revision, *Refuse candidate* leaves the current one untouched. **Locked dates and reviewed dates
survive re-generation**, which is the whole point of locking them.

**Months.** The month panel lists every month in the draft, its state, and what is needed to close
it. Each month closes on its own: **Finalise this month**. Closing every open month at once is
also offered as **Finalize all months**, which is all or nothing — if any month cannot be closed,
none of them is, and the page names the month in the way.

Before a month closes, the page says whether it *can*, and what is in the way: unreviewed dates,
missing content, or recorded findings that you must acknowledge. Acknowledging is not a hidden
switch — the finding stays on the record, and saying you have seen it is the whole act.

A closed month is **immutable**: it cannot be edited, regenerated or reopened. What can still be
done with it is export it, print it, or start a correction — a **new version** that supersedes it
while the old one stays on the record, downloadable and printable exactly as it was issued.

**Issued versions.** Each closed month keeps every version it ever issued, with its finalisation
identifier, its note and its date. Any of them can be downloaded as Excel or PDF.

### 5.7 History

Every generation attempt ever made, grouped by outcome:

- **Draft registers** — attempts that produced an allocation-complete draft;
- **Attempts that could not cover the month** — infeasible, with the constraint responsible;
- **Attempts the search could not settle** — unresolved within the budget.

Failed attempts are listed deliberately. They are findings about the month — the catalogue is too
thin, the ceilings too tight, the period too short — and they are the evidence for changing an
input rather than pressing Generate again and hoping.

Any listed run can be opened to see its stored evidence.

### 5.8 Backup

Three things live on this tab.

**Take a backup.** One button, one file. The copy is taken while the tool keeps working and holds
every change saved when it started. It contains the whole database. Download it and keep it
somewhere other than this computer.

**Restore from a backup.** Choose the file, check it first, then confirm. Nothing is replaced
until you confirm, and what the check reports is what is inside the file before it is trusted
(masters, months, revisions). The file must be a backup this tool made: a substituted or edited
database is refused rather than loaded.

**Start a new office.** Two scopes, each with its own button and its own `RESET` confirmation:

- **Clear everything** — the tool as it was on its first start.
- **Clear everything, but keep the calendar** — keeps the Karnataka General Holiday list, the
  leave types and the weekly-off rules, because any office in the state works from the same ones.

Before anything is removed the page counts what the scope would take, offers a backup of the
database as it stood, and afterwards leaves a recovery copy beside the live one. Export files
already written are not touched.

A reset does **not** put any seed data back: a fresh start has **no areas and no feeders**, because
those are one office's own places. Only the records you import for the new office appear.

---

## 6. A full walkthrough: a new office to a first issued month

This is the whole job, once, in order, with the reason for each step. It uses the file names in
`data/templates/`, which are the office's own equivalents once filled in.

### Step 1 — Start it and look at the Overview

Start the tool and open http://127.0.0.1:8010. The Overview will report almost everything as
missing. That is correct on a fresh start: a new installation has **no areas and no feeders**,
because those are the office's own places and nobody else's.

### Step 2 — Read the areas

*Imports* → Dataset **Areas** → choose `04_area_master.csv` → **Read and judge** → **Open** →
read the rows → **Accept into master data**.

The area master needs a Kannada name per area. English name, aliases, weight and the area's own
monthly minimum, maximum and revisit gap are read when the sheet has them.

### Step 3 — Read the feeders

The same, with **Feeders** and `05_feeder_master.csv`. A feeder needs only its name. Its manual
multiplier, monthly minimum, maximum and revisit gap are read when present.

### Step 4 — Read the DTC roster

**DTC roster**, `01_dtc_master_roster.csv`. It needs the DTC identifier, the DTC name and the
**Class** column. This is the step that is refused if steps 2 and 3 are missing, or if any
non-blank feeder cell names a feeder the feeder master does not carry.

The class column decides what is kept: `SCF` and `SCE` rows are excluded, `DEPT` / `MSB` / `OWN`
are included and mapped to their canonical class. The letters in front of an identifier mean
nothing to the tool — it reads the office's own code as supplied, whether that is `SCD-12` or
`SCE142`.

A blank feeder cell is accepted with a warning; the transformer is then not reached through any
feeder's capacity.

### Step 5 — Read the activity catalogue

**Activity catalogue**, `03_activity_catalogue_blank_import.csv` filled in. It needs a category
and an English description per row. Kannada description, weight and the per-activity monthly
minimum, maximum, repeat gap and weekday restriction are read when present. Use
`03_activity_proposed_examples_DO_NOT_IMPORT.csv` as a shape reference only — its rows are
fictional.

### Step 6 — Read the General Holiday list

**General Holidays**, `02_karnataka_general_holidays_2025_2026.csv`, or the current official list
for the years you will plan. Only General Holidays. Restricted Holidays are ordinary working days
and must not be entered.

### Step 7 — Set the calendar

*Calendar* → set the weekly offs (Sunday, second and fourth Saturday by default) → **Save rules**.
Then *From* / *To* across the period you plan → **Show calendar**. Read the day table and the
conflict banner. Add anything missing: leave records for the office, dated events, and per-date
overrides where a single date differs from the pattern.

### Step 8 — Read the leave sheet (month by month)

**Monthly leave records**, the month's leave sheet. One row covers a date range and writes one
leave record per date. The leave type must already exist. Rejected, cancelled and withdrawn rows
are left out with their reason.

### Step 9 — Check the Overview again

**Refresh readiness**. You are ready to generate when the DTC master, the area master, the feeder
master, the activity catalogue and the holiday baseline for each planned year are present.

### Step 10 — Generate

*Generate*. A typical first run for a single month:

- **From / To** — the month, in full.
- **Mode** — coverage cycle once you have declared one; capacity fallback until then.
- **Cycle start month**, **Cycle months**, **Visits per asset** — for example `2026-07`, `4`, `1`
  if that is the declared cycle for this office.
- **Regular activities** — minimum 3, maximum 5, or whatever the office approved.
- **Per-kind ceilings** — DTC 3, area 2, feeder 1 unless the office decided otherwise.
- **Entry language** — English to begin with; Kannada once the masters carry Kannada text.
- **Seed** — leave it at 0 for the first run.

Press **Generate** and watch the progress bar. If it reports **infeasible**, read
[section 10](#10-messages-you-will-see-and-what-to-do) before pressing it again: the message names
the constraint that could not be met, and pressing again with the same inputs produces the same
answer.

### Step 11 — Read the draft

*Draft* opens the newest revision. Read the exceptions and the month evidence first, then the
register, day by day. This is the step where a person decides whether the plan is the office's
month.

### Step 12 — Correct and lock

Change what is wrong: edit, add, remove or move an entry; move a whole visit to another date. Lock
the dates you have settled, so that a later candidate regenerates around them instead of through
them.

If you want a different plan for the unlocked remainder, press **Generate candidate**, compare it
with the current revision, then **Accept candidate** or **Refuse candidate**.

### Step 13 — Review

Mark each date reviewed, or use the bulk button and then look at the dates it marked. Review is
what a month's closure checks.

### Step 14 — Close the month

In the month panel, press **Refresh finalisation checks**, read what is in the way, acknowledge any
recorded finding you have seen, and press **Finalise this month**. The month becomes immutable and
its version is issued.

### Step 15 — Export and print

From the closed month: **Download Excel (.xlsx)**, **Download PDF (.pdf)**, or **Print / Save as
PDF**. From the register level, the packages and the single combined press PDF are on offer —
see [section 8](#8-exports-and-printing).

### Step 16 — Take a backup

*Backup* → **Create a backup** → download it → keep it somewhere other than this computer. Do this
whenever a month is issued; it is the cheapest insurance in the tool.

---

## 7. The rules the tool enforces

Nothing in this section is advisory. Each one is either refused with a message or enforced by the
allocator, and the ones that can make a month impossible are the ones to understand before
planning.

### The calendar

- **Weekly offs**: every Sunday and the second and fourth Saturday, by default and configurable.
- **Only Karnataka General Holidays are operational holidays.** Restricted Holidays are ignored
  entirely, names and all.
- **The Monday safety meeting is recorded automatically on every Monday,** including a Monday that
  is a holiday or a leave date. An individual-date exception is supported.
- **Overlapping reasons are preserved.** A leave on a holiday is a conflict to be shown, not data
  to be deleted.
- **An override never erases the fact underneath it.** The effective working-day decision and the
  source calendar fact both stay on the record.

### Activities

- **The minimum is a floor and the maximum is a ceiling, per working day.** Both are the office's
  numbers, entered on the Generate form; 3 and 5 are only the form's defaults.
- **Events, meetings, the safety meeting and field visits are additional** and do not consume the
  activity quota.
- Each activity can carry its own monthly minimum and maximum and its own repeat gap, and can be
  restricted to certain weekdays.

### Visits

- **One ceiling per kind of visit**, independent of the others: DTC, area and feeder. The defaults
  are 3, 2 and 1 per working day, and a date may hold up to each of them.
- **Each asset's monthly maximum is a cap, not an obligation.** The default is 3 per month.
- **The minimum revisit interval is 7 calendar days** by default, per asset, and it is calendar
  days rather than working days.
- **A DTC is never visited before the day it was commissioned**, where the roster carries that
  date.
- **Primary DTC coverage is a declared cycle.** Declaring "each active DTC once in a four-month
  cycle" is a coverage promise. Without a cycle, the DTC target is a capacity-weighted suggestion
  and is not a promise that everything is reached.
- **Area and feeder targets follow their own monthly minimums and maximums**, not the DTC cycle.

### What a plan may not do

- It may not relax a minimum, maximum, revisit interval or daily ceiling to make a month fit. If
  the constraints cannot all be met, it reports infeasibility and names the constraints
  responsible.
- It may not plan a partial month. A period that begins or ends mid-month is refused, because the
  monthly minimums would be prorated against days that do not exist in the plan.
- It may not overwrite a locked date, a reviewed date, or a closed month.
- It may not decide that a month is closed without a person having reviewed its dates.

### Revision rules

- Every generation is a new revision; nothing is destroyed by generating again.
- A candidate is compared with the current revision before it can replace it.
- A closed month is immutable. A correction creates a new version that supersedes it, and the old
  version remains on the record, exportable exactly as issued.
- A finalised revision cannot be deleted or rewritten. Corrections and new versions are the only
  routes forward.

---

## 8. Exports and printing

There are two families of export: what one month produces, and what a whole register produces.

### One closed month

| Action | What you get |
| --- | --- |
| **Download Excel (.xlsx)** | A workbook for that month, with the register rows and a metadata sheet identifying the register, month, version and finalisation. |
| **Download PDF (.pdf)** | The month as a document, ending with an issued-version **identification page**: what was issued, when, from which revision and under which finalisation. That page is the archive's proof and it is not part of what the office prints. |
| **Print / Save as PDF** | The same document in the browser, so you can print it or save it through the browser's own print dialog. |

Any earlier version of a closed month downloads the same way, at the version it was issued.

### The whole register

| Action | What you get | Notes |
| --- | --- | --- |
| **Download all issued months** (register package) | A ZIP with one workbook per issued month, a combined print-ready HTML document, and a README naming each month, its version and its finalisation | Months with no issued finalisation are omitted, and the package says so. |
| **PDF package** | A ZIP with one PDF per issued month, and a README naming each month and version | Same omission rule. |
| **Single PDF with no metadata** | One PDF holding every issued month, in month order, with each month's identification page left out | One download, straight from the browser. This is the press copy. |
| **Excel package** | A ZIP with one workbook per issued month, using the same `Register` and `Metadata` sheets as the single-month download | |

A register with nothing issued yet refuses any of these with a sentence saying so, rather than
producing an empty file.

### Joining PDFs you already exported

If the month PDFs are already on disk, put them in one folder and run:

```powershell
.\.venv\Scripts\dailyworkreg-print.exe "C:\path\to\print\before"
```

It drops each file's last page (the identification page) and joins the rest in file-name order.
The document is written to an `after` folder beside the input folder, named after the months it
holds — `daily-dairy-register-2025-04-to-2026-10.pdf`, for example — and the folder it read is left
exactly as it was. A single-page PDF is named rather than merged, because dropping its only page
would leave nothing of that month.

`python -m dailyworkreg.interfaces.print_pdfs <folder>` does the same thing without the launcher.

Both the button and the folder command drop the identification page by the same rule, which lives
in one place in the code, so a month pressed from one route and the same month pressed from the
other produce the same register.

---

## 9. Backup, restore and handing over

### What a backup holds

Everything the tool knows: masters, staged imports, every revision of every draft, every review
and lock, every closed month, every issued version and every correction. It is the whole database
in one file, not a list of records.

### When to take one

- Before any update of the code.
- After closing a month, and after any large import.
- Before a reset or a restore.
- Monthly, into a folder outside this computer, kept with the office's other records.

A downloaded backup exists only until the application is closed, so download it in the same
session you created it.

### Restoring

Open the **Backup** tab, choose the file, **check it first**, then confirm. Nothing is replaced
until you confirm. The check reports what is inside the file — masters, months, revisions — before
it is trusted. A file that is not a backup this tool made is refused rather than loaded.

When the restore finishes, the tool has already kept a copy of the database that was in use before
it, beside the live one, and names it. Reload the page to work with the restored records, and
delete that copy once you are satisfied.

Only one restore runs at a time. A second attempt while the first is running says so and asks you
to wait, rather than corrupting the file.

### Handing the computer to a different office

This is the **Start a new office** panel, and the choice of scope matters:

- **Clear everything** — the tool exactly as it was on its first start.
- **Clear everything, but keep the calendar** — keeps the Karnataka General Holiday list, the
  leave types and the weekly-off rules, removes everything else.

Before removing anything, the page counts what the selected scope would take and offers a backup
of the database as it stood. Afterwards it leaves a recovery copy beside the live one, names it,
and offers the removed records as a download. Exports already written to disk are untouched.

Then start again from [step 2 of the walkthrough](#6-a-full-walkthrough-a-new-office-to-a-first-issued-month):
a fresh start has no areas and no feeders, and the new office's own files are what fill them.

---

## 10. Messages you will see, and what to do

Every refusal names what happened and what to do about it. The table below groups the ones you
will meet most often. Where a message quotes a constraint name in brackets, that name is the
stable identifier the tool uses and is worth writing down when you report a problem.

### At import time

| Message, in substance | What it means | What to do |
| --- | --- | --- |
| *"this roster names transformers, and no area is stored yet. Read the area master (04_area_master.csv) first, then read this roster again"* | The DTC roster was read before the areas. | Read and **accept** the area master, then read the roster again. |
| *"no feeder is stored yet, so no transformer can be placed on one. Read the feeder master (05_feeder_master.csv) first…"* | The same, for feeders. | Read and accept the feeder master, then the roster. |
| *"the feeder master does not carry N name(s) this roster gives: '…'…"* | The roster names a feeder the feeder master does not have. | Either the wrong feeder master was imported, or the roster has a typo. Fix one of them and read the roster again. |
| *"the file has no column for X; the … import needs Y"* | The sheet is missing a column the dataset cannot work without. | Add or rename the column. Headings are matched leniently, so `DTC ID` and `dtccode` both work. |
| *"N row(s) cannot be imported; correct the source or reject the batch, because applying the rest would silently drop them"* | At least one row is unusable. | Open the batch, read each red row's reason, fix the sheet, read it again — or reject the batch to leave the masters untouched. |
| *"this batch has no row that would change the master"* | Every row was excluded (all `SCF`/`SCE`, or every leave row refused). | Nothing to accept. Reject the batch; it changed nothing. |
| *"leave type 'X' is not one of the stored leave types (CL (Casual Leave), …); add it on the Calendar tab first"* | A leave sheet names a leave type the office has not configured. | Add the leave type, or correct the sheet. An import never invents a type. |

### At generation time

| Message, in substance | What it means | What to do |
| --- | --- | --- |
| *infeasible*, with the constraints responsible (`revisit_gap_violated`, `asset_monthly_max_exceeded`, `dtc_daily_cap_exceeded`, `activity_placement_cannot_fill_a_day`, `search_node_limit_reached`, …) | No plan satisfies every constraint at once. | Read which constraint was hit. The usual fixes: widen `Monthly max` / `Repeat gap` in a master, lower a daily ceiling, lower the activity minimum, lengthen the period, or declare a cycle the period can satisfy. |
| `period_not_single_calendar_month` / a refusal for a partial month | The requested range begins or ends mid-month. | Generate whole months. Partial-month planning is deliberately not implemented. |
| `draft_range_outside_coverage_cycle` | The period is not inside the declared cycle. | Either widen the cycle, change its start, or generate a period the cycle covers. |
| `period_date_not_working` / a period with no working day | Every date in the range is a weekly off, holiday or leave. | Check the calendar. |
| `asset_monthly_minimum_not_met` | A configured monthly minimum could not be honoured, usually because the asset has too few eligible dates. | Lower that minimum, or widen the period. |
| A very long or very large run | A multi-month, multi-visit, high-capacity run can take minutes. | The progress bar reports the month it has reached. Leave the window open. |

### At review and closure

| Message, in substance | What it means | What to do |
| --- | --- | --- |
| *"The server reports this month cannot be closed yet"*, with a blocker table | Something is outstanding: unreviewed dates, missing content, or a finding to acknowledge. | The blocker table names each one and, where relevant, the dates. Clear them and press **Refresh finalisation checks**. |
| *"Recorded findings to acknowledge"* | Over-covered commitments or calendar conflicts are on the record. | Tick each one you have seen, then close. The finding stays on the record; acknowledging is the whole act. |
| *"Closing every open month at once is all or nothing"* | The bulk close hit a month that cannot close. | Nothing was written. Close that month on its own after clearing its blockers, or clear them and run the bulk close again. |
| *"This month has not been finalised"* | You asked for an issued document from a month that is still a draft. | Close the month first. |

### At export time

| Message, in substance | What it means | What to do |
| --- | --- | --- |
| *"the PDF layout engine is not installed on this computer, so a PDF cannot be written; install it once with 'python -m playwright install chromium' and try again"* | Chromium was never installed, or the profile folder that holds it was deleted. | Run that exact command, then try again. Excel exports are unaffected. |
| *"a PDF render failed"* | The document could not be produced and nothing partial was sent. | Try again; if it repeats, print from the browser (**Print / Save as PDF**) and report it with the month and version. |
| A package refusing with "no issued month" | The register has nothing finalised. | The package contains only issued months by design. Close a month first. |

### At backup, restore and reset

| Message, in substance | What it means | What to do |
| --- | --- | --- |
| *"backup protections are missing"* | The file is not a backup this tool made, or it was edited after the fact. | Restore only a file the tool produced. |
| *"Another restore is in progress"* | One restore is already running. | Wait for it to finish, then check the file again. |
| *"type RESET"* | A reset needs the word typed in the box beside the scope you mean. | Type `RESET`. The two scopes have separate boxes on purpose. |
| *"The tool is busy replacing its database"* | A restore is running. | Wait, then try again. |

### Reading the log

Nothing here writes a technical log the operator needs. If a message does not explain itself, note
the page, the month and the exact sentence; that is what makes a report actionable.

---

## 11. Troubleshooting

**Nothing opens at http://127.0.0.1:8010.**
The window running `dailyworkreg` is the application. If it is not running, start it. If it is
running and reported a different port, use the port it printed. If it exited immediately, run it
again from a PowerShell window to read the error.

**The port is already in use.**
Something else is on 8010. Start with `--port 8020` and open that address instead.

**"Windows protected your PC" when running `python.exe` or a script.**
This is the SmartScreen prompt for a new executable in a folder Windows has not seen. It is
expected for a fresh checkout. Confirm only if the checkout is yours.

**The page loads but shows nothing.**
Reload with `Ctrl+F5`. The client is served with a version key, so a stale copy is unusual; a hard
reload settles it.

**PDF downloads refuse while Excel works.**
The Chromium that writes PDFs is missing. Run
`.\.venv\Scripts\python.exe -m playwright install chromium` and try again.

**An import refuses saying a column is missing, though the sheet has it.**
Check the heading's spelling. Headings are matched on letters and digits only, so case, spaces and
punctuation do not matter, but different words do: the alias list in
[section 12](#12-the-files-in-datatemplates-and-which-ones-the-tool-reads) is what is read.

**Generation returns infeasible for a month that used to work.**
Something changed: a master's monthly minimum, a daily ceiling, the holiday list, the weekly offs,
or the period. Read the constraint named in the result — it is one of the six in the table above in
almost every case.

**A month is closed and the register has a mistake in it.**
Do not try to reopen it; that is not possible by design. Use **Start a correction**, which issues a
new version. The old version stays downloadable.

**The computer was replaced and the register is gone.**
Restore the last backup from the Backup tab. If there is no backup, the database file lives at
`C:\Users\<you>\AppData\Local\DailyDairyRegister\dailyworkreg.db` — copying that file to the same
place on the new computer is equivalent.

**Two copies of the tool are running.**
Two windows against the same database file: stop one. SQLite allows only one writer at a time, and
although the tool waits and retries, two applications is not a supported arrangement. A second copy
started with `--db` pointing at a rehearsal file is fine.

**The tool feels slow.**
A multi-month generation with many visits is genuinely heavy work — minutes, not seconds — and the
progress bar is the honest report of it. Opening the Draft tab of a very large register also takes a
moment. Nothing is stuck if the progress bar is moving.

---

## 12. The files in `data/templates`, and which ones the tool reads

`data/templates/` holds the blank sheets the office fills in, plus files marked
`_DO_NOT_IMPORT`, which are shape illustrations with fictional rows. **Never import a
`_DO_NOT_IMPORT` file.**

Files the importer registers as datasets:

| File | Dataset | Read when present | Required |
| --- | --- | --- | --- |
| `01_dtc_master_roster.csv` | DTC roster | Feeder, Feeder code, MDM code, kVA, Station, Date of commissioning | DTC id, DTC name, Class |
| `02_karnataka_general_holidays_2025_2026.csv` | General Holidays | Description | Date, Holiday name |
| `03_activity_catalogue_blank_import.csv` | Activity catalogue | Kannada description, Weight, Monthly min, Monthly max, Repeat gap, Weekdays | Category, Description |
| `04_area_master.csv` | Areas | Area id, English name, Aliases, Weight, Monthly min, Monthly max, Revisit gap | Kannada name |
| `05_feeder_master.csv` | Feeders | Feeder id, Manual multiplier, Monthly min, Monthly max, Revisit gap | Feeder name |
| `08_monthly_leave_records.csv` | Monthly leave records | To date, Full day, Approval status, Employee id, Employee name, Notes | Leave type, From date |

Files the tool does **not** import, kept as working evidence:

| File | Why |
| --- | --- |
| `06_dtc_classification_reference.csv` | Reference for the class mapping the roster already uses. |
| `07_dtc_weighting_template.csv` and its worked examples | Working sheet for computing a weight by hand. The tool computes weight as kVA × class multiplier × individual adjustment. |
| `09_monthly_special_events.csv` | Dated events are entered on the Calendar tab. |
| `10_monthly_additional_meetings.csv` | The same, as events. |
| `11`, `12`, `13` fixed DTC / area / feeder visits | Fixed commitments are not yet an import dataset. |
| `14_monthly_date_overrides.csv` | Per-date overrides are managed in the calendar model. |

`data/record_A/` holds the real office's filled sheets for reference and for rehearsal, and
`data/Unit-5/` a second office's set. Neither is read automatically: you choose the file in the
Imports tab.

---

## 13. For whoever maintains the installation

- **The dependency manifest is `pyproject.toml`.** Do not keep a second hand-edited list.
  `pip install -e .` installs runtime dependencies; `".[test]"` adds pytest and httpx.
- **`playwright` is a runtime dependency** because the tool writes its own PDFs, but the Chromium
  it drives is a separate one-time download per computer, outside the checkout.
- **The database is outside the checkout.** `DAILYWORKREG_DB` or `--db` moves it; the default is
  the per-user application data folder.
- **The tool is single-operator and loopback-only.** There is no authentication and no host option
  to widen it. Do not put it behind a proxy or a tunnel.
- **Updates are `git pull` then reinstall, then restart.** The restart applies schema migrations.
  Take a backup first.
- **The regression suite** is documented in `documentation/REGRESSION_TESTING.md`. It runs entirely
  against temporary databases and never touches the live one:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests -q
  ```

- **Local development, first-time setup and update detail** are in
  `documentation/LOCAL_DEVELOPMENT.md`; the product requirements are in
  `documentation/PRODUCT_REQUIREMENTS_BASELINE.md`; the change history is `CHANGELOG.md`.

---

### A last word on what to trust

The register the tool produces is a plan. The register you sign is the record. Every screen here
exists to make the difference obvious: a draft is called a draft, a reviewed day says so, a closed
month cannot be edited, and a corrected month keeps both versions. Keep the backups, review before
you close, and the tool will hold up when somebody asks what was issued in a particular month.
