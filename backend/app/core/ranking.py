"""Deterministic schedule ranking for academic course planning.

Pipeline position:
  generate_schedules (solver) → rank_schedules → [AI explainer]

Public API:
    rank_schedules(schedules, requirement_status, preferences,
                   *, locked_ref_nos, max_ranked) -> list[RankedSchedule]

Category labels (assigned deterministically per ranked schedule):
    "requirements_first"   2 or more unique requirement items would be gained
    "preferred_subjects"   preferred-subject score > 0 and dominates other non-req components
    "compact_schedule"     compactness + incidental free-day score dominates and schedule is tight
    "balanced"             fallback when no other category dominates
    "current_registration" every section in the schedule is a locked (preregistered) section

Scoring components (always present in score_breakdown):
    "requirement_gains"   +100 per unique unsatisfied RequirementItem addressed
    "preferred_subjects"  +12/9/6/3 per parent section by preferred-subject list position
    "free_days"           +5 per incidental free weekday (used days and explicit free days excluded)
    "compactness"         +20/15/10/5/0 by total weekly idle minutes between meetings
    "credit_load"         +10/6/3/0 by proximity of total_credits to preferences.max_credits

Tie-breaking sort order (applied after total score):
    1. score DESC
    2. requirement_gains count DESC
    3. total_credits DESC
    4. weekly idle minutes ASC (compact schedules first)
    5. used weekday count ASC (fewer campus days first)
    6. canonical ref_no tuple ASC (lexicographic; fully deterministic)

Invariants satisfied:
    INV-23   rank_schedules never mutates Schedule objects.
    INV-23a  Schedule has no requirement_gains field; only RankedSchedule does.
    INV-29   The AI explainer is NOT called here; explanation is always "".
    INV-40   No circular imports: ranking.py → models.py only.
"""
from __future__ import annotations

from decimal import Decimal

