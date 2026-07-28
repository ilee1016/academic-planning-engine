"""Deterministic constraint diagnostics for academic schedule planning.

Called only when generate_schedules() returns an empty list.  Analyzes the
option pool and locked sections to explain why no valid schedule exists.

Public API:
    diagnose_no_schedules(
        *,
        options, locked_sections, preferences,
        candidate_sections=None,
    ) -> ConstraintDiagnostic

Diagnostic checks (evaluated in priority order; first applicable cause returned):
    1.  Locked credits exceed max_credits
    2.  No options in the option pool
    3.  Minimum credits mathematically unreachable (optimistic upper bound < min)
    4.  Every individual option would exceed max_credits
    5.  Locked sections conflict with each other
    6.  Every eligible option conflicts with at least one locked section
    7.  Free-day constraint blocks all schedules (probe with relaxed free_days)
    8.  Earliest-start constraint blocks all schedules (probe without earliest_start)
    9.  Latest-end constraint blocks all schedules (probe without latest_end)
    10. Credit bounds too tight (probe with bounded relaxations)
    11. Fallback: no isolated cause found

Probes:
    Probes 7–9 re-expand options from candidate_sections under relaxed preferences
    (when candidate_sections is provided) and then call generate_schedules(max_results=1).
    Probes 10 call generate_schedules directly with the same options and modified
    credit bounds (max_results=1).

    Input preferences are never mutated; dataclasses.replace() is used throughout.
    Input options and locked_sections are never mutated.
    No PII appears in diagnostic messages (course codes used for identification).

Invariants:
    INV-01  No adapter imports.
    INV-40  No circular imports: diagnostics.py → solver.py → models.py.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

from app.core.solver import (
    SelectionOption,
    SolverError,
    expand_selection_options,
    generate_schedules,
)
from app.models import (
    ConstraintDiagnostic,
    CourseSection,
    Preferences,
)

_PROBE_CAP = 1  # INV (from spec): diagnostic solver calls use a cap of one result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _locked_parent_credits(locked_sections: list[CourseSection]) -> Decimal:
    return sum((s.credits for s in locked_sections if s.is_parent), Decimal("0"))


def _probe_generate(
    options: list[SelectionOption],
    locked_sections: list[CourseSection],
    prefs: Preferences,
) -> bool:
    """Return True iff generate_schedules finds at least one schedule.

    Catches SolverError so caller can treat any solver rejection as a non-result.
    """
    try:
        return bool(
            generate_schedules(options, locked_sections, prefs, max_results=_PROBE_CAP)
        )
    except SolverError:
        return False


def _expanded_probe(
    candidate_sections: list[CourseSection],
    locked_sections: list[CourseSection],
    prefs: Preferences,
) -> bool:
    """Re-expand options from candidate_sections under relaxed prefs and probe.

    Used for time-constraint probes (free_days, earliest_start, latest_end):
    those constraints affect option expansion, not generate_schedules itself.
    By re-expanding under relaxed prefs we include sections that were filtered
    out of the original option pool by those constraints.
    """
    new_options = expand_selection_options(candidate_sections, prefs)
    return _probe_generate(new_options, locked_sections, prefs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def diagnose_no_schedules(
    *,
    options: list[SelectionOption],
    locked_sections: list[CourseSection],
    preferences: Preferences,
    candidate_sections: list[CourseSection] | None = None,
) -> ConstraintDiagnostic:
    """Identify which hard constraint(s) caused generate_schedules to return [].

    Returns a ConstraintDiagnostic with no_valid_schedules=True, one or more
    reasons describing the cause, and corresponding suggested_relaxations.

    Args:
        options:            SelectionOptions passed to the failing generate_schedules
                            call.  Not mutated.
        locked_sections:    Locked sections passed to the failing call.  Not mutated.
        preferences:        Preferences passed to the failing call.  Not mutated.
        candidate_sections: Optional parent CourseSection list before option expansion.
                            Enables probing for time-constraint causes (checks 7–9)
                            by re-expanding options under relaxed preferences.
                            Not required; those checks are skipped when absent.

    Returns:
        ConstraintDiagnostic(no_valid_schedules=True, reasons=[...], suggested_relaxations=[...])

    The returned reasons list is in the priority order defined in this module's
    docstring.  Reasons and relaxations are aligned one-to-one in the same order.

    Diagnoses identify LIKELY binding constraints, not a mathematically proven
    minimal unsatisfiable core.  Multiple causes may be reported when probing
    finds several independent constraints each sufficient to cause the failure.
    """
    reasons: list[str] = []
    relaxations: list[str] = []

    lcred = _locked_parent_credits(locked_sections)

    # ---- 1. Locked credits exceed max_credits ----------------------------
    if lcred > preferences.max_credits:
        return ConstraintDiagnostic(
            no_valid_schedules=True,
            reasons=[
                "Current locked registration exceeds the maximum credit limit."
            ],
            suggested_relaxations=[
                "Raise the maximum credit limit or unlock a preregistered course."
            ],
        )

    # ---- 2. No options ---------------------------------------------------
    if not options:
        return ConstraintDiagnostic(
            no_valid_schedules=True,
            reasons=[
                "No eligible course selections remain after filtering and "
                "linked-section expansion."
            ],
            suggested_relaxations=[
                "Review excluded courses, completed-course rules, time windows, "
                "and linked-section availability."
            ],
        )

    # ---- 3. Minimum credits mathematically unreachable -------------------
    best_per_code: dict[str, Decimal] = {}
    for opt in options:
        code = opt.parent.course_code
        if code not in best_per_code or opt.credits > best_per_code[code]:
            best_per_code[code] = opt.credits
    optimistic_max = lcred + sum(best_per_code.values())
    if optimistic_max < preferences.min_credits:
        return ConstraintDiagnostic(
            no_valid_schedules=True,
            reasons=[
                "The available course options cannot reach the minimum credit load."
            ],
            suggested_relaxations=[
                "Lower the minimum credit requirement or broaden candidate eligibility."
            ],
        )

    # ---- 4. Every option individually exceeds max_credits ----------------
    can_add_any = any(
        lcred + opt.credits <= preferences.max_credits for opt in options
    )
    if not can_add_any:
        return ConstraintDiagnostic(
            no_valid_schedules=True,
            reasons=[
                "Every remaining course option would exceed the maximum credit load."
            ],
            suggested_relaxations=[
                "Raise the maximum credit limit or reduce locked credits."
            ],
        )

    # ---- 5. Locked-section time conflicts --------------------------------
    for i in range(len(locked_sections)):
        for j in range(i + 1, len(locked_sections)):
            a, b = locked_sections[i], locked_sections[j]
            if a.conflicts_with(b):
                codes = f"{a.course_code} and {b.course_code}"
                return ConstraintDiagnostic(
                    no_valid_schedules=True,
                    reasons=[
                        "Two locked sections overlap in meeting time."
                    ],
                    suggested_relaxations=[
                        f"Unlock or change one of the conflicting registered "
                        f"sections ({codes})."
                    ],
                )

    # ---- 6. Every eligible option conflicts with locked sections ----------
    if locked_sections:
        eligible = [
            opt for opt in options
            if lcred + opt.credits <= preferences.max_credits
        ]
        if eligible and all(
            any(opt.parent.conflicts_with(ls) for ls in locked_sections)
            for opt in eligible
        ):
            return ConstraintDiagnostic(
                no_valid_schedules=True,
                reasons=[
                    "Every eligible option conflicts with the current locked registration."
                ],
                suggested_relaxations=[
                    "Unlock a conflicting registered course or choose a different section."
                ],
            )

    # ---- 7–11. Constraint probes ----------------------------------------
    # Collect (reason, relaxation) tuples; report all found causes.
    causes: list[tuple[str, str]] = []

    # 7. Free-day constraint
    if preferences.free_days and candidate_sections is not None:
        relaxed = dataclasses.replace(preferences, free_days=[])
        if _expanded_probe(candidate_sections, locked_sections, relaxed):
            causes.append((
                "The requested free-day constraint prevents all valid schedules.",
                "Remove or change one requested free day.",
            ))

    # 8. Earliest-start constraint
    if preferences.earliest_start is not None and candidate_sections is not None:
        relaxed = dataclasses.replace(preferences, earliest_start=None)
        if _expanded_probe(candidate_sections, locked_sections, relaxed):
            causes.append((
                "The earliest-start constraint prevents all valid schedules.",
                "Allow courses that begin earlier.",
            ))

    # 9. Latest-end constraint
    if preferences.latest_end is not None and candidate_sections is not None:
        relaxed = dataclasses.replace(preferences, latest_end=None)
        if _expanded_probe(candidate_sections, locked_sections, relaxed):
            causes.append((
                "The latest-end constraint prevents all valid schedules.",
                "Allow courses that end later.",
            ))

    # 10. Credit bounds (only probe if no time-constraint cause found yet)
    if not causes:
        low_min = max(Decimal("0"), preferences.min_credits - Decimal("1"))
        if low_min < preferences.min_credits:
            relaxed = dataclasses.replace(preferences, min_credits=low_min)
            if _probe_generate(options, locked_sections, relaxed):
                causes.append((
                    "The minimum credit requirement is too restrictive.",
                    "Lower the minimum credit requirement.",
                ))

        high_max = preferences.max_credits + Decimal("1")
        relaxed = dataclasses.replace(preferences, max_credits=high_max)
        if _probe_generate(options, locked_sections, relaxed):
            causes.append((
                "The maximum credit limit prevents valid schedules.",
                "Raise the maximum credit limit.",
            ))

    if causes:
        for reason, relaxation in causes:
            reasons.append(reason)
            relaxations.append(relaxation)
    else:
        # 11. Fallback
        reasons.append(
            "No valid combination satisfies all current hard constraints."
        )
        relaxations.append(
            "Relax one hard constraint or select different course sections."
        )

    return ConstraintDiagnostic(
        no_valid_schedules=True,
        reasons=reasons,
        suggested_relaxations=relaxations,
    )
