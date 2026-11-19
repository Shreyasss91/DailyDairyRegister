"""How a long generation says how far it has got.

A generation can be asked to fill ten months of a register, and the operator watches it
from a page with nothing on it: a request that takes twenty seconds and one that is stuck
look identical. This module gives that work somewhere to write its progress, so the page can
show a bar that moves and a sentence naming what is happening.

Two pieces, deliberately separate:

* :class:`ProgressReporter` is the vocabulary the generation uses, with no knowledge of HTTP
  or of the page. ``generate_draft`` and the allocator call it; :class:`SilentReporter` is
  what they are handed when nobody is watching, so no call site ever tests for ``None``.
* :class:`GenerationJobs` keeps one record per requested run, in memory, guarded by a lock,
  and runs the work on its own thread. It answers a poll with a snapshot, so the page never
  has to reason about ordering: it reads a whole state, not a deltas stream it might miss.

Nothing here reads a database, formats a date for a human or knows what a schedule is; the
work itself arrives as a callable and is executed, not understood.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from ..domain.cycle import MonthKey

# --- what a generation reports ---------------------------------------------

#: The masters and the calendar are being read. Nothing is scheduled yet.
STAGE_READING = "reading_masters"
#: Field visits are being placed, one month at a time.
STAGE_ALLOCATING = "allocating"
#: The approved activities are being placed on the working days of the range.
STAGE_ACTIVITIES = "placing_activities"
#: The draft is being written to the register.
STAGE_SAVING = "saving"
#: The run finished and its result is stored.
STAGE_DONE = "done"
#: The run was refused, or failed. ``error`` says why, in the office's words.
STAGE_FAILED = "failed"

#: Where the bar sits at each stage, and how much of it a month of allocation covers. The
#: numbers are a presentation of progress, not a measurement of it: allocation is the long
#: part because it is the part that can take a minute, and reading the masters is short
#: because it is a handful of queries.
_BASE_READING = 2
_BASE_ALLOCATING = 5
_ALLOCATING_SPAN = 85
_BASE_ACTIVITIES = 92
_BASE_SAVING = 97
_DONE = 100


class ProgressReporter(Protocol):
    """Where a generation writes what it is doing. Implementations never raise."""

    def stage(self, name: str, detail: str) -> None:
        """Record that a stage began, with a sentence for the operator."""

    def months_total(self, total: int) -> None:
        """Record how many months this run will place visits in."""

    def month(self, month: MonthKey, index: int, total: int) -> None:
        """Record that month ``index`` of ``total`` (1-based) is being placed now."""

    def attempt(self, attempt: int, budget: int) -> None:
        """Record how many constructive attempts the current month has used."""


class SilentReporter:
    """The reporter used when nobody is watching. Every call does nothing."""

    def stage(self, name: str, detail: str) -> None:  # noqa: D102 - see the protocol
        return

    def months_total(self, total: int) -> None:  # noqa: D102 - see the protocol
        return

    def month(self, month: MonthKey, index: int, total: int) -> None:  # noqa: D102
        return

    def attempt(self, attempt: int, budget: int) -> None:  # noqa: D102 - see the protocol
        return


#: What a generation is handed when a caller passes nothing.
SILENT: ProgressReporter = SilentReporter()


#: Month names as an operator reads them. ``MonthKey`` deliberately carries no
#: presentation, so the label is built here rather than beside it.
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def month_label(month: MonthKey) -> str:
    """``March 2026`` - the month as an operator reads it, not as a key sorts."""

    name = _MONTH_NAMES[month.month - 1] if 1 <= month.month <= 12 else "month"
    return f"{name} {month.year}"


def percent_for(*, stage: str, months_done: int, months_total: int) -> int:
    """The share of the bar a stage has filled.

    Kept in one function so two places cannot disagree about what 60% means, and so a bar
    that jumps backwards is a failing test rather than a thing the operator notices first.
    """

    if stage == STAGE_DONE:
        return _DONE
    if stage == STAGE_SAVING:
        return _BASE_SAVING
    if stage == STAGE_ACTIVITIES:
        return _BASE_ACTIVITIES
    if stage == STAGE_ALLOCATING:
        if months_total <= 0:
            return _BASE_ALLOCATING
        done = max(0, min(months_done, months_total))
        return _BASE_ALLOCATING + (_ALLOCATING_SPAN * done) // months_total
    if stage == STAGE_READING:
        return _BASE_READING
    return 0


# --- the record of one requested run ---------------------------------------


@dataclass
class _Job:
    """One requested run. Every field is guarded by the registry's lock."""

    job_id: str
    started_at: float
    started_wall: datetime
    stage: str = STAGE_READING
    detail: str = ""
    months_done: int = 0
    months_total: int = 0
    attempt: int = 0
    attempt_budget: int = 0
    percent: int = 0
    finished: bool = False
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    finished_at: float | None = None
    #: Highest percentage reached, so the bar cannot slide backwards on a re-report.
    high_water: int = 0


