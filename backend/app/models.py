from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal


# ---------------------------------------------------------------------------
# Time and scheduling primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeetingTime:
    """A single time slot on one or more days. Immutable — used in set operations."""

    days: tuple[str, ...]  # single uppercase letters: "M", "T", "W", "R", "F"
    start: time
    end: time

    def conflicts_with(self, other: MeetingTime) -> bool:
        """True iff the two meeting times overlap on at least one shared day."""
        day_overlap = bool(set(self.days) & set(other.days))
        time_overlap = self.start < other.end and other.start < self.end
        return day_overlap and time_overlap


# ---------------------------------------------------------------------------
# Catalog domain models
# ---------------------------------------------------------------------------


@dataclass
class CourseSection:
    """One schedulable section as normalized from the course catalog CSV."""

    ref_no: str          # unique section ID from CSV ("Crs Ref No"); primary key
    subject: str         # e.g. "CPSC"
    number: str          # e.g. "035" (Excel formula stripped)
    section_id: str      # e.g. "01" or "A"
    title: str
    credits: Decimal     # Decimal("0") for labs and drills
    distribution: frozenset[str]  # raw + derived composites e.g. frozenset({"HU","W","HUW"})
    enr_limit: int | None
    instructors: list[str]
    course_type: str     # "Course", "Lab", "Language Course", etc.
    meeting_times: list[MeetingTime]
    note: str
    linked_sections: list[CourseSection]  # labs/drills for parent; empty list for children

    @property
    def course_code(self) -> str:
        return f"{self.subject} {self.number}"

    @property
    def is_parent(self) -> bool:
        return self.course_type in {
            "Course",
            "Language Course",
            "Studio Course",
            "FY Seminar",
            "Seminar1",
            "Workshop",
        }

    def conflicts_with(self, other: CourseSection) -> bool:
        """True iff any meeting time of self overlaps with any meeting time of other."""
        return any(
            mt1.conflicts_with(mt2)
            for mt1 in self.meeting_times
            for mt2 in other.meeting_times
        )


# ---------------------------------------------------------------------------
# Audit domain models
# ---------------------------------------------------------------------------


@dataclass
class CompletedCourse:
    """A course from the student's academic history (transcript or preregistered)."""

    code: str       # e.g. "CPSC 035"
    title: str
    grade: str      # "B+", "CR", "W", "S", "----" for preregistered
    credits: Decimal
    term: str       # e.g. "Spring 2024"

    @property
    def is_passing(self) -> bool:
        """False for withdrawn, no-credit, fail, or not-reported grades."""
        return self.grade not in {"W", "NC", "F", "NR"}

    @property
    def is_letter_grade(self) -> bool:
        """False for pass/fail, credit/no-credit, and special grades."""
        return self.grade not in {"CR", "NC", "S", "U", "W", "", "----"}


@dataclass
class RequirementBlock:
    """One requirement section as parsed from the Degree Works audit.

    Represents what Degree Works already computed — never re-derived by the engine.
    `still_needed_text` is display-only; it is never parsed algorithmically.
    """

    name: str          # e.g. "DISTRIBUTION: Arts and Humanities (HU)"
    status: str        # "COMPLETE" | "INCOMPLETE"
    still_needed_text: str  # raw DW text; empty if COMPLETE; display only


@dataclass
class StudentRecord:
    """Complete parsed representation of one student's Degree Works audit."""

    name: str
    student_id: str
    major: str
    class_year: int
    catalog_year: str        # e.g. "202304"
    credits_required: Decimal
    credits_applied: Decimal
    audit_date: date
    completed_courses: list[CompletedCourse]
    preregistered_courses: list[CompletedCourse]  # grade will be "----"
    other_courses: list[CompletedCourse]          # withdrawn, additional, not-applied
    exempted_courses: list[str]                   # codes exempted via placement (e.g. ["CPSC 021"])
    requirement_blocks: list[RequirementBlock]
    exceptions: list[str]                         # raw registrar exception descriptions


# ---------------------------------------------------------------------------
# Requirement evaluation models
# ---------------------------------------------------------------------------


@dataclass
class RequirementItem:
    """One evaluable remaining requirement item.

    Fields are ordered: all required before defaults.
    - `matching_attributes`: frozenset for consistency with CourseSection.distribution.
    - `subject_predicate`: when set, any course from this subject satisfies one unit.
    - `auto_registered`: True means the item is handled outside the solver (e.g. CPSC 099).
    """

    id: str                              # stable slug, e.g. "writing_huw"
    label: str                           # human-readable, shown in UI
    satisfied: bool
    satisfying_courses: list[str]        # specific course codes (e.g. ["CPSC 031"])
    matching_attributes: frozenset[str]  # distribution codes (e.g. frozenset({"HUW", "NSW"}))
    notes: str                           # displayed in UI, e.g. "minimum grade C required"
    subject_predicate: str | None = None # e.g. "CPSC" — any 1-credit course from subject satisfies
    auto_registered: bool = False        # True → exclude from candidate pool entirely


