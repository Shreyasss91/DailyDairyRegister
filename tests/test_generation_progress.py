"""A generation says how far it has got, so a still page is not mistaken for a stuck one.

Ten months of register can be asked for in one click. Without a report, a request that is
working and a request that has wedged look the same on screen: a button that was pressed and
a page that has not changed. The progress bar exists to answer one question - is it moving -
so what it says has to be true, ordered and impossible to confuse for an outcome.

These tests hold the contract at the layers that own each part:

* the allocator and the cycle runner report, and the schedule they produce is **identical**
  whether or not anybody is listening - a run that allocates differently because it is being
  watched would be a register that changes when the operator looks;
* the registry turns those reports into one monotonic state, and a finished run keeps its
  result so a late poll still answers;
* the route starts a run and answers a poll, and a refusal reaches the page as a refusal,
  in the office's words;
* the client draws the bar from the server's own percentage and words.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from dailyworkreg.application import progress as progress_module
from dailyworkreg.application.draft_generation import (
    DraftMode,
    generate_draft,
)
from dailyworkreg.application.draft_requests import GenerationSpec, prepare_generation
from dailyworkreg.application.progress import (
    STAGE_ACTIVITIES,
    STAGE_ALLOCATING,
    STAGE_DONE,
    STAGE_FAILED,
    STAGE_READING,
    STAGE_SAVING,
    GenerationBusy,
    GenerationJobs,
    UnknownJob,
    month_label,
    percent_for,
)
from dailyworkreg.domain.allocator import AllocationRequest, allocate_month
from dailyworkreg.domain.cycle import MonthKey
from dailyworkreg.domain.targeting import run_cycle
from dailyworkreg.domain.validator import Calendar, DatePeriod, FeasibilityConfig
from dailyworkreg.infrastructure.sqlite import calendar_records as cal
from dailyworkreg.infrastructure.sqlite import masters
from dailyworkreg.infrastructure.sqlite.database import open_database
from master_fixtures import seed_office_masters

# --- a recorder -----------------------------------------------------------


@dataclass
class Recorder:
    """Keeps every report a generation makes, in the order it made them."""

    totals: list[int] = field(default_factory=list)
    months: list[tuple[MonthKey, int, int]] = field(default_factory=list)
    attempts: list[tuple[int, int]] = field(default_factory=list)
    stages: list[tuple[str, str]] = field(default_factory=list)

    def stage(self, name: str, detail: str) -> None:
        self.stages.append((name, detail))

    def months_total(self, total: int) -> None:
        self.totals.append(total)

    def month(self, month: MonthKey, index: int, total: int) -> None:
        self.months.append((month, index, total))

    def attempt(self, attempt: int, budget: int) -> None:
        self.attempts.append((attempt, budget))


# --- the office these tests generate for --------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_database(tmp_path / "progress.db")
    seed_office_masters(connection)
    for index in range(1, 13):
        masters.upsert_dtc(
            connection,
            dtc_id=f"SCD-{index}",
            name=f"Test DTC {index}",
            dtc_class="DEPT",
            kva=100.0 + index,
        )
    for index in range(1, 4):
        masters.upsert_area(
            connection,
            area_id=f"A-{index:02d}",
            name_kn=f"ಪ್ರದೇಶ {index}",
            monthly_min=0,
            monthly_max=4,
            revisit_gap_days=7,
        )
    for index in range(1, 5):
        masters.upsert_feeder(
            connection,
            feeder_id=f"F-{index:02d}",
            name=f"Feeder {index}",
            monthly_min=2,
            monthly_max=3,
            revisit_gap_days=5,
        )
    cal.upsert_holiday(
        connection,
        holiday_id="H-2026-11-10",
        holiday_date=date(2026, 11, 10),
        name="Recorded holiday",
        source="karnataka",
    )
    for index in range(1, 13):
        masters.upsert_activity(
            connection,
            activity_id=f"ACT-{index:03d}",
            category="System operation",
            description_en=f"Activity {index}",
            description_kn=f"ಚಟುವಟಿಕೆ {index}",
            weight=1.0,
            monthly_min=0,
            monthly_max=20,
            repeat_gap_days=1,
        )
    yield connection
    connection.close()


def spec(**overrides) -> GenerationSpec:
    values = {
        "start": date(2026, 11, 1),
        "end": date(2027, 1, 31),
        "mode": DraftMode.COVERAGE_CYCLE,
        "seed": 7,
        "cycle_start_month": MonthKey(2026, 11),
        "cycle_duration_months": 3,
        "cycle_visits_per_asset": 1,
    }
    values.update(overrides)
    return GenerationSpec(**values)


# --- the schedule does not depend on being watched ------------------------


def test_the_attempt_hook_is_reported_and_changes_nothing(db):
    """A month watched attempt by attempt is the same month as one nobody watched."""

    prepared = prepare_generation(db, spec())
    request = prepared.request
    calendar = _working_calendar(db)
    period = DatePeriod(date(2026, 11, 1), date(2026, 11, 30))

    def run(on_attempt):
        return allocate_month(
            AllocationRequest(
                period=period,
                calendar=calendar,
                roster=request.roster,
                config=request.config,
                seed=7,
                attempt_budget=8,
                on_attempt=on_attempt,
            )
        )

    heard: list[tuple[int, int]] = []
    watched = run(lambda attempt, budget: heard.append((attempt, budget)))
    silent = run(None)

    assert watched == silent
    assert heard, "the hook was never called"
    assert heard[0] == (1, 8), "the count starts at the first attempt"
    assert all(attempt <= budget for attempt, budget in heard)


def _working_calendar(db: sqlite3.Connection) -> Calendar:
    """The calendar the office's own stored facts produce for this range."""

    from dailyworkreg.application.calendar_projection import project_calendar

    projection = project_calendar(db, date(2026, 11, 1), date(2027, 1, 31))
    return Calendar(projection.working_days)