class GenerationBusy(RuntimeError):
    """A generation is already running. One at a time is the whole point of the bar."""

    constraint = "generation_already_running"

    def __init__(self, job_id: str, detail: str):
        self.job_id = job_id
        self.detail = detail
        super().__init__(detail)


class UnknownJob(LookupError):
    """The polled run is not one this process is running or has run."""

    constraint = "generation_job_unknown"

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.detail = (
            f"no generation run {job_id!r} is running or was run by this session; "
            "ask for a new generation"
        )
        super().__init__(self.detail)


def _snapshot(job: _Job) -> dict[str, Any]:
    """The whole state of a run, as one readable object."""

    now = job.finished_at if job.finished_at is not None else time.monotonic()
    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "stage": job.stage,
        "detail": job.detail,
        "percent": job.percent,
        "months_done": job.months_done,
        "months_total": job.months_total,
        "attempt": job.attempt,
        "attempt_budget": job.attempt_budget,
        "finished": job.finished,
        "failed": job.error is not None,
        "elapsed_seconds": round(max(0.0, now - job.started_at), 1),
        "started_at": job.started_wall.isoformat(),
    }
    if job.result is not None:
        payload["result"] = job.result
    if job.error is not None:
        payload["error"] = job.error
    return payload


class _Sink:
    """The reporter a worker writes through, translating into the shared record."""

    def __init__(self, registry: GenerationJobs, job: _Job):
        self._registry = registry
        self._job = job

    def stage(self, name: str, detail: str) -> None:
        self._registry._update(  # noqa: SLF001 - the registry owns the lock
            self._job, stage=name, detail=detail
        )

    def months_total(self, total: int) -> None:
        self._registry._update(self._job, months_total=total)  # noqa: SLF001

    def month(self, month: MonthKey, index: int, total: int) -> None:
        self._registry._update(  # noqa: SLF001
            self._job,
            stage=STAGE_ALLOCATING,
            months_done=max(0, index - 1),
            months_total=total,
            attempt=0,
            detail=(f"Placing field visits for {month_label(month)} ({index} of {max(1, total)})."),
        )

    def attempt(self, attempt: int, budget: int) -> None:
        self._registry._update(  # noqa: SLF001
            self._job, attempt=attempt, attempt_budget=budget
        )