@dataclass(frozen=True)
class RequirementDefinition:
    """Pairs a RequirementItem with the Degree Works block name patterns that establish its status.

    Produced by adapters (e.g. adapters/swarthmore/requirement_defs.py); consumed by
    core/requirements.py. block_patterns are Degree Works block name substrings checked
    case-insensitively after normalization. Each pattern must match at most one block.
    """

    item: RequirementItem
    block_patterns: tuple[str, ...]


@dataclass
class RequirementStatus:
    """Derived from StudentRecord. Tells the ranker what remains and how to score schedules."""

    items: list[RequirementItem]
    credits_remaining: Decimal  # credits_required - credits_applied

    def items_satisfied_by(self, section: CourseSection) -> list[RequirementItem]:
        """Return unsatisfied items that adding this section would advance."""
        result = []
        for item in self.items:
            if item.satisfied or item.auto_registered:
                continue
            code_match = section.course_code in item.satisfying_courses
            attr_match = bool(item.matching_attributes & section.distribution)
            subj_match = (
                item.subject_predicate is not None
                and section.subject == item.subject_predicate
                and section.credits >= Decimal("1")
            )
            if code_match or attr_match or subj_match:
                result.append(item)
        return result


# ---------------------------------------------------------------------------
# Preference models
# ---------------------------------------------------------------------------


@dataclass
class Preferences:
    """Student-supplied scheduling preferences. Defaults represent a standard full load."""

    min_credits: Decimal = Decimal("3")
    max_credits: Decimal = Decimal("4")
    free_days: list[str] = field(default_factory=list)       # days to keep clear, e.g. ["F"]
    earliest_start: time | None = None                        # hard cutoff; None = no restriction
    latest_end: time | None = None                            # hard cutoff; None = no restriction
    preferred_subjects: list[str] = field(default_factory=list)
    excluded_courses: list[str] = field(default_factory=list) # course codes to exclude
    lock_preregistered: bool = True


# ---------------------------------------------------------------------------
# Schedule and ranking models
# ---------------------------------------------------------------------------


@dataclass
class Schedule:
    """Pure solver output. Contains only selected sections and credit total.

    The solver never reads RequirementStatus — this type has no requirement fields.
    The ranker reads RequirementStatus and produces RankedSchedule from this.
    """

    parent_sections: list[CourseSection]
    lab_sections: list[CourseSection]  # one linked lab/drill per selected parent that requires one
    total_credits: Decimal

    @property
    def all_sections(self) -> list[CourseSection]:
        return self.parent_sections + self.lab_sections


@dataclass
class RankedSchedule:
    """Solver output annotated with ranking metadata and requirement analysis.

    The ranker computes `requirement_gains` by calling
    RequirementStatus.items_satisfied_by() for each parent section.
    The AI explainer populates `explanation` afterward.
    """

    schedule: Schedule
    category: str              # "requirements" | "preferences" | "balanced" | "current_registration"
    score: float               # 0.0–100.0
    score_breakdown: dict[str, float]
    requirement_gains: list[RequirementItem]  # computed by ranker; never populated by solver
    explanation: str = ""      # set by AI explainer; empty string if explainer fails or unavailable


@dataclass
class ExplainerInput:
    """Data transfer object for the AI explanation layer.

    Contains only display-ready strings — no domain model objects, internal IDs,
    scores, or implementation details. This is the exact boundary between the
    deterministic engine and the generative AI layer.
    """

    student_name: str                  # first name only (e.g. "Isaac")
    archetype_label: str               # e.g. "Best for Requirements"
    credit_total: str                  # e.g. "3" or "4"
    schedule_courses: list[str]        # display strings e.g. ["CPSC 031 — Intro (TR 11:20, lab W 10:30)"]
    requirements_addressed: list[str]  # display strings e.g. ["CS Major — Intro to Computer Systems"]


@dataclass
class ConstraintDiagnostic:
    """Produced when the solver returns zero valid schedules.

    All conflict and unsatisfiability information is expressed as human-readable
    prose inside `reasons` — there are no separate structured conflict fields.
    """

    no_valid_schedules: bool
    reasons: list[str]                # human-readable explanations, max 3 bullets
    suggested_relaxations: list[str]  # specific actions the student can take