def test_a_cycle_reports_every_month_it_places_and_the_plan_is_identical(db):
    prepared = prepare_generation(db, spec())
    request = prepared.request
    calendar = _working_calendar(db)
    months = request.months()

    def run(**hooks):
        return run_cycle(
            roster=request.roster,
            calendar=calendar,
            config=request.config,
            cycle=request.cycle,
            seed=request.seed,
            weights=request.weights,
            fixed_by_month=dict(request.fixed_by_month),
            months_to_place=months,
            **hooks,
        )

    heard: list[tuple[MonthKey, int, int]] = []
    watched = run(on_month=lambda month, index, total: heard.append((month, index, total)))
    silent = run()

    assert watched.months == silent.months
    assert [month for month, _, _ in heard] == [plan.month for plan in watched.months]
    assert [index for _, index, _ in heard] == [1, 2, 3]
    assert {(total) for _, _, total in heard} == {3}


def test_the_generator_reports_the_months_and_the_activity_stage(db):
    """The whole run: every month announced, then the activities, in that order."""

    prepared = prepare_generation(db, spec(activities_per_day_min=2, activities_per_day_max=3))
    recorder = Recorder()

    watched = generate_draft(prepared.request, recorder)
    silent = generate_draft(prepared.request)

    assert watched.rows == silent.rows, "watching a run must not move a single date"
    assert watched.visits == silent.visits
    assert recorder.totals == [3]
    assert [month for month, _, _ in recorder.months] == [
        MonthKey(2026, 11),
        MonthKey(2026, 12),
        MonthKey(2027, 1),
    ]
    assert [name for name, _ in recorder.stages] == [STAGE_ACTIVITIES]
    assert "working days" in recorder.stages[0][1]


def test_a_run_without_activities_does_not_claim_to_be_placing_them(db):
    prepared = prepare_generation(db, spec(activities_per_day_min=0, activities_per_day_max=0))
    recorder = Recorder()
    generate_draft(prepared.request, recorder)
    assert recorder.stages == []


def test_a_silent_reporter_can_be_used_where_a_reporter_is_expected():
    """Every call site passes a reporter, so none of them has to test for ``None``."""

    silent = progress_module.SILENT
    silent.stage(STAGE_READING, "anything")
    silent.months_total(3)
    silent.month(MonthKey(2026, 11), 1, 3)
    silent.attempt(1, 400)


# --- the percentage ------------------------------------------------------