class GenerationJobs:
    """The runs this process is executing, one at a time, with their progress.

    The record lives in memory because the page that asked for the run is the only reader,
    and because a restart must not resurrect a half-written register as though it were
    running. A finished run keeps its result here so a poll that arrives after completion
    still answers, and the oldest records are dropped once ``keep`` have accumulated.
    """

    def __init__(self, *, keep: int = 20) -> None:
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, _Job] = OrderedDict()
        self._running: str | None = None
        self._keep = max(1, keep)

    # --- reading ---------------------------------------------------------

    def report(self, job_id: str) -> dict[str, Any]:
        """The current state of one run."""

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise UnknownJob(job_id)
            return _snapshot(job)

    def running(self) -> str | None:
        """The id of the run in progress, or ``None`` when the desk is idle."""

        with self._lock:
            return self._running

    # --- running ---------------------------------------------------------

    def begin(self, work: Callable[[ProgressReporter], dict[str, Any]]) -> str:
        """Start ``work`` on its own thread and return the id to poll.

        Args:
            work: What to run. It receives the reporter and returns the result payload the
                page shows once the run finishes. Raising refuses the run, and the error's
                own ``constraint`` and ``detail`` are kept for the page.

        Returns:
            The job id.

        Raises:
            GenerationBusy: A run is already in progress. A second generation would compete
                for the same database and the same masters, and its progress would be
                indistinguishable from the first one's.
        """

        with self._lock:
            if self._running is not None:
                raise GenerationBusy(
                    self._running,
                    "a generation is already running; wait for it to finish, or watch its "
                    "progress bar, before starting another",
                )
            job_id = uuid.uuid4().hex
            job = _Job(
                job_id=job_id,
                started_at=time.monotonic(),
                started_wall=datetime.now(UTC),
                detail="Starting: reading the stored masters and the calendar.",
                percent=percent_for(stage=STAGE_READING, months_done=0, months_total=0),
            )
            job.high_water = job.percent
            self._jobs[job_id] = job
            self._running = job_id
            self._trim()

        sink = _Sink(self, job)
        worker = threading.Thread(
            target=self._run, args=(job, work, sink), name=f"generation-{job_id[:8]}"
        )
        worker.daemon = True
        worker.start()
        return job_id

    def _run(
        self,
        job: _Job,
        work: Callable[[ProgressReporter], dict[str, Any]],
        sink: _Sink,
    ) -> None:
        """Execute the work, and record either its result or its refusal."""

        try:
            result = work(sink)
        except Exception as error:  # noqa: BLE001 - any failure must reach the page
            # The constraint and the sentence are what the page renders; the traceback
            # belongs in the log, not in a message a trader reads.
            with self._lock:
                job.error = {
                    "constraint": str(getattr(error, "constraint", "request_refused")),
                    "detail": str(
                        getattr(error, "detail", None) or str(error).strip() or type(error).__name__
                    ),
                }
                job.stage = STAGE_FAILED
                job.detail = job.error["detail"]
                job.finished = True
                job.finished_at = time.monotonic()
                if self._running == job.job_id:
                    self._running = None
            return

        with self._lock:
            job.result = dict(result or {})
            job.stage = STAGE_DONE
            job.detail = "Finished: the draft is written and ready to read."
            job.months_done = job.months_total
            job.percent = _DONE
            job.high_water = _DONE
            job.finished = True
            job.finished_at = time.monotonic()
            if self._running == job.job_id:
                self._running = None

    # --- internals -------------------------------------------------------

    def _update(self, job: _Job, **changes: Any) -> None:
        """Apply one reported change, never letting the bar move backwards."""

        with self._lock:
            if job.finished:
                # A late report from a thread that already finished is stale by
                # definition, and applying it would reopen a closed run.
                return
            for name, value in changes.items():
                setattr(job, name, value)
            percent = percent_for(
                stage=job.stage,
                months_done=job.months_done,
                months_total=job.months_total or 0,
            )
            if percent > job.high_water:
                job.high_water = percent
            job.percent = job.high_water

    def _trim(self) -> None:
        """Drop the oldest finished runs, keeping the ones still worth a poll."""

        if len(self._jobs) <= self._keep:
            return
        for job_id in list(self._jobs):
            if len(self._jobs) <= self._keep:
                break
            job = self._jobs[job_id]
            if job.job_id == self._running:
                continue
            self._jobs.pop(job_id, None)


#: What the page is told when a poll arrives before the run has reported anything.
EMPTY_DETAIL = "Starting: reading the stored masters and the calendar."


@dataclass(frozen=True)
class JobSummary:
    """A finished run, as the history list shows it. Kept for readability in tests."""

    job_id: str
    stage: str
    percent: int
    finished: bool
    detail: str = field(default="")


def summarise(report: Mapping[str, Any]) -> JobSummary:
    """Read a poll response into the few fields a caller usually checks."""

    return JobSummary(
        job_id=str(report.get("job_id", "")),
        stage=str(report.get("stage", "")),
        percent=int(report.get("percent", 0) or 0),
        finished=bool(report.get("finished")),
        detail=str(report.get("detail", "")),
    )