from app.models import (
    Preferences,
    RankedSchedule,
    RequirementItem,
    RequirementStatus,
    Schedule,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WEEKDAYS: tuple[str, ...] = ("M", "T", "W", "R", "F")
_WEEKDAY_SET: frozenset[str] = frozenset(_WEEKDAYS)

_BREAKDOWN_KEYS: tuple[str, ...] = (
    "requirement_gains",
    "preferred_subjects",
    "free_days",
    "compactness",
    "credit_load",
)


# ---------------------------------------------------------------------------
# Requirement gains
# ---------------------------------------------------------------------------


def _compute_requirement_gains(
    schedule: Schedule,
    requirement_status: RequirementStatus,
) -> list[RequirementItem]:
    """Items from requirement_status that this schedule would advance.

    Preserves requirement_status.items order.
    Each item is included at most once.
    Already-satisfied and auto_registered items are excluded.
    RequirementItem objects are never mutated.
    """
    gains: list[RequirementItem] = []
    for item in requirement_status.items:
        if item.satisfied or item.auto_registered:
            continue
        for section in schedule.parent_sections:
            matched = requirement_status.items_satisfied_by(section)
            if any(m.id == item.id for m in matched):
                gains.append(item)
                break  # each item counted at most once
    return gains


# ---------------------------------------------------------------------------
# Individual scoring components
# ---------------------------------------------------------------------------


def _score_requirement_gains(gains: list[RequirementItem]) -> float:
    return float(len(gains) * 100)


def _subject_award(position: int) -> float:
    """Points for a preferred subject at the given zero-based list position."""
    if position == 0:
        return 12.0
    if position == 1:
        return 9.0
    if position == 2:
        return 6.0
    return 3.0


def _score_preferred_subjects(
    schedule: Schedule,
    preferred_subjects: list[str],
) -> float:
    """Award preference points for parent sections whose subject is preferred.

    - Linked lab/drill sections (schedule.lab_sections) never receive subject points.
    - Each parent section earns at most one award based on its subject's position.
    """
    if not preferred_subjects:
        return 0.0
    total = 0.0
    for section in schedule.parent_sections:
        try:
            pos = preferred_subjects.index(section.subject)
            total += _subject_award(pos)
        except ValueError:
            pass
    return total


def _used_weekdays(schedule: Schedule) -> frozenset[str]:
    """Set of standard weekday letters used by any section (parent or lab)."""
    used: set[str] = set()
    for section in schedule.all_sections:
        for mt in section.meeting_times:
            used.update(mt.days)
    return frozenset(used & _WEEKDAY_SET)


def _score_free_days(
    used_days: frozenset[str],
    preferences: Preferences,
) -> float:
    """Award +5 per incidental free weekday.

    Incidental free days = all weekdays − used days − explicitly required free days.
    Days already in preferences.free_days are NOT double-counted.
    """
    explicit_free = frozenset(preferences.free_days) & _WEEKDAY_SET
    incidental = _WEEKDAY_SET - used_days - explicit_free
    return float(len(incidental) * 5)


def _time_to_minutes(h: int, m: int) -> int:
    return h * 60 + m


def _weekly_idle_minutes(schedule: Schedule) -> int:
    """Total idle minutes between consecutive meetings across all weekdays.

    Only counts gaps between meetings on the same day.  Time before the first
    meeting, after the last meeting, and between different days is excluded.
    Back-to-back meetings (no gap) contribute 0.
    """
    day_intervals: dict[str, list[tuple[int, int]]] = {}
    for section in schedule.all_sections:
        for mt in section.meeting_times:
            start_m = _time_to_minutes(mt.start.hour, mt.start.minute)
            end_m = _time_to_minutes(mt.end.hour, mt.end.minute)
            for day in mt.days:
                if day in _WEEKDAY_SET:
                    day_intervals.setdefault(day, []).append((start_m, end_m))

    total_idle = 0
    for intervals in day_intervals.values():
        if len(intervals) <= 1:
            continue
        intervals.sort()
        for i in range(len(intervals) - 1):
            gap = intervals[i + 1][0] - intervals[i][1]
            if gap > 0:
                total_idle += gap
    return total_idle


def _score_compactness(idle_minutes: int) -> float:
    """Map total weekly idle minutes to a compactness bonus.

    0 idle minutes → 20 (no gaps at all; fully compact)
    1–60           → 15
    61–120         → 10
    121–240        → 5
    > 240          → 0
    """
    if idle_minutes == 0:
        return 20.0
    if idle_minutes <= 60:
        return 15.0
    if idle_minutes <= 120:
        return 10.0
    if idle_minutes <= 240:
        return 5.0
    return 0.0


def _score_credit_load(schedule: Schedule, preferences: Preferences) -> float:
    """Reward proximity to max_credits as the target credit load.

    Comparisons use Decimal to avoid float drift.
    Exactly target          → 10
    Within 0.5 of target    → 6
    Within 1.0 of target    → 3
    Otherwise               → 0
    """
    target = preferences.max_credits
    diff = abs(schedule.total_credits - target)
    if diff == Decimal("0"):
        return 10.0
    if diff <= Decimal("0.5"):
        return 6.0
    if diff <= Decimal("1"):
        return 3.0
    return 0.0


# ---------------------------------------------------------------------------
# Category assignment
# ---------------------------------------------------------------------------


def _assign_category(
    *,
    requirement_gains: list[RequirementItem],
    score_breakdown: dict[str, float],
    all_ref_nos: frozenset[str],
    locked_ref_nos: frozenset[str],
    idle_minutes: int,
    used_days_count: int,
) -> str:
    """Assign one category label to a ranked schedule.

    Precedence (first match wins):
    1. current_registration — ref_nos exactly match locked_ref_nos (non-empty)
    2. requirements_first   — 2 or more requirement items would be gained
    3. preferred_subjects   — preferred-subject score > 0 and dominates other non-req components
    4. compact_schedule     — compactness + free-day score dominates AND schedule is compact
    5. balanced             — fallback

    Category assignment never affects the numerical score.
    """
    # 1. current_registration
    if locked_ref_nos and all_ref_nos == locked_ref_nos:
        return "current_registration"

    # 2. requirements_first
    if len(requirement_gains) >= 2:
        return "requirements_first"

    pref_score = score_breakdown.get("preferred_subjects", 0.0)
    free_score = score_breakdown.get("free_days", 0.0)
    compact_score = score_breakdown.get("compactness", 0.0)
    compact_total = free_score + compact_score

    # 3. preferred_subjects
    if pref_score > 0.0 and pref_score >= compact_total:
        return "preferred_subjects"

    # 4. compact_schedule — must dominate AND schedule must actually be compact
    if compact_total > pref_score and (used_days_count <= 3 or idle_minutes == 0):
        return "compact_schedule"

    # 5. balanced
    return "balanced"


# ---------------------------------------------------------------------------
# Sort key
# ---------------------------------------------------------------------------


def _sort_key(
    entry: tuple[RankedSchedule, int, int, tuple[str, ...]],
) -> tuple[float, int, float, int, int, tuple[str, ...]]:
    rs, idle_minutes, used_days_count, canon_key = entry
    return (
        -rs.score,
        -len(rs.requirement_gains),
        -float(rs.schedule.total_credits),
        idle_minutes,
        used_days_count,
        canon_key,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def rank_schedules(
    schedules: list[Schedule],
    requirement_status: RequirementStatus,
    preferences: Preferences,
    *,
    locked_ref_nos: frozenset[str] = frozenset(),
    max_ranked: int | None = None,
) -> list[RankedSchedule]:
    """Score and sort schedules; return RankedSchedule objects in descending score order.

    Each schedule receives five scoring components (always present in score_breakdown).
    The total score equals the exact sum of the breakdown values.
    explanation is always set to "" — the AI layer populates it later.

    Args:
        schedules:          Solver-produced Schedule objects.  Not mutated.
        requirement_status: Used to compute requirement_gains per schedule.  Not mutated.
        preferences:        Used for preferred-subject and free-day scoring.  Not mutated.
        locked_ref_nos:     ref_nos of all locked (preregistered) sections.  A schedule
                            whose ref_nos exactly equal this set is labelled
                            "current_registration".  Pass frozenset() (default) when
                            no locking applies.
        max_ranked:         If None, return all ranked schedules.  A positive integer
                            limits output to the top-N after sorting.  Zero or negative
                            raises ValueError.

    Returns:
        list[RankedSchedule] sorted by (score DESC, gains DESC, credits DESC,
        idle ASC, used_days ASC, canonical_key ASC).  Length equals len(schedules)
        unless limited by max_ranked.

    Raises:
        ValueError: if max_ranked is not None and max_ranked < 1.

    Invariants:
        - No input object is mutated.
        - All five breakdown keys are always present.
        - score == sum(score_breakdown.values()) (float arithmetic).
        - explanation == "" for every returned RankedSchedule.
        - Repeated calls with identical inputs produce identical outputs.

    Note on coverage:
        rank_schedules operates on the solver's capped output (up to max_results
        schedules from generate_schedules).  It therefore guarantees the best
        ordering AMONG the supplied schedules, not the globally best schedules
        from all feasible combinations.
    """
    if max_ranked is not None and max_ranked < 1:
        raise ValueError(
            f"max_ranked must be a positive integer or None, got {max_ranked!r}"
        )

    entries: list[tuple[RankedSchedule, int, int, tuple[str, ...]]] = []

    for schedule in schedules:
        gains = _compute_requirement_gains(schedule, requirement_status)
        idle_minutes = _weekly_idle_minutes(schedule)
        used_days = _used_weekdays(schedule)
        used_days_count = len(used_days)
        all_ref_nos = frozenset(s.ref_no for s in schedule.all_sections)

        req_score = _score_requirement_gains(gains)
        subj_score = _score_preferred_subjects(schedule, preferences.preferred_subjects)
        free_score = _score_free_days(used_days, preferences)
        compact_score = _score_compactness(idle_minutes)
        credit_score = _score_credit_load(schedule, preferences)

        breakdown: dict[str, float] = {
            "requirement_gains": req_score,
            "preferred_subjects": subj_score,
            "free_days": free_score,
            "compactness": compact_score,
            "credit_load": credit_score,
        }
        total_score = sum(breakdown.values())

        category = _assign_category(
            requirement_gains=gains,
            score_breakdown=breakdown,
            all_ref_nos=all_ref_nos,
            locked_ref_nos=locked_ref_nos,
            idle_minutes=idle_minutes,
            used_days_count=used_days_count,
        )
        canon_key = tuple(sorted(s.ref_no for s in schedule.all_sections))

        rs = RankedSchedule(
            schedule=schedule,
            category=category,
            score=total_score,
            score_breakdown=breakdown,
            requirement_gains=gains,
            explanation="",
        )
        entries.append((rs, idle_minutes, used_days_count, canon_key))

    entries.sort(key=_sort_key)

    result = [rs for rs, _, _, _ in entries]
    if max_ranked is not None:
        result = result[:max_ranked]
    return result