def test_the_bar_only_moves_forward_and_reaches_the_end():
    reading = percent_for(stage=STAGE_READING, months_done=0, months_total=3)
    first = percent_for(stage=STAGE_ALLOCATING, months_done=1, months_total=3)
    last = percent_for(stage=STAGE_ALLOCATING, months_done=3, months_total=3)
    activities = percent_for(stage=STAGE_ACTIVITIES, months_done=0, months_total=3)
    saving = percent_for(stage=STAGE_SAVING, months_done=0, months_total=3)
    done = percent_for(stage=STAGE_DONE, months_done=0, months_total=3)

    assert reading < first < last < activities <= saving < done
    assert done == 100
    assert percent_for(stage=STAGE_ALLOCATING, months_done=0, months_total=0) == 5


def test_a_month_is_named_the_way_an_operator_reads_it():
    assert month_label(MonthKey(2026, 3)) == "March 2026"


# --- the registry --------------------------------------------------------


def test_a_finished_run_keeps_its_result_for_a_late_poll():
    jobs = GenerationJobs()

    def work(progress) -> dict[str, object]:
        progress.months_total(1)
        progress.month(MonthKey(2026, 11), 1, 1)
        return {"run_id": "abc", "months": 1}

    job_id = jobs.begin(work)
    report = wait(jobs, job_id)

    assert report["stage"] == STAGE_DONE
    assert report["percent"] == 100
    assert report["finished"] is True
    assert report["failed"] is False
    assert report["result"] == {"run_id": "abc", "months": 1}
    assert jobs.running() is None


def test_a_refusal_becomes_a_failed_run_in_the_office_s_words():
    jobs = GenerationJobs()

    class Refusal(Exception):
        constraint = "activity_allocation_infeasible"
        detail = "between 3 and 5 activities a day cannot be placed"

    def work(progress) -> dict[str, object]:
        raise Refusal()

    job_id = jobs.begin(work)
    report = wait(jobs, job_id)

    assert report["stage"] == STAGE_FAILED
    assert report["failed"] is True
    assert report["finished"] is True
    assert report["error"] == {
        "constraint": "activity_allocation_infeasible",
        "detail": "between 3 and 5 activities a day cannot be placed",
    }
    assert "result" not in report
    assert jobs.running() is None


def test_an_unexpected_failure_says_something_rather_than_nothing():
    jobs = GenerationJobs()

    def work(progress) -> dict[str, object]:
        raise ValueError("")

    report = wait(jobs, jobs.begin(work))

    assert report["failed"] is True
    assert report["error"]["constraint"] == "request_refused"
    assert report["error"]["detail"]


def test_one_generation_runs_at_a_time():
    """A second run would compete for the same database and the same bar."""

    jobs = GenerationJobs()
    holding = threading.Event()
    entered = threading.Event()

    def work(progress) -> dict[str, object]:
        entered.set()
        holding.wait(5)
        return {"run_id": "first"}

    first = jobs.begin(work)
    assert entered.wait(5), "the run never started"

    with pytest.raises(GenerationBusy) as caught:
        jobs.begin(lambda progress: {"run_id": "second"})
    assert caught.value.job_id == first
    assert "already running" in caught.value.detail

    holding.set()
    wait(jobs, first)
    holding.clear()

    second = jobs.begin(lambda progress: {"run_id": "third"})
    assert wait(jobs, second)["result"] == {"run_id": "third"}


def test_the_bar_never_goes_backwards_even_when_a_stage_is_reported_again():
    jobs = GenerationJobs()
    holding = threading.Event()
    entered = threading.Event()

    def work(progress) -> dict[str, object]:
        progress.months_total(3)
        progress.month(MonthKey(2026, 12), 2, 3)
        entered.set()
        holding.wait(5)
        # A stale report from an earlier stage: it must not undo what was already shown.
        progress.stage(STAGE_READING, "reading again")
        return {}

    job_id = jobs.begin(work)
    assert entered.wait(5)
    before = jobs.report(job_id)["percent"]
    holding.set()
    after = wait(jobs, job_id)["percent"]
    assert after >= before


def test_a_report_arriving_after_the_run_finished_is_ignored():
    jobs = GenerationJobs()
    keep = {}

    def work(progress) -> dict[str, object]:
        keep["progress"] = progress
        return {"run_id": "done"}

    report = wait(jobs, jobs.begin(work))
    keep["progress"].stage(STAGE_ALLOCATING, "too late")
    keep["progress"].month(MonthKey(2026, 12), 1, 1)

    assert jobs.report(report["job_id"])["stage"] == STAGE_DONE


