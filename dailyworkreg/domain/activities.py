"""Placing regular work activities on working days.

A register is not only field visits. The office's approved catalogue describes the
regular work an Assistant Engineer records: reviewing feeders, attending complaints,
meter testing, safety drills. This module decides which of those activities lands on
which working day. It performs no I/O and reads no clock, so the same catalogue, the
same days and the same seed always produce the same placements.

**What is hard and what is a preference.** The requirements are explicit that a
weight influences relative allocation and never overrides a minimum, a maximum, a
repeat interval or a daily cap. So:

*   the count per working day is a range with two hard ends: ``per_day_min`` is a
    floor no working day may fall below, ``per_day_max`` is a ceiling no working day
    may exceed. Between them the day's count is drawn from the seed, so a register
    is not a flat wall of threes. The office asked for "a minimum of three
    activities a day", and a minimum is what this is: three is the floor of the
    range, not the ceiling of it;
*   ``monthly_max`` per activity per calendar month is hard;
*   ``monthly_min`` per activity per calendar month is hard - a floor that must be
    met even on a month whose days are already full;
*   ``repeat_gap_days`` is hard: two placements of one activity are at least that
    many days apart. ``0`` and ``1`` mean no restriction beyond one placement a day;
*   ``weekday_mask`` is hard: an activity restricted to Wednesdays is never placed
    on a Monday;
*   ``weight`` only decides which of the *permitted* activities is chosen.

When those cannot all hold, this module raises :class:`ActivityInfeasible` naming
the constraint and the day or month responsible. It never quietly places fewer
activities, never widens a maximum and never shortens a gap: a register that is
quietly wrong is worse than one that refuses and says why.

**Determinism.** The random draw is seeded from the caller's generation seed and the
month, and candidate order is by activity id, so two runs of the same request are
identical and a different seed gives a different - equally valid - draft. The count
each day carries is drawn from its own stream, so editing the catalogue changes which
activities a day holds without also changing how many it holds.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from .cycle import MonthKey

C_ACTIVITY_NONE_ALLOWED = "activity_placement_has_no_candidate"
C_ACTIVITY_DAY_SHORT = "activity_placement_cannot_fill_a_day"
C_ACTIVITY_MONTH_MIN_UNSATISFIABLE = "activity_monthly_minimum_unsatisfiable"
C_ACTIVITY_MONTH_MAX_TOO_SMALL = "activity_monthly_maximum_below_minimum"
C_ACTIVITY_MASK_UNREADABLE = "activity_weekday_mask_unreadable"
C_ACTIVITY_LIMITS_INCONSISTENT = "activity_limits_inconsistent"
C_ACTIVITY_DAILY_RANGE_INVALID = "activity_daily_range_invalid"

#: Mask tokens accepted for ``weekday_mask``, in Python's Monday-first numbering.
_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: ``per_day_min``/``per_day_max`` bound the count of *distinct* activities a day
#: carries. Two placements of one activity on one day would be a double record of one
#: act, not two activities.
MINIMUM_GAP = 1


class ActivityInfeasible(ValueError):
    """The catalogue cannot meet the hard constraints of one month or one day."""

    def __init__(
        self,
        constraint: str,
        detail: str,
        *,
        month: MonthKey | None = None,
        day: date | None = None,
        activity_id: str | None = None,
    ):
        self.constraint = constraint
        self.detail = detail
        self.month = month
        self.day = day
        self.activity_id = activity_id
        super().__init__(f"{constraint}: {detail}")


@dataclass(frozen=True)
class ActivitySpec:
    """One approved activity, as the office recorded it.

    The fields are the catalogue's own columns, carried unchanged. Nothing here is
    defaulted from a guess: an activity with no recorded weight gets the neutral
    weight of 1.0, which is the catalogue's own coding for "no preference".
    """

    activity_id: str
    category: str
    title_en: str
    title_kn: str | None = None
    weight: float = 1.0
    monthly_min: int = 0
    monthly_max: int = 10
    repeat_gap_days: int = 2
    weekday_mask: str | None = None

    def allowed_on(self, weekday: int) -> bool:
        """Whether the activity may be recorded on this weekday (0 = Monday)."""

        mask = parse_weekday_mask(self.weekday_mask, self.activity_id)
        return mask is None or weekday in mask


@dataclass(frozen=True)
class ActivityPlacement:
    """One activity on one day, carrying the activity it came from."""

    day: date
    activity: ActivitySpec


def parse_weekday_mask(mask: str | None, activity_id: str = "") -> frozenset[int] | None:
    """Read a ``weekday_mask`` into weekday numbers, or ``None`` for "any day".

    Accepted forms are the catalogue's own: an empty value or ``None`` means no
    restriction, and entries may be weekday names (``Mon``) or numbers (``0`` for
    Monday, ``6`` for Sunday), separated by commas, semicolons or spaces. An
    unreadable token is refused rather than ignored, because ignoring it would place
    the activity on a day the office excluded.
    """

    if mask is None:
        return None
    text = mask.strip()
    if not text:
        return None

    days: set[int] = set()
    for token in text.replace(";", ",").replace(" ", ",").split(","):
        token = token.strip()
        if not token:
            continue
        lowered = token.lower()
        if lowered in _WEEKDAY_NAMES:
            days.add(_WEEKDAY_NAMES.index(lowered))
            continue
        if lowered.isdigit() and 0 <= int(lowered) <= 6:
            days.add(int(lowered))
            continue
        raise ActivityInfeasible(
            C_ACTIVITY_MASK_UNREADABLE,
            (
                f"activity {activity_id or '(unnamed)'} restricts its weekdays to "
                f"{token!r}, which is not a weekday name (Mon..Sun) or a number "
                "0..6; correct the catalogue before generating"
            ),
            activity_id=activity_id or None,
        )
    return frozenset(days) if days else None


def allocate_activities(
    *,
    days: Sequence[date],
    activities: Sequence[ActivitySpec],
    per_day_min: int,
    per_day_max: int,
    seed: int = 0,
) -> tuple[ActivityPlacement, ...]:
    """Place between ``per_day_min`` and ``per_day_max`` activities on every day.

    ``days`` is the ordered list of days an activity may be recorded on - working
    days only, as the caller decided. The result is chronological, and within a day
    is ordered by the catalogue's activity id so two renderings of one day agree.

    ``per_day_min`` is a floor and ``per_day_max`` a ceiling, both hard. Each day's
    own count is drawn between them, so the register varies day to day; a month whose
    catalogue still owes placements may borrow a day's headroom up to the ceiling,
    never past it. ``per_day_min == per_day_max`` is the flat count an earlier build
    could ask for, and behaves exactly as it did.

    Raises:
        ActivityInfeasible: when the hard constraints cannot all hold. The message
            names the constraint, the month and - where it applies - the day.
    """

    if per_day_min < 0 or per_day_max < per_day_min:
        raise ActivityInfeasible(
            C_ACTIVITY_DAILY_RANGE_INVALID,
            (
                f"the daily activity range {per_day_min}..{per_day_max} cannot be "
                "read: a count is never negative and its minimum is never above "
                "its maximum"
            ),
        )
    if per_day_max <= 0:
        return ()
    if not days:
        return ()

    active = sorted(
        (item for item in activities if item.monthly_max > 0), key=lambda a: a.activity_id
    )
    if not active:
        raise ActivityInfeasible(
            C_ACTIVITY_NONE_ALLOWED,
            (
                f"at least {per_day_min} activities a day are required, but no active "
                "activity is stored; import the approved activity catalogue or set "
                "the daily count to zero"
            ),
        )
    seen: set[str] = set()
    for item in active:
        if item.activity_id in seen:
            raise ActivityInfeasible(
                C_ACTIVITY_LIMITS_INCONSISTENT,
                (
                    f"activity {item.activity_id} appears twice in the catalogue; one "
                    "activity has one set of limits, and two would let each escape the "
                    "other's maximum"
                ),
                activity_id=item.activity_id,
            )
        seen.add(item.activity_id)

    if per_day_min > len(active):
        raise ActivityInfeasible(
            C_ACTIVITY_NONE_ALLOWED,
            (
                f"at least {per_day_min} different activities must be recorded on "
                f"every working day, but the catalogue offers only {len(active)} "
                f"active activit{'y' if len(active) == 1 else 'ies'} that may be "
                "recorded; add activities or lower the daily minimum"
            ),
        )
    for item in active:
        _check_limits(item)

    placements: list[ActivityPlacement] = []
    by_month: dict[MonthKey, list[date]] = {}
    last_day: dict[str, date | None] = {item.activity_id: None for item in active}
    for day in sorted(set(days)):
        by_month.setdefault(MonthKey(day.year, day.month), []).append(day)

    for month in sorted(by_month, key=lambda key: key.ordinal()):
        placements.extend(
            _allocate_month(
                month=month,
                month_days=by_month[month],
                activities=active,
                per_day_min=per_day_min,
                per_day_max=per_day_max,
                seed=seed,
                # Gaps are calendar facts, not month facts: an activity recorded on
                # the last day of one month is still constraining the first day of
                # the next, so the last placement is carried across the boundary.
                last_day=last_day,
            )
        )

    return tuple(placements)


def _check_limits(item: ActivitySpec) -> None:
    if item.monthly_min > item.monthly_max:
        raise ActivityInfeasible(
            C_ACTIVITY_MONTH_MAX_TOO_SMALL,
            (
                f"activity {item.activity_id} has a monthly minimum of "
                f"{item.monthly_min} above its monthly maximum of {item.monthly_max}"
            ),
            activity_id=item.activity_id,
        )


def _allocate_month(
    *,
    month: MonthKey,
    month_days: Sequence[date],
    activities: Sequence[ActivitySpec],
    per_day_min: int,
    per_day_max: int,
    seed: int,
    last_day: dict[str, date | None],
) -> list[ActivityPlacement]:
    """Fill one month: obligations first, then weighted choice among the rest."""

    rng = random.Random(f"{seed}:{month.label()}:{per_day_min}..{per_day_max}")
    # The count a day carries is drawn from its own stream. Sharing the choice stream
    # would make the shape of the month depend on which activities were drawn, so
    # editing the catalogue's weights would move the days as well as the content.
    count_rng = random.Random(f"{seed}:{month.label()}:count:{per_day_min}..{per_day_max}")

    #: The count each day was drawn to carry. The ceiling is ``per_day_max`` and the
    #: floor is ``per_day_min``; a draw can never be below the floor, and a day is
    #: never asked for more candidates than the catalogue holds, because a ceiling
    #: may be drawn under but not exceeded.
    upper = min(per_day_max, len(activities))
    target: dict[date, int] = {
        day: per_day_min + count_rng.randrange(upper - per_day_min + 1) for day in month_days
    }
    #: Slots the day still has for its drawn count, keyed by date.
    free: dict[date, int] = dict(target)
    #: The headroom above the drawn count, up to the ceiling. Only an outstanding
    #: monthly minimum may spend it: the catalogue's floor is hard, so a month that
    #: still owes obligations is allowed a fuller day, but never a longer one.
    headroom: dict[date, int] = {day: per_day_max - target[day] for day in month_days}
    counts: dict[str, int] = {item.activity_id: 0 for item in activities}
    on_day: dict[date, set[str]] = {day: set() for day in month_days}

    def gap_allows(item: ActivitySpec, day: date) -> bool:
        previous = last_day[item.activity_id]
        if previous is None:
            return True
        return (day - previous).days >= max(item.repeat_gap_days, MINIMUM_GAP)

    def allowed(item: ActivitySpec, day: date) -> bool:
        return (
            free[day] + headroom[day] > 0
            and item.activity_id not in on_day[day]
            and counts[item.activity_id] < item.monthly_max
            and gap_allows(item, day)
            and item.allowed_on(day.weekday())
        )

    def place(item: ActivitySpec, day: date) -> None:
        counts[item.activity_id] += 1
        last_day[item.activity_id] = day
        if free[day] > 0:
            free[day] -= 1
        else:
            headroom[day] -= 1
        on_day[day].add(item.activity_id)

    slots = len(month_days) * per_day_max
    obligated = sum(item.monthly_min for item in activities)
    if obligated > slots:
        raise ActivityInfeasible(
            C_ACTIVITY_MONTH_MIN_UNSATISFIABLE,
            (
                f"{month.label()} has {len(month_days)} eligible day(s) allowing at "
                f"most {per_day_max} activities each, which is {slots} placements, "
                f"but the catalogue requires {obligated} placements to meet its "
                "monthly minimums; raise the daily maximum or lower the minimums"
            ),
            month=month,
        )

    # One chronological pass, so the gap bookkeeping only ever looks backwards. Each
    # day is filled with the outstanding obligations first - the catalogue's monthly
    # minimums are a floor, not a suggestion - and then by weighted choice among the
    # activities the hard constraints still permit. The weight orders the choice; it
    # can never unlock an activity a maximum, a gap or a weekday rule has excluded.
    for index, day in enumerate(month_days):
        drawn = target[day]
        while free[day] > 0:
            pending = [
                item
                for item in activities
                if counts[item.activity_id] < item.monthly_min and allowed(item, day)
            ]
            if pending:
                pending.sort(
                    key=lambda item: (
                        -(item.monthly_min - counts[item.activity_id]),
                        -item.weight,
                        item.activity_id,
                    )
                )
                chosen = pending[0]
            else:
                candidates = [item for item in activities if allowed(item, day)]
                if not candidates:
                    raise ActivityInfeasible(
                        C_ACTIVITY_DAY_SHORT,
                        (
                            f"only {drawn - free[day]} of {drawn} activities can "
                            f"be placed on {day.isoformat()}: no further catalogue "
                            "activity is permitted there under its monthly maximum, "
                            "repeat gap or weekday restriction"
                        ),
                        month=month,
                        day=day,
                    )
                chosen = _weighted_choice(rng, candidates)
            place(chosen, day)

        # The drawn count is the day's usual load. An obligation the month still owes
        # may borrow the day's headroom, because a minimum is hard and the ceiling is
        # the one bound it may not cross.
        while headroom[day] > 0:
            pending = [
                item
                for item in activities
                if counts[item.activity_id] < item.monthly_min and allowed(item, day)
            ]
            if not pending:
                break
            pending.sort(
                key=lambda item: (
                    -(item.monthly_min - counts[item.activity_id]),
                    -item.weight,
                    item.activity_id,
                )
            )
            place(pending[0], day)

        # Whatever is still owed must still fit in the slots that remain, or the
        # month can never reach its minimums and continuing would only produce a
        # register that claims a catalogue it did not follow.
        room = sum(free[other] + headroom[other] for other in month_days[index:])
        owed = sum(
            item.monthly_min - counts[item.activity_id]
            for item in activities
            if counts[item.activity_id] < item.monthly_min
        )
        if owed > room:
            short = sorted(
                item.activity_id
                for item in activities
                if counts[item.activity_id] < item.monthly_min
            )
            raise ActivityInfeasible(
                C_ACTIVITY_MONTH_MIN_UNSATISFIABLE,
                (
                    f"{month.label()} still owes {owed} placement(s) to meet the "
                    f"catalogue's monthly minimum for {', '.join(short)}, but only "
                    f"{room} slot(s) remain after {day.isoformat()}"
                ),
                month=month,
                day=day,
            )

    unmet = sorted(
        f"{item.activity_id} ({counts[item.activity_id]}/{item.monthly_min})"
        for item in activities
        if counts[item.activity_id] < item.monthly_min
    )
    if unmet:
        raise ActivityInfeasible(
            C_ACTIVITY_MONTH_MIN_UNSATISFIABLE,
            (
                f"{month.label()} cannot meet the catalogue's monthly minimum for "
                + ", ".join(unmet)
                + "; the month's eligible days are too few or too restricted"
            ),
            month=month,
        )

    return [
        ActivityPlacement(day=day, activity=_by_id(activities, activity_id))
        for day in month_days
        for activity_id in sorted(on_day[day])
    ]


def _weighted_choice(rng: random.Random, candidates: Sequence[ActivitySpec]) -> ActivitySpec:
    """One candidate, drawn in proportion to its weight, ties broken by id."""

    weights = [max(item.weight, 0.0) for item in candidates]
    total = sum(weights)
    if total <= 0:
        return candidates[rng.randrange(len(candidates))]
    point = rng.random() * total
    cumulative = 0.0
    for item, weight in zip(candidates, weights, strict=True):
        cumulative += weight
        if point < cumulative:
            return item
    return candidates[-1]


def _by_id(activities: Sequence[ActivitySpec], activity_id: str) -> ActivitySpec:
    for item in activities:
        if item.activity_id == activity_id:
            return item
    raise KeyError(activity_id)
