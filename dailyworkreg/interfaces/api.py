"""The local HTTP surface.

Every route is a thin translation: JSON in, an application call, JSON out. No route
decides a schedule, reproduces a capacity rule or filters a violation, because a second
copy of a domain rule in a request handler is a rule that will disagree with the domain
within a release.

The server binds to localhost only. It is a single-operator tool on a desk, not a
service, and it carries no authentication because it has no network exposure to
protect.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from ..application import backup_restore
from ..application import export as documents_source
from ..application.calendar_projection import (
    WeeklyOffRules,
    load_weekly_off_rules,
    project_calendar,
    save_weekly_off_rules,
)
from ..application.draft_generation import DraftMode, generate_draft
from ..application.draft_requests import GenerationInputError, GenerationSpec, prepare_generation
from ..application.finalisation import (
    FinalisationError,
    abandon_correction,
    check_finalisation,
    correction_chain,
    create_correction_revision,
    finalisation_record,
    finalise_month,
    month_lifecycle,
)
from ..application.generation_runs import (
    GenerationStorageError,
    list_runs,
    load_readiness,
    load_register,
    regenerate,
    runs_by_outcome,
    save_run,
)
from ..application.readiness import assess_readiness
from ..application.regeneration import (
    accept_candidate,
    awaiting_decision,
    compare_revisions,
    regenerate_candidate,
    reject_candidate,
)
from ..application.register_edits import (
    add_entry,
    edit_entry,
    move_entry,
    move_visit,
    remove_entry,
)
from ..application.revisions import (
    RevisionError,
    clear_review,
    lock_date,
    mark_all_reviewed,
    mark_reviewed,
    review_state,
    revision_summary,
    unlock_date,
)
from ..application.revisions import (
    registers as register_history,
)
from ..application.snapshot import SnapshotError
from ..domain.cycle import MonthKey
from ..domain.validator import InputError, Visit
from ..infrastructure.backup import Limits
from ..infrastructure.imports.datasets import JUDGES
from ..infrastructure.imports.tabular import SourceFormatError, read_source_bytes
from ..infrastructure.sqlite import calendar_records as cal
from ..infrastructure.sqlite import imports as staged
from ..infrastructure.sqlite import masters, revision_state, seed
from ..infrastructure.sqlite.database import open_database, resolve_db_path
from . import documents
from .maintenance import (
    BackupDesk,
    MaintenanceBusy,
    MaintenanceGate,
    PendingRestore,
)

HERE = Path(__file__).parent
STATIC = HERE / "static"
TEMPLATES = HERE / "templates"

C_LOCAL_ONLY = "server_is_local_only"


class SurfaceError(ValueError):
    """A refusal this surface makes itself, before any application call."""

    def __init__(self, constraint: str, detail: str, status: int = 400):
        self.constraint = constraint
        self.detail = detail
        self.status = status
        super().__init__(detail)


def _fail(constraint: str, error: Exception, status: int = 400) -> JSONResponse:
    detail = getattr(error, "detail", None) or str(error)
    return JSONResponse(
        status_code=status, content={"error": {"constraint": constraint, "detail": detail}}
    )


def _tokens(value: str | None) -> tuple[str, ...]:
    """Acknowledgements from a query string, one per repeated parameter."""

    if not value:
        return ()
    return tuple(item for item in (part.strip() for part in value.split(",")) if item)


def _error_payload(error: Exception) -> JSONResponse:
    constraint = getattr(error, "constraint", "request_refused")
    status = getattr(error, "status", 400)
    return _fail(constraint, error, status)


def _date(text: str | None, fallback: date) -> date:
    if not text:
        return fallback
    return date.fromisoformat(text)


def create_app(db_path: str | Path | None = None) -> FastAPI:
    """Build the application. ``db_path`` exists for tests and for a chosen location."""

    app = FastAPI(
        title="DailyDairyRegister",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.db_path = db_path
    # Resolved once, here, because a restore replaces a named file rather than whichever
    # file the environment happens to point at later.
    target_db = resolve_db_path(db_path)
    # Admission for ordinary requests, and the exclusive hold a replacement needs.
    gate = MaintenanceGate()
    desk = BackupDesk()
    app.state.maintenance_gate = gate
    app.state.backup_desk = desk
    # Autoescaping is on by default, which is what makes a stored activity title or a
    # holiday name safe to place in the page: nothing the record holds becomes markup.
    templates = Jinja2Templates(directory=str(TEMPLATES))

    def connection() -> sqlite3.Connection:
        return open_database(db_path)

    def prepare() -> None:
        """Seed the approved identities once, at startup, idempotently.

        They are confirmed policy rather than user data, so they must exist before any
        screen is opened; the seed records that it ran, which is what makes a restart
        leave an edited master alone.
        """

        existing = connection()
        try:
            seed.seed_approved_identities(existing)
        finally:
            existing.close()

    prepare()

    def get_db() -> Iterator[sqlite3.Connection]:
        # Every route that touches the database passes the barrier first, so a replacement
        # can rely on the connection count reaching zero rather than on good timing.
        with gate.request():
            existing = connection()
            try:
                yield existing
            finally:
                existing.close()

    @app.exception_handler(SurfaceError)
    async def surface_error(_: Request, error: SurfaceError) -> JSONResponse:
        return _error_payload(error)

    @app.exception_handler(backup_restore.BackupError)
    async def backup_refused(_: Request, error: backup_restore.BackupError) -> JSONResponse:
        payload = {"error": {"constraint": error.constraint, "detail": error.detail}}
        payload["error"].update(error.fields)
        return JSONResponse(status_code=400, content=payload)

    @app.exception_handler(MaintenanceBusy)
    async def maintenance_busy(_: Request, error: MaintenanceBusy) -> JSONResponse:
        return _fail(error.constraint, error, error.status)

    @app.exception_handler(InputError)
    async def domain_input_error(_: Request, error: InputError) -> JSONResponse:
        return _error_payload(error)

    @app.exception_handler(GenerationInputError)
    async def generation_input_error(
        _: Request, error: GenerationInputError
    ) -> JSONResponse:
        return _error_payload(error)

    @app.exception_handler(masters.StorageError)
    async def storage_error(_: Request, error: masters.StorageError) -> JSONResponse:
        return _error_payload(error)

    @app.exception_handler(GenerationStorageError)
    async def generation_storage_error(
        _: Request, error: GenerationStorageError
    ) -> JSONResponse:
        return _error_payload(error)

    @app.exception_handler(SnapshotError)
    async def snapshot_error(_: Request, error: SnapshotError) -> JSONResponse:
        return _error_payload(error)

    @app.exception_handler(FinalisationError)
    async def finalisation_error(
        _: Request, error: FinalisationError
    ) -> JSONResponse:
        payload: dict[str, Any] = {
            "error": {"constraint": error.constraint, "detail": error.detail}
        }
        if error.blockers:
            # Every reason a month cannot be closed travels with the refusal, so the page
            # shows the whole picture rather than the first thing that went wrong.
            payload["error"]["blockers"] = [item.as_dict() for item in error.blockers]
        return JSONResponse(status_code=400, content=payload)

    @app.exception_handler(RevisionError)
    async def revision_error(_: Request, error: RevisionError) -> JSONResponse:
        payload: dict[str, Any] = {
            "error": {"constraint": error.constraint, "detail": error.detail}
        }
        if error.violations:
            # A refused correction carries the same structured findings any other
            # refusal does, so the operator is told which rule and which asset.
            payload["error"]["violations"] = [
                _attribution(item) for item in error.violations
            ]
        return JSONResponse(status_code=400, content=payload)

    # --- pages ---------------------------------------------------------

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(HERE / "templates" / "index.html")

    @app.get("/api/health")
    def health(db: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
        return {
            "ok": True,
            "master_version": masters.current_version(db),
            "schema_versions": list(_applied(db)),
        }

    # --- calendar rules -------------------------------------------------

    @app.get("/api/settings/calendar")
    def read_calendar_settings(
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        rules = load_weekly_off_rules(db)
        return {
            "sundays": rules.sundays,
            "second_saturday": rules.second_saturday,
            "fourth_saturday": rules.fourth_saturday,
        }

    @app.put("/api/settings/calendar")
    def write_calendar_settings(
        payload: dict[str, Any], db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        rules = WeeklyOffRules(
            sundays=bool(payload.get("sundays", True)),
            second_saturday=bool(payload.get("second_saturday", True)),
            fourth_saturday=bool(payload.get("fourth_saturday", True)),
        )
        save_weekly_off_rules(db, rules)
        return {"saved": True}

    # --- readiness ------------------------------------------------------

    @app.get("/api/readiness")
    def readiness(
        start: str | None = None,
        end: str | None = None,
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        today = date.today()
        first = _date(start, today.replace(day=1))
        last = _date(end, first)
        report = assess_readiness(db, first, last)
        return {
            "can_generate": report.can_generate,
            "is_operational_diary": report.is_operational_diary,
            "missing_content": list(report.missing_content),
            "items": [
                {
                    "key": item.key,
                    "label": item.label,
                    "satisfied": item.satisfied,
                    "detail": item.detail,
                    "blocks_draft": item.blocks_draft,
                }
                for item in report.items
            ],
        }

    # --- masters --------------------------------------------------------

    @app.get("/api/masters/summary")
    def master_summary(db: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
        return {
            "version": masters.current_version(db),
            "dtc": len(masters.list_dtcs(db)),
            "area": len(masters.list_areas(db)),
            "feeder": len(masters.list_feeders(db)),
            "activity": len(masters.list_activities(db)),
            "holiday": len(cal.list_holidays(db)),
            "leave_record": len(cal.list_leave_records(db)),
            "event": len(cal.list_events(db)),
        }

    @app.get("/api/masters/dtc")
    def read_dtcs(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, Any]]:
        return [_dtc(dtc) for dtc in masters.list_dtcs(db)]

    @app.put("/api/masters/dtc")
    def write_dtc(
        payload: dict[str, Any], db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        row = masters.upsert_dtc(
            db,
            dtc_id=payload["dtc_id"],
            name=payload["name"],
            dtc_class=payload["dtc_class"],
            canonical_feeder_id=payload.get("canonical_feeder_id"),
            kva=payload.get("kva"),
            station=payload.get("station"),
            individual_adjustment=payload.get("individual_adjustment", 1.0),
            monthly_min=payload.get("monthly_min", 0),
            monthly_max=payload.get("monthly_max", 3),
            revisit_gap_days=payload.get("revisit_gap_days", 7),
            active=payload.get("active", True),
        )
        return _dtc(row)

    @app.get("/api/masters/area")
    def read_areas(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, Any]]:
        return [_area(area) for area in masters.list_areas(db)]

    @app.put("/api/masters/area")
    def write_area(
        payload: dict[str, Any], db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        row = masters.upsert_area(
            db,
            area_id=payload["area_id"],
            name_kn=payload["name_kn"],
            name_en=payload.get("name_en"),
            weight=payload.get("weight", 1.0),
            monthly_min=payload.get("monthly_min", 0),
            monthly_max=payload.get("monthly_max", 3),
            revisit_gap_days=payload.get("revisit_gap_days", 7),
            active=payload.get("active", True),
            aliases=tuple(payload.get("aliases", ())),
        )
        return _area(row)

    @app.get("/api/masters/feeder")
    def read_feeders(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, Any]]:
        return [_feeder(feeder) for feeder in masters.list_feeders(db)]

    @app.put("/api/masters/feeder")
    def write_feeder(
        payload: dict[str, Any], db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        row = masters.upsert_feeder(
            db,
            feeder_id=payload["feeder_id"],
            name=payload["name"],
            manual_multiplier=payload.get("manual_multiplier", 1.0),
            monthly_min=payload.get("monthly_min", 0),
            monthly_max=payload.get("monthly_max", 3),
            revisit_gap_days=payload.get("revisit_gap_days", 7),
            active=payload.get("active", True),
        )
        return _feeder(row)

    @app.get("/api/masters/activity")
    def read_activities(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, Any]]:
        return [_activity(item) for item in masters.list_activities(db)]

    @app.put("/api/masters/activity")
    def write_activity(
        payload: dict[str, Any], db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        row = masters.upsert_activity(
            db,
            activity_id=payload["activity_id"],
            category=payload["category"],
            description_en=payload["description_en"],
            description_kn=payload.get("description_kn"),
            weight=payload.get("weight", 1.0),
            monthly_min=payload.get("monthly_min", 0),
            monthly_max=payload.get("monthly_max", 10),
            repeat_gap_days=payload.get("repeat_gap_days", 2),
            weekday_mask=payload.get("weekday_mask"),
            active=payload.get("active", True),
        )
        return _activity(row)

    @app.get("/api/calendar/holidays")
    def read_holidays(
        year: int | None = None, db: sqlite3.Connection = Depends(get_db)
    ) -> list[dict[str, Any]]:
        return [
            {
                "holiday_id": item.holiday_id,
                "holiday_date": item.holiday_date.isoformat(),
                "name": item.name,
                "description": item.description,
                "source": item.source,
            }
            for item in cal.list_holidays(db, year=year)
        ]

    @app.put("/api/calendar/holidays")
    def write_holiday(
        payload: dict[str, Any], db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        day = date.fromisoformat(payload["holiday_date"])
        row = cal.upsert_holiday(
            db,
            holiday_id=payload.get("holiday_id") or f"H-{day.isoformat()}",
            holiday_date=day,
            name=payload["name"],
            description=payload.get("description"),
            source=payload.get("source", "operator"),
        )
        return {
            "holiday_id": row.holiday_id,
            "holiday_date": row.holiday_date.isoformat(),
            "name": row.name,
        }

    @app.delete("/api/calendar/holidays/{holiday_id}")
    def remove_holiday(
        holiday_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        cal.delete_holiday(db, holiday_id)
        return {"deleted": holiday_id}

    @app.get("/api/calendar/leave-types")
    def read_leave_types(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, Any]]:
        return [
            {"leave_type_id": item.leave_type_id, "name": item.name, "active": item.active}
            for item in cal.list_leave_types(db)
        ]

    @app.get("/api/calendar/leave-records")
    def read_leave_records(
        db: sqlite3.Connection = Depends(get_db),
    ) -> list[dict[str, Any]]:
        return [
            {
                "leave_id": item.leave_id,
                "leave_date": item.leave_date.isoformat(),
                "leave_type_id": item.leave_type_id,
                "leave_type_name": item.leave_type_name,
                "note": item.note,
            }
            for item in cal.list_leave_records(db)
        ]

    @app.put("/api/calendar/leave-records")
    def write_leave_record(
        payload: dict[str, Any], db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        day = date.fromisoformat(payload["leave_date"])
        cal.upsert_leave_record(
            db,
            leave_id=payload.get("leave_id") or f"L-{day.isoformat()}",
            leave_date=day,
            leave_type_id=payload["leave_type_id"],
            note=payload.get("note"),
        )
        return {"saved": True}

    @app.get("/api/calendar/events")
    def read_events(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, Any]]:
        return [
            {
                "event_id": item.event_id,
                "event_date": item.event_date.isoformat(),
                "title_en": item.title_en,
                "title_kn": item.title_kn,
                "event_kind": item.event_kind,
            }
            for item in cal.list_events(db)
        ]

    @app.put("/api/calendar/events")
    def write_event(
        payload: dict[str, Any], db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        day = date.fromisoformat(payload["event_date"])
        cal.upsert_event(
            db,
            event_id=payload.get("event_id") or f"E-{day.isoformat()}",
            event_date=day,
            title_en=payload["title_en"],
            title_kn=payload.get("title_kn"),
            event_kind=payload.get("event_kind", "event"),
        )
        return {"saved": True}

    @app.get("/api/calendar/overrides")
    def read_overrides(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, Any]]:
        return [
            {
                "override_id": item.override_id,
                "override_date": item.override_date.isoformat(),
                "is_working_day": item.is_working_day,
                "reason_en": item.reason_en,
                "reason_kn": item.reason_kn,
            }
            for item in cal.list_overrides(db)
        ]

    @app.put("/api/calendar/overrides")
    def write_override(
        payload: dict[str, Any], db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        day = date.fromisoformat(payload["override_date"])
        cal.upsert_override(
            db,
            override_id=payload.get("override_id") or f"O-{day.isoformat()}",
            override_date=day,
            is_working_day=bool(payload["is_working_day"]),
            reason_en=payload.get("reason_en"),
            reason_kn=payload.get("reason_kn"),
        )
        return {"saved": True}

    # --- calendar preview -----------------------------------------------

    @app.get("/api/calendar/preview")
    def preview_calendar(
        start: str, end: str, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        projection = project_calendar(db, date.fromisoformat(start), date.fromisoformat(end))
        return {
            "working_days": len(projection.working_days),
            "conflicts": list(projection.conflicts),
            "days": [
                {
                    "day": item.day.isoformat(),
                    "kind": item.kind.value,
                    "is_working_day": item.is_working_day,
                    "reasons_en": list(item.reasons_en),
                    "reasons_kn": list(item.reasons_kn),
                }
                for item in projection.days
            ],
            "entries": [
                {
                    "entry_id": item.entry_id,
                    "day": item.day.isoformat(),
                    "kind": item.kind.value,
                    "title_en": item.title_en,
                }
                for item in projection.entries
            ],
        }

    # --- imports --------------------------------------------------------

    @app.post("/api/imports")
    async def stage_import(
        dataset: str, file: UploadFile, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        if dataset not in JUDGES:
            raise SurfaceError(
                "unknown_dataset", f"{dataset!r} is not a dataset this tool imports"
            )
        content = await file.read()
        try:
            source = read_source_bytes(file.filename or "", content)
            batch, _ = staged.stage_source(db, dataset, source)
        except SourceFormatError as error:
            # Reading and staging both refuse a file in the same vocabulary, and both
            # refusals belong in front of whoever uploaded it. Only reading used to be
            # wrapped, so a file whose columns did not match the contract raised past
            # this handler and reached the operator as a server error with no reason.
            raise SurfaceError(error.constraint, error.detail) from error
        return _batch(db, batch.batch_id)

    @app.get("/api/imports")
    def list_imports(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, Any]]:
        return [
            {
                "batch_id": batch.batch_id,
                "dataset": batch.dataset,
                "filename": batch.source_filename,
                "status": batch.status,
                "row_count": batch.row_count,
                "error_count": batch.error_count,
                "warning_count": batch.warning_count,
                "staged_at": batch.staged_at,
                "accepted_at": batch.accepted_at,
            }
            for batch in staged.list_batches(db)
        ]

    @app.get("/api/imports/{batch_id}")
    def read_import(
        batch_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        return _batch(db, batch_id)

    @app.post("/api/imports/{batch_id}/accept")
    def accept_import(
        batch_id: str,
        payload: dict[str, Any] | None = None,
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        note = None if payload is None else payload.get("note")
        # Replacing the year is the default for a General Holiday import; merging is an
        # explicit choice, because a merge leaves last year's cancellation in place.
        holiday_mode = staged.C_HOLIDAY_REPLACE
        if payload and payload.get("holiday_mode"):
            holiday_mode = payload["holiday_mode"]
        result = staged.accept_batch(
            db, batch_id, note=note, holiday_mode=holiday_mode
        )
        return {
            "accepted": True,
            "inserted": result.inserted,
            "updated": result.updated,
            "excluded": result.excluded,
            "feeder_overrides_applied": result.feeder_overrides_applied,
        }

    @app.post("/api/imports/{batch_id}/reject")
    def reject_import(
        batch_id: str,
        payload: dict[str, Any] | None = None,
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        note = None if payload is None else payload.get("note")
        staged.reject_batch(db, batch_id, note=note)
        return {"rejected": True}

    # --- generations ----------------------------------------------------

    @app.post("/api/generations")
    def generate(
        payload: dict[str, Any], db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        spec = _spec(payload)
        prepared = prepare_generation(db, spec)
        draft = generate_draft(prepared.request)
        run_id = save_run(db, prepared, draft)
        return {
            "run_id": run_id,
            "register_id": (
                revision_state.register_for_run(db, run_id)
                if draft.is_allocation_complete
                else None
            ),
            # An attempt that produced no draft is a finding, not a revision: it has
            # no revision number to report and must not pretend to have one.
            "revision_number": (
                revision_summary(db, run_id)["revision_number"]
                if draft.is_allocation_complete
                else None
            ),
            "status": draft.status.value,
            "is_draft": draft.is_allocation_complete,
            "months": len(draft.months),
            "rows": len(draft.rows),
            "visits": len(draft.visits),
            "exceptions": len(draft.exceptions),
            "missing_content": list(prepared.readiness.missing_content),
        }

    @app.get("/api/generations")
    def history(db: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
        grouped = runs_by_outcome(db)
        return {
            key: [
                {
                    "run_id": run.run_id,
                    "created_at": run.created_at,
                    "mode": run.mode,
                    "start": run.start.isoformat(),
                    "end": run.end.isoformat(),
                    "months": list(run.months),
                    "seed": run.seed,
                    "outcome": run.outcome,
                    "is_draft": run.is_draft,
                    "revision_number": run.revision_number,
                    "register_id": run.register_id,
                    "kind": run.kind,
                    "parent_run_id": run.parent_run_id,
                    "missing_content": list(run.missing_content),
                }
                for run in runs
            ]
            for key, runs in grouped.items()
        }

    @app.get("/api/generations/{run_id}")
    def read_generation(
        run_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        register = load_register(db, run_id)
        return {
            "run_id": run_id,
            "status": register.status.value,
            "is_draft": register.is_allocation_complete,
            "mode": register.mode.value,
            "start": register.start.isoformat(),
            "end": register.end.isoformat(),
            "seed": register.seed,
            "generator_version": register.generator_version,
            "allocator_version": register.allocator_version,
            "missing_content": list(register.missing_content),
            "is_operational_diary": register.is_operational_diary,
            "months": [
                {
                    "month": month.month.label(),
                    "status": month.status.value,
                    "allocator_status": month.allocator_status,
                    "obligations": dict(month.obligations),
                    "declared_obligations": dict(month.declared_obligations),
                    "visits": len(month.visits),
                    "evidence": [_attribution(item) for item in month.evidence],
                }
                for month in register.months
            ],
            "exceptions": [
                {
                    "kind": item.kind.value,
                    "month": None if item.month is None else item.month.label(),
                    **_attribution(item),
                }
                for item in register.exceptions
            ],
            "rows": [
                {
                    "day": row.day.isoformat(),
                    "weekday": row.weekday,
                    "kind": row.kind.value,
                    "is_working_day": row.is_working_day,
                    "reasons_en": list(row.reasons_en),
                    "reasons_kn": list(row.reasons_kn),
                    "visits": [
                        {
                            "asset_id": visit.visit.asset_id,
                            "name_en": visit.name_en,
                            "name_kn": visit.name_kn,
                            "provenance": visit.provenance.value,
                        }
                        for visit in row.visits
                    ],
                    "entries": [
                        {
                            "entry_id": entry.entry_id,
                            "kind": entry.kind.value,
                            "title_en": entry.title_en,
                            "title_kn": entry.title_kn,
                            "provenance": entry.provenance,
                        }
                        for entry in row.entries
                    ],
                    "warnings": list(row.warnings),
                }
                for row in register.rows
            ],
        }

    @app.get("/api/generations/{run_id}/readiness")
    def read_generation_readiness(
        run_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> list[dict[str, Any]]:
        return [dict(item) for item in load_readiness(db, run_id)]

    @app.get("/api/generations/{run_id}/regenerate")
    def repeat_generation(
        run_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        """Prove the stored inputs still produce the same visits. Never a replacement."""

        again = regenerate(db, run_id)
        stored = load_register(db, run_id)
        same = [
            (visit.visit.asset_id, visit.visit.visit_date, visit.provenance)
            for visit in again.visits
        ] == [
            (visit.visit.asset_id, visit.visit.visit_date, visit.provenance)
            for visit in stored.visits
        ]
        return {"identical": same, "visits": len(again.visits)}

    # --- corrections -----------------------------------------------------

    @app.put("/api/generations/{run_id}/entries/{entry_id}")
    def correct_entry(
        run_id: str,
        entry_id: str,
        payload: dict[str, Any],
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        outcome = edit_entry(
            db,
            run_id,
            entry_id,
            title_en=payload.get("title_en"),
            title_kn=payload.get("title_kn"),
            kind=payload.get("kind"),
            reason=payload.get("reason"),
        )
        return dict(outcome.__dict__)

    @app.post("/api/generations/{run_id}/entries")
    def create_entry(
        run_id: str,
        payload: dict[str, Any],
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        outcome = add_entry(
            db,
            run_id,
            day=date.fromisoformat(payload["date"]),
            kind=payload["kind"],
            title_en=payload["title_en"],
            title_kn=payload.get("title_kn"),
            reason=payload.get("reason"),
        )
        return dict(outcome.__dict__)

    @app.delete("/api/generations/{run_id}/entries/{entry_id}")
    def delete_entry(
        run_id: str, entry_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        return dict(remove_entry(db, run_id, entry_id).__dict__)

    @app.post("/api/generations/{run_id}/entries/{entry_id}/move")
    def relocate_entry(
        run_id: str,
        entry_id: str,
        payload: dict[str, Any],
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        outcome = move_entry(
            db,
            run_id,
            entry_id,
            to_day=date.fromisoformat(payload["date"]),
            reason=payload.get("reason"),
        )
        return dict(outcome.__dict__)

    @app.post("/api/generations/{run_id}/visits/move")
    def relocate_visit(
        run_id: str,
        payload: dict[str, Any],
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        outcome = move_visit(
            db,
            run_id,
            asset_id=payload["asset_id"],
            from_day=date.fromisoformat(payload["from"]),
            to_day=date.fromisoformat(payload["to"]),
            reason=payload.get("reason"),
        )
        return dict(outcome.__dict__)

    # --- regeneration, comparison and acceptance -------------------------

    @app.post("/api/registers/{register_id}/candidates")
    def create_candidate(
        register_id: str,
        payload: dict[str, Any] | None = None,
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        body = payload or {}
        outcome = regenerate_candidate(
            db,
            current_of(db, register_id),
            seed=None if body.get("seed") is None else int(body["seed"]),
            reason=body.get("reason"),
        )
        return dict(outcome.__dict__)

    @app.get("/api/registers/{register_id}/candidates")
    def list_candidates(
        register_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> list[dict[str, Any]]:
        return awaiting_decision(db, register_id)

    @app.get("/api/revisions/{run_id}/compare/{other_run_id}")
    def compare(
        run_id: str, other_run_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        return compare_revisions(db, run_id, other_run_id)

    @app.post("/api/registers/{register_id}/candidates/{run_id}/accept")
    def accept(
        register_id: str,
        run_id: str,
        payload: dict[str, Any] | None = None,
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return accept_candidate(
            db, register_id, run_id, note=(payload or {}).get("note")
        )

    @app.post("/api/registers/{register_id}/candidates/{run_id}/reject")
    def reject(
        register_id: str,
        run_id: str,
        payload: dict[str, Any] | None = None,
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return reject_candidate(
            db, register_id, run_id, note=(payload or {}).get("note")
        )

    # --- registers, revisions, review and locks --------------------------

    @app.get("/api/registers")
    def list_registers(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, Any]]:
        return register_history(db)

    @app.get("/api/registers/{register_id}")
    def read_register(
        register_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        from ..application.revisions import register_summary

        return register_summary(db, register_id)

    @app.get("/api/registers/{register_id}/revisions")
    def read_register_revisions(
        register_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> list[dict[str, Any]]:
        from ..application.revisions import register_summary

        return register_summary(db, register_id)["revisions"]

    @app.get("/api/revisions/{run_id}")
    def read_revision(
        run_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        return revision_summary(db, run_id)

    @app.get("/api/generations/{run_id}/review")
    def read_review_state(
        run_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        return review_state(db, run_id)

    @app.put("/api/generations/{run_id}/review")
    def review_all_dates(
        run_id: str,
        payload: dict[str, Any] | None = None,
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        body = payload or {}
        expected = body.get("fingerprints")
        if not isinstance(expected, dict) or any(
            not isinstance(day, str) or not isinstance(value, str)
            for day, value in expected.items()
        ):
            raise RevisionError(
                C_STALE_REVIEW,
                "the date fingerprints are missing or malformed; "
                "reload the draft before reviewing all dates",
            )
        return mark_all_reviewed(
            db, run_id, expected_fingerprints=expected
        )

    @app.put("/api/generations/{run_id}/review/{day}")
    def review_date(
        run_id: str, day: date, payload: dict[str, Any] | None = None,
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        body = payload or {}
        return mark_reviewed(
            db,
            run_id,
            day,
            note=body.get("note"),
            expected_fingerprint=body.get("fingerprint"),
        )

    @app.delete("/api/generations/{run_id}/review/{day}")
    def withdraw_review(
        run_id: str, day: date, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        return clear_review(db, run_id, day)

    @app.put("/api/generations/{run_id}/locks/{day}")
    def lock_a_date(
        run_id: str, day: date, payload: dict[str, Any] | None = None,
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        body = payload or {}
        return lock_date(db, run_id, day, note=body.get("note"))

    @app.delete("/api/generations/{run_id}/locks/{day}")
    def unlock_a_date(
        run_id: str, day: date, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        return unlock_date(db, run_id, day)

    # --- finalising a month, and correcting a finalised one --------------

    @app.get("/api/registers/{register_id}/months")
    def read_month_lifecycle(
        register_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> list[dict[str, Any]]:
        return month_lifecycle(db, register_id)

    @app.get("/api/generations/{run_id}/finalisation")
    def read_finalisation_check(
        run_id: str,
        month: str,
        acknowledged: str | None = None,
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return check_finalisation(
            db, run_id, month, acknowledged=_tokens(acknowledged)
        ).as_dict()

    @app.post("/api/generations/{run_id}/finalisation")
    def close_month(
        run_id: str,
        payload: dict[str, Any],
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return finalise_month(
            db,
            run_id,
            payload["month"],
            acknowledged=tuple(payload.get("acknowledged") or ()),
            note=payload.get("note"),
        )

    @app.get("/api/registers/{register_id}/finalisations/{month}")
    def read_month_finalisations(
        register_id: str, month: str, db: sqlite3.Connection = Depends(get_db)
    ) -> list[dict[str, Any]]:
        return correction_chain(db, register_id, month)

    @app.get("/api/finalisations/{finalisation_id}")
    def read_finalisation(
        finalisation_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> dict[str, Any]:
        return finalisation_record(db, finalisation_id)

    @app.get("/finalisations/{finalisation_id}/print", response_class=HTMLResponse)
    def print_finalisation(
        finalisation_id: str, request: Request, db: sqlite3.Connection = Depends(get_db)
    ) -> HTMLResponse:
        issued = documents_source.document(db, finalisation_id)
        return templates.TemplateResponse(
            request, "print_register.html", {"document": issued}
        )

    @app.get("/api/finalisations/{finalisation_id}/export.xlsx")
    def export_finalisation(
        finalisation_id: str, db: sqlite3.Connection = Depends(get_db)
    ) -> Response:
        issued = documents_source.document(db, finalisation_id)
        body = documents.workbook_bytes(issued)
        return Response(
            content=body,
            media_type=documents.XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{documents.xlsx_filename(issued)}"'
                )
            },
        )

    # --- backup and restore -------------------------------------------------
    #
    # A backup is made from the live connection and handed over as one file. A restore is
    # two deliberate steps: validate a package into a staged database beside the live one,
    # then replace — with the barrier held, a recovery copy taken and the result verified
    # before it is called a success. No route decides whether a backup is trustworthy; the
    # application layer does, and this surface only translates.

    @app.post("/api/backups")
    def create_backup(db: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
        package = backup_restore.create_backup(db)
        held = desk.remember(package, f"BK-{uuid.uuid4().hex[:12]}")
        return held.as_payload()

    @app.get("/api/backups/{backup_id}/download")
    def download_backup(backup_id: str) -> Response:
        held = desk.backup(backup_id)
        if held is None:
            raise SurfaceError(
                "backup_not_held",
                "that backup is no longer being held; make a new one and download it",
            )
        return Response(
            content=held.package.content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{held.package.filename}"'
            },
        )

    @app.post("/api/backup-validation")
    async def validate_backup(file: UploadFile) -> dict[str, Any]:
        limits = Limits.from_environment()
        token = uuid.uuid4().hex[:12]
        workdir = Path(tempfile.mkdtemp(prefix="dailyworkreg-upload-"))
        try:
            upload = workdir / "package.zip"
            written = 0
            with upload.open("wb") as sink:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limits.max_package_bytes:
                        raise SurfaceError(
                            backup_restore.C_PACKAGE_TOO_LARGE,
                            "the file is larger than this tool accepts as a backup",
                        )
                    sink.write(chunk)
            if not written:
                raise SurfaceError(
                    backup_restore.C_PACKAGE_UNREADABLE, "no backup file was sent"
                )
            # A copy that is already validated is kept: a refused upload must not throw
            # away the restore the operator had just been shown and confirmed.
            waiting = desk.pending
            backup_restore.prune_staging(
                target_db,
                keep=set()
                if waiting is None
                else {waiting.staged.staged.name},
            )
            staged = backup_restore.validate_package(
                upload, target_db=target_db, token=token, limits=limits
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        desk.hold(
            PendingRestore(
                token=token,
                staged=staged,
                validated_at=datetime.now(UTC),
                upload_name=file.filename or "backup.zip",
            )
        )
        return desk.outstanding(token).as_payload()

    @app.post("/api/restore")
    def perform_restore(payload: dict[str, Any]) -> dict[str, Any]:
        token = str(payload.get("restore_token") or "")
        confirmation = str(payload.get("confirmation") or "").strip().upper()
        if confirmation != backup_restore.CONFIRMATION_WORD:
            raise SurfaceError(
                backup_restore.C_RESTORE_CONFIRMATION_REQUIRED,
                "restoring replaces every local record, including issued months. Type "
                f"{backup_restore.CONFIRMATION_WORD} to confirm, or close this and keep "
                "working.",
            )
        pending = desk.outstanding(token)
        if pending is None:
            raise SurfaceError(
                backup_restore.C_RESTORE_TOKEN_UNKNOWN,
                "that backup has not been validated in this session; validate it again "
                "before restoring",
            )
        with gate.exclusive():
            result = backup_restore.restore(pending.staged, target_db=target_db)
            desk.clear()
        return {
            "restored": True,
            "verified": result.verification,
            "rollback_copy": result.rollback.name,
            "migrations_applied": list(result.migrations_applied),
            "summary": {
                key: value
                for key, value in result.summary.items()
                if key in {"format", "format_version", "created_at", "schema_version"}
            },
        }

    @app.post("/api/finalisations/{finalisation_id}/corrections")
    def start_correction(
        finalisation_id: str,
        payload: dict[str, Any],
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        record = finalisation_record(db, finalisation_id)
        return create_correction_revision(
            db, record["run_id"], reason=payload.get("reason", "")
        )

    @app.delete("/api/registers/{register_id}/months/{month}/correction")
    def drop_correction(
        register_id: str,
        month: str,
        payload: dict[str, Any] | None = None,
        db: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        return abandon_correction(
            db, register_id, month, note=(payload or {}).get("note")
        )

    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    return app


def current_of(db: sqlite3.Connection, register_id: str) -> str:
    from ..application.revisions import current_run

    return current_run(db, register_id)


def _applied(db: sqlite3.Connection) -> tuple[int, ...]:
    from ..infrastructure.sqlite.schema import applied_versions

    return applied_versions(db)


def _spec(payload: dict[str, Any]) -> GenerationSpec:
    start = date.fromisoformat(payload["start"])
    end = date.fromisoformat(payload["end"])
    mode = DraftMode(payload.get("mode", "coverage_cycle"))
    cycle_start = payload.get("cycle_start_month")
    fixed: dict[MonthKey, tuple[Visit, ...]] = {}
    for label, visits in (payload.get("fixed_by_month") or {}).items():
        year, month = label.split("-", 1)
        fixed[MonthKey(int(year), int(month))] = tuple(
            Visit(item["asset_id"], date.fromisoformat(item["visit_date"]))
            for item in visits
        )
    history = tuple(
        Visit(item["asset_id"], date.fromisoformat(item["visit_date"]))
        for item in payload.get("prior_history", ())
    )
    return GenerationSpec(
        start=start,
        end=end,
        mode=mode,
        seed=int(payload.get("seed", 0)),
        attempt_budget=int(payload.get("attempt_budget", 400)),
        rotation_offset=int(payload.get("rotation_offset", 0)),
        cycle_start_month=(
            None if not cycle_start else _month(cycle_start)
        ),
        cycle_duration_months=int(payload.get("cycle_duration_months", 1)),
        cycle_visits_per_asset=int(payload.get("cycle_visits_per_asset", 1)),
        dtc_target=payload.get("dtc_target"),
        area_required=_counts(payload.get("area_required")),
        feeder_required=_counts(payload.get("feeder_required")),
        fixed_by_month=fixed,
        prior_history=history,
    )


def _month(label: str) -> MonthKey:
    year, month = label.split("-", 1)
    return MonthKey(int(year), int(month))


def _counts(payload: Any) -> dict[MonthKey, dict[str, int]]:
    if not payload:
        return {}
    return {
        _month(label): {key: int(value) for key, value in counts.items()}
        for label, counts in payload.items()
    }


def _type_name(asset_type: Any) -> str | None:
    """Render an asset type as its stored name, whether enum or already text.

    A record read back from the database and one built in memory are presented
    identically, so the client never has to know which it received.
    """

    if asset_type is None:
        return None
    return getattr(asset_type, "value", asset_type)


def _attribution(evidence: Any) -> dict[str, Any]:
    """Render an evidence or exception as the client displays it.

    The server sends the structured attribution it holds. The client never
    reconstructs an asset from the message, so every field an operator can see
    here came from the validator's own record of what failed.
    """

    return {
        "constraint": evidence.constraint,
        # Evidence carries `detail`; a raw Violation carries the same sentence as
        # `message`. Both are rendered into the field the client reads, so a
        # rejected correction is explained exactly like any other refusal.
        "detail": getattr(evidence, "detail", None) or evidence.message,
        "asset_id": evidence.asset_id,
        "asset_type": _type_name(evidence.asset_type),
        "visit_date": (
            None if evidence.visit_date is None else evidence.visit_date.isoformat()
        ),
        "offending_dates": [day.isoformat() for day in evidence.offending_dates],
        "contributing_assets": [
            {"asset_id": asset_id, "asset_type": _type_name(asset_type)}
            for asset_id, asset_type in getattr(evidence, "contributing_assets", ())
        ],
        "limit": evidence.limit,
        "actual": evidence.actual,
    }


def _batch(db: sqlite3.Connection, batch_id: str) -> dict[str, Any]:
    batch = staged.get_batch(db, batch_id)
    rows = staged.staging_rows(db, batch_id)
    return {
        "batch_id": batch.batch_id,
        "dataset": batch.dataset,
        "filename": batch.source_filename,
        "digest": batch.source_sha256,
        "status": batch.status,
        "row_count": batch.row_count,
        "error_count": batch.error_count,
        "warning_count": batch.warning_count,
        "staged_at": batch.staged_at,
        "accepted_at": batch.accepted_at,
        "decision_note": batch.decision_note,
        "can_accept": batch.status == "staged" and batch.error_count == 0,
        "rows": [
            {
                "row_number": row.row_number,
                "severity": row.severity,
                "decision": row.decision,
                "messages": list(row.messages),
                "raw": row.raw,
            }
            for row in rows
        ],
    }


def _dtc(row: masters.DtcRow) -> dict[str, Any]:
    return {
        "dtc_id": row.dtc_id,
        "name": row.name,
        "dtc_class": row.dtc_class,
        "source_class": row.source_class,
        "canonical_feeder_id": row.canonical_feeder_id,
        "kva": row.kva,
        "station": row.station,
        "individual_adjustment": row.individual_adjustment,
        "monthly_min": row.monthly_min,
        "monthly_max": row.monthly_max,
        "revisit_gap_days": row.revisit_gap_days,
        "active": row.active,
    }


def _area(row: masters.AreaRow) -> dict[str, Any]:
    return {
        "area_id": row.area_id,
        "name_kn": row.name_kn,
        "name_en": row.name_en,
        "weight": row.weight,
        "aliases": list(row.aliases),
        "monthly_min": row.monthly_min,
        "monthly_max": row.monthly_max,
        "revisit_gap_days": row.revisit_gap_days,
        "active": row.active,
    }


def _feeder(row: masters.FeederRow) -> dict[str, Any]:
    return {
        "feeder_id": row.feeder_id,
        "name": row.name,
        "manual_multiplier": row.manual_multiplier,
        "monthly_min": row.monthly_min,
        "monthly_max": row.monthly_max,
        "revisit_gap_days": row.revisit_gap_days,
        "active": row.active,
    }


def _activity(row: masters.ActivityRow) -> dict[str, Any]:
    return {
        "activity_id": row.activity_id,
        "category": row.category,
        "description_en": row.description_en,
        "description_kn": row.description_kn,
        "weight": row.weight,
        "monthly_min": row.monthly_min,
        "monthly_max": row.monthly_max,
        "repeat_gap_days": row.repeat_gap_days,
        "weekday_mask": row.weekday_mask,
        "active": row.active,
    }