def test_a_poll_for_a_run_this_session_never_saw_is_refused():
    jobs = GenerationJobs()
    with pytest.raises(UnknownJob) as caught:
        jobs.report("nope")
    assert caught.value.constraint == "generation_job_unknown"


def test_the_registry_forgets_the_oldest_finished_runs_and_keeps_the_work():
    jobs = GenerationJobs(keep=2)
    first = jobs.begin(lambda progress: {"run_id": "first"})
    wait(jobs, first)
    second = jobs.begin(lambda progress: {"run_id": "second"})
    wait(jobs, second)

    # The third run makes nine tenths of the history drop, and the one that drops is the
    # oldest - a run the page has long stopped asking about.
    third = jobs.begin(lambda progress: {"run_id": "third"})
    wait(jobs, third)

    with pytest.raises(UnknownJob):
        jobs.report(first)
    assert jobs.report(second)["finished"] is True
    assert jobs.report(third)["result"] == {"run_id": "third"}


def wait(jobs: GenerationJobs, job_id: str, *, timeout: float = 20.0) -> dict:
    """Poll until the run reports itself finished. Bounded, so a stuck run fails a test."""

    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        report = jobs.report(job_id)
        if report["finished"]:
            return report
        time.sleep(0.01)
    raise AssertionError(f"the run {job_id} did not finish within {timeout}s")


# --- through the HTTP surface -------------------------------------------


def surface(tmp_path: Path):
    from dailyworkreg.interfaces.api import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app(tmp_path / "surface.db"))
    for index in range(1, 13):
        client.put(
            "/api/masters/dtc",
            json={
                "dtc_id": f"SCD-{index}",
                "name": f"Test DTC {index}",
                "dtc_class": "DEPT",
                "kva": 100.0 + index,
            },
        )
    for index in range(1, 4):
        client.put(
            "/api/masters/area",
            json={
                "area_id": f"A-{index:02d}",
                "name_kn": f"ಪ್ರದೇಶ {index}",
                "monthly_min": 0,
                "monthly_max": 4,
                "revisit_gap_days": 7,
            },
        )
    for index in range(1, 5):
        client.put(
            "/api/masters/feeder",
            json={
                "feeder_id": f"F-{index:02d}",
                "name": f"Feeder {index}",
                "monthly_min": 2,
                "monthly_max": 3,
                "revisit_gap_days": 5,
            },
        )
    for index in range(1, 13):
        client.put(
            "/api/masters/activity",
            json={
                "activity_id": f"ACT-{index:03d}",
                "category": "System operation",
                "description_en": f"Activity {index}",
                "description_kn": f"ಚಟುವಟಿಕೆ {index}",
                "weight": 1.0,
                "monthly_min": 0,
                "monthly_max": 20,
                "repeat_gap_days": 1,
            },
        )
    return client


def body(**extra) -> dict:
    payload = {
        "start": "2026-11-01",
        "end": "2027-01-31",
        "mode": "coverage_cycle",
        "seed": 7,
        "cycle_start_month": "2026-11",
        "cycle_duration_months": 3,
        "cycle_visits_per_asset": 1,
    }
    payload.update(extra)
    return payload


def poll(client, job_id: str, *, rounds: int = 400) -> dict:
    """Follow a run to its end through the route, as the page does."""

    import time

    for _ in range(rounds):
        answer = client.get(f"/api/generations/jobs/{job_id}")
        assert answer.status_code == 200, answer.text
        report = answer.json()
        if report["finished"]:
            return report
        time.sleep(0.01)
    raise AssertionError("the route never reported the run as finished")


def test_the_route_starts_a_run_and_following_it_gives_the_same_answer(tmp_path: Path):
    client = surface(tmp_path)
    direct = client.post("/api/generations", json=body())
    assert direct.status_code == 200, direct.text

    started = client.post("/api/generations/jobs", json=body())
    assert started.status_code == 202, started.text
    first = started.json()
    assert first["stage"] == STAGE_READING
    assert first["percent"] < 10, "a run that has not started placing is not far along"
    assert first["finished"] is False

    report = poll(client, first["job_id"])
    assert report["failed"] is False
    assert report["percent"] == 100
    assert report["elapsed_seconds"] >= 0
    assert sorted(report["result"]) == sorted(direct.json()), (
        "the watched run must report exactly what the direct route reports"
    )
    # The run is stored like any other, so the page can open it afterwards.
    stored = client.get(f"/api/generations/{report['result']['run_id']}")
    assert stored.status_code == 200, stored.text


def test_a_refused_run_reaches_the_page_as_a_refusal(tmp_path: Path):
    """A month the catalogue cannot fill must not look like a run that is still going."""

    client = surface(tmp_path)
    refused = client.post(
        "/api/generations/jobs",
        json=body(activities_per_day_min=13, activities_per_day_max=13),
    )
    assert refused.status_code == 202, refused.text

    report = poll(client, refused.json()["job_id"])
    assert report["failed"] is True
    assert report["finished"] is True
    assert report["stage"] == STAGE_FAILED
    assert report["error"]["constraint"]
    # The office reads a sentence, not an exception class.
    assert "activities a day cannot be placed" in report["error"]["detail"]


def test_a_poll_for_a_run_that_is_not_here_is_refused_in_words(tmp_path: Path):
    client = surface(tmp_path)
    answer = client.get("/api/generations/jobs/does-not-exist")
    assert answer.status_code == 404
    error = answer.json()["error"]
    assert error["constraint"] == "generation_job_unknown"
    assert "ask for a new generation" in error["detail"]


def test_a_second_run_is_refused_while_one_is_going(tmp_path: Path, monkeypatch):
    """The refusal names the run in progress, so the page can watch that one instead."""

    client = surface(tmp_path)
    entered = threading.Event()
    holding = threading.Event()
    original = progress_module.GenerationJobs._run

    def run(self, job, work, sink):  # noqa: ANN001 - mirrors the registry's own method
        entered.set()
        holding.wait(10)
        return original(self, job, work, sink)

    monkeypatch.setattr(progress_module.GenerationJobs, "_run", run)

    first = client.post("/api/generations/jobs", json=body())
    assert first.status_code == 202, first.text
    assert entered.wait(10), "the run never started"

    second = client.post("/api/generations/jobs", json=body())
    assert second.status_code == 409, second.text
    error = second.json()["error"]
    assert error["constraint"] == "generation_already_running"
    assert error["job_id"] == first.json()["job_id"]
    assert "already running" in error["detail"]

    holding.set()


# --- the page ------------------------------------------------------------


APP_JS = (Path(__file__).resolve().parents[1] / "dailyworkreg/interfaces/static/app.js").read_text(
    encoding="utf-8"
)
INDEX = (
    Path(__file__).resolve().parents[1] / "dailyworkreg/interfaces/templates/index.html"
).read_text(encoding="utf-8")


def test_the_form_shows_a_bar_with_the_words_the_server_gave_it():
    assert 'id="gen-progress"' in INDEX
    assert 'id="gen-progress-fill"' in INDEX
    assert 'id="gen-progress-text"' in INDEX
    assert 'role="status"' in INDEX, "a screen reader is told when the run moves on"
    assert "gen-progress" in APP_JS


def test_the_button_is_posted_to_the_watched_route_and_polled_to_the_end():
    assert "/api/generations/jobs" in APP_JS
    assert "/api/generations/jobs/${jobId}" in APP_JS
    assert "followGeneration" in APP_JS
    # The page draws the server's percentage and its sentence; it computes neither.
    assert "report.percent" in APP_JS
    assert "report.detail" in APP_JS
    assert "elapsed_seconds" in APP_JS


def test_the_button_cannot_be_pressed_twice_while_a_run_is_going():
    assert "button.disabled = true" in APP_JS
    assert "button.disabled = false" in APP_JS
    assert "Generating" in APP_JS


def test_the_page_is_not_told_to_compute_its_own_progress():
    """Only the server knows how many months there are, so the client must not guess."""

    assert "100 /" not in APP_JS
    assert "% 100" not in APP_JS


def test_the_client_is_loaded_from_a_new_cache_key():
    # A changed client must not be served from a browser's cached copy. The value
    # moves whenever app.js does, which is why it is compared against the current
    # one rather than against the key it had when the progress bar was written.
    keys = re.findall(r"app\.js\?v=([\w-]+)", INDEX)
    assert keys == ["reset-1"], keys
