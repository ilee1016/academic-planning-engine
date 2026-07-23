# Technical Architecture Review: Academic Planning Engine

## Context

This is a senior architect review of the proposed Academic Planning Engine before any implementation begins. The repo is currently empty. The goal is to answer 10 specific engineering questions and produce a set of concrete architectural decisions to guide implementation.

---

## 1. Is the Architecture Appropriately Modular?

**Short answer: mostly yes, with two weak spots.**

The linear pipeline (Parser → Model → Requirements → CSP → Optimization → AI) is the right shape. It's testable, replaceable, and follows data flow naturally.

**Weak spots:**

- **"Recommendation Engine" and "AI Explanation Layer" are underspecified.** Right now they appear to be one thing. Separate them clearly: the Recommendation Engine is algorithmic (selects and ranks the top-N schedules), and the AI layer is a consumer of that output, not a peer component.

- **Feedback loops are absent.** When the CSP produces zero valid schedules, the system has no way to explain why or relax constraints. You need a "why did this fail?" path — either the CSP reports which constraints it couldn't satisfy, or a separate diagnostic pass runs.

**Recommendation:** Add an explicit `ConstraintDiagnostic` output alongside valid schedules so the AI layer can explain infeasibility, not just success.

---

## 2. Hidden Technical Challenges

In rough order of severity:

### Prerequisite parsing (highest risk)
Swarthmore's catalog writes prerequisites in natural language: *"CSCI 031 or MATH 027, or permission of instructor."* Parsing this reliably into a boolean expression tree is harder than parsing meeting times. You need to handle:
- OR conditions (`CSCI 031 or MATH 027`)
- AND conditions (`CSCI 035 and MATH 028`)
- Permission overrides (`or permission of instructor` — should these be modeled?)
- Corequisites (taken simultaneously, not before)

A regex-based parser will fail on edge cases. Consider a small PEG grammar (e.g., via `lark` in Python) for prerequisite strings.

### Requirement rule complexity (second-highest risk)
Real graduation requirements look like:

> "8 courses in the major, of which at least 2 must be at the 100-level, at least 3 at the 200-level, and no more than 1 may be fulfilled by a course also used to satisfy distribution requirements."

This is not a list of courses. It's a quantified constraint with cross-requirement interaction (double-counting rules). Modeling this naively as a list of required courses will break immediately.

### Course section time data
Meeting times in exported catalogs are often inconsistently formatted. Expect strings like `"MWF 10:30-11:20"`, `"TR 1:15-2:30"`, `"M 7:00-9:50pm"`. Parser must normalize all of these into structured `MeetingTime` objects before any conflict detection can happen.

### The search space is smaller than you think — but the branching is trickier
For a single semester, a student choosing 4 courses from ~80 available sections sounds manageable. But requirement satisfaction adds a combinatorial dimension: you're not just picking 4 sections that don't conflict, you're picking 4 that *together* cover the most useful requirements. That's a set-cover variant, which is NP-hard in general. Pruning strategy matters.

### Grades and in-progress courses
Did the student get a C- in Calculus? Does Swarthmore require a minimum grade for prerequisite satisfaction? The student model needs to capture grades, not just course codes.

---

## 3. OR-Tools vs. Other Approaches

**OR-Tools CP-SAT is the right long-term choice but may be overkill for MVP.**

The actual semester scheduling problem for a single student is small: ~4 courses from ~80 sections. A well-pruned backtracking search will find all valid schedules in milliseconds. The complexity worth worrying about is *which valid schedules to prioritize*, not *finding valid schedules at all*.

**Recommended two-phase approach:**

**Phase 1 — Enumeration (custom backtracking):**
Build schedules incrementally (pick course 1, pick course 2, ...), pruning on hard constraints at each step. This is fast, debuggable, and produces the complete feasible set. Use this for MVP.

**Phase 2 — Ranking (weighted objective):**
Score each feasible schedule against soft constraints. No OR-Tools needed here — it's just a scoring function.

**When to introduce OR-Tools:**
- If you extend to multi-semester planning (4-year plan), the problem size increases dramatically. CP-SAT shines there.
- If you want to guarantee *optimality* rather than just good solutions.
- If the single-semester feasible set becomes large enough that exhaustive enumeration is slow (unlikely at Swarthmore's scale).

**Bottom line:** Start with backtracking + scoring. Treat OR-Tools as the upgrade path for multi-semester planning.

---

## 4. Academic Domain Model Design

**Core principle: prerequisites are trees, not lists.**

```python
# Prerequisite expression tree
@dataclass
class PrereqAnd:
    children: list[PrereqExpr]

@dataclass  
class PrereqOr:
    children: list[PrereqExpr]

@dataclass
class PrereqCourse:
    course_code: str
    min_grade: str | None = None  # e.g. "C-"

@dataclass
class PrereqPermission:  # "permission of instructor"
    pass

PrereqExpr = PrereqAnd | PrereqOr | PrereqCourse | PrereqPermission
```

**Core entities:**

```python
@dataclass
class MeetingTime:
    days: list[str]          # ["M", "W", "F"]
    start: time
    end: time

@dataclass
class Course:
    code: str                # "CSCI 035"
    title: str
    credits: Decimal
    prerequisites: PrereqExpr | None
    corequisites: list[str]
    attributes: set[str]     # distribution tags, e.g. {"NatSci", "Quantitative"}

@dataclass
class CourseSection:
    course: Course
    section_id: str
    semester: str            # "Fall 2026"
    instructor: str
    meeting_times: list[MeetingTime]
    enrollment_cap: int | None

@dataclass
class CompletedCourse:
    course_code: str
    grade: str               # "A", "B+", "CR", etc.
    semester: str
    credits_earned: Decimal

@dataclass
class Student:
    id: str
    completed: list[CompletedCourse]
    in_progress: list[str]   # course codes currently enrolled
    program: DegreeProgram
    preferences: Preferences

@dataclass
class Schedule:
    sections: list[CourseSection]
    score: float
    requirement_coverage: dict[str, list[str]]   # req_id → [course codes satisfying it]
    diagnostics: list[str]
```

**Key decisions:**
- Credits as `Decimal`, not `float` — avoid floating-point accumulation errors on credit totals.
- `attributes: set[str]` on Course is how distribution requirements get attached without hardcoding.
- `in_progress` on Student is separate from `completed` — needed to avoid recommending courses already enrolled.

---

## 5. Requirement Evaluation: Rule-Based vs. Graph-Based

**Neither. Use a composable predicate tree (mini-DSL).**

Requirement satisfaction is structurally identical to the prerequisite tree above — it's boolean logic over sets of courses. Define a small set of requirement node types:

```python
@dataclass
class RequireCredits:
    minimum: Decimal

@dataclass
class RequireCourseCount:
    minimum: int
    from_set: list[str]      # course codes

@dataclass
class RequireAttribute:
    attribute: str
    minimum_count: int

@dataclass
class RequireAll:             # every child must be satisfied
    children: list[RequirementNode]

@dataclass
class RequireAny:             # at least one child
    children: list[RequirementNode]

@dataclass
class RequireExclusion:       # courses that cannot double-count
    requirement_ids: list[str]
```

**Where graphs fit:** Use a directed graph for *prerequisite dependencies* only — to answer "what courses unlock if I take X?" and to topologically sort requirements for multi-semester planning. The requirement evaluation itself is the predicate tree above.

**Why not pure rule-based?** Rule engines (Drools-style) are overkill and introduce a separate runtime. Python dataclasses with an `evaluate(student, completed_courses) -> bool` method on each node is sufficient and testable.

---

## 6. Multi-University Support Without Overengineering

**The principle: isolate all university-specific code behind typed interfaces. Never let it touch the engine.**

Directory structure:

```
academic_engine/
  core/
    models.py          # domain types (Course, Student, etc.)
    requirements.py    # requirement node types + evaluator
    solver.py          # constraint solver
    optimizer.py       # ranking/scoring
  adapters/
    swarthmore/
      audit_parser.py  # produces Student from Swarthmore degree audit
      catalog_parser.py # produces list[CourseSection] from Swarthmore catalog
      requirements/
        cs_major.py    # Swarthmore CS requirement definitions (RequirementNode trees)
        distribution.py
```

The interface contract is simple: adapters produce `core.models` objects. The engine never imports from `adapters/`.

**What NOT to do:** Don't try to parameterize the parser (no `SwarthmoreParser(format="csv")` god-class). Each university gets its own module. Parsers are not reusable — they're throwaway glue code. The *engine* is what you're protecting.

---

## 7. API Boundaries Between Subsystems

Every subsystem boundary should be a typed function signature, not a class hierarchy.

```python
# Parser layer → domain model
def parse_degree_audit(path: Path) -> Student: ...
def parse_course_catalog(path: Path, semester: str) -> list[CourseSection]: ...

# Requirement engine
def evaluate_requirements(
    student: Student,
    program: DegreeProgram,
) -> RequirementStatus: ...          # what's done, what remains

# Constraint solver
def generate_valid_schedules(
    student: Student,
    available_sections: list[CourseSection],
    requirement_status: RequirementStatus,
    preferences: Preferences,
    max_results: int = 500,
) -> list[Schedule]: ...             # only hard-constraint-valid schedules

# Optimizer
def rank_schedules(
    schedules: list[Schedule],
    preferences: Preferences,
) -> list[RankedSchedule]: ...       # sorted, with scores + explanations populated

# AI layer
def explain_schedule(
    schedule: RankedSchedule,
    student: Student,
    requirement_status: RequirementStatus,
) -> str: ...                        # natural language explanation
```

All intermediate data crosses boundaries as plain dataclasses. No shared mutable state between subsystems.

---

## 8. Better Optimization Pipeline Structure

**The proposal's soft-constraint scoring is sound, but consider two improvements:**

### Weighted Pareto instead of weighted sum
A weighted sum (score = 0.3×requirement_coverage + 0.2×free_friday + ...) forces you to pick weights upfront, and users can't meaningfully tune them. A better alternative for the top-3 recommendations:

1. Return the schedule that maximizes *requirement coverage*.
2. Return the schedule that best matches *user preferences*.
3. Return the schedule that best *accelerates graduation*.

This gives users meaningful distinct options rather than three schedules that differ only in score.

### Beam search for schedule construction
Instead of enumerating all valid schedules and then ranking, use beam search:
- Build schedules one course at a time.
- At each step, keep only the top-K partial schedules (by estimated final score).
- This scales to larger catalogs without exhaustive enumeration.

For Swarthmore's catalog size, exhaustive enumeration is fine for MVP. Beam search is the upgrade path if performance becomes an issue.

---

## 9. Testing Strategy

### Parsers: golden file tests
Commit a real (or anonymized) degree audit and catalog. Save the expected normalized output as a fixture. Test that the parser output matches the fixture exactly. When Swarthmore changes their format, the test fails and you fix the parser.

```
tests/fixtures/
  swarthmore_audit_sample.pdf
  swarthmore_audit_expected.json
  swarthmore_catalog_fall2026.csv
  swarthmore_catalog_expected.json
```

### Requirement engine: unit tests with synthetic rules
Don't use real Swarthmore requirements in unit tests — build minimal requirement trees and test the evaluator logic in isolation. The real requirement definitions get integration-tested separately.

### Constraint solver: property-based tests (Hypothesis)
Generate random valid students and catalogs. Assert that *every* schedule the solver produces satisfies *every* hard constraint. This finds edge cases (one course, zero courses available, credit limit exactly met, etc.) that handwritten tests miss.

```python
@given(student=st.builds(Student, ...), sections=st.lists(section_strategy))
def test_solver_never_produces_invalid_schedules(student, sections):
    schedules = generate_valid_schedules(student, sections, ...)
    for schedule in schedules:
        assert no_time_conflicts(schedule)
        assert within_credit_limit(schedule, student.preferences)
        assert all_prerequisites_met(schedule, student)
```

### Optimizer: preference satisfaction ordering
Assert relative ordering, not absolute scores. If a student prefers no Friday classes, every schedule with a free Friday must rank above every otherwise-identical schedule with Friday classes.

### Integration tests: synthetic student end-to-end
One integration test per requirement type (CS major, distribution, etc.) using synthetic data. Confirm the pipeline produces expected top recommendations.

---

## 10. If This Were a Production System

Honest list of what would change:

**Data model:** Add versioned requirement definitions. Swarthmore's CS requirements change every few years. A student who started under the 2023 catalog must be evaluated under 2023 rules, not 2026 rules. Requirements need a `catalog_year` field, and students need to track which catalog year they enrolled under.

**Async schedule generation:** The constraint solver could take 1-5 seconds for a complex case. In production, this is a background job — the API returns a job ID immediately, the frontend polls or uses WebSockets. Do not block an HTTP request on CSP solving.

**Prerequisite override tracking:** Real students petition for exceptions all the time ("I took the equivalent at another school"). The system needs a way to record advisor-approved overrides without changing the underlying course data.

**Grade cutoffs:** "B or better required" prerequisites exist. The domain model handles this (`min_grade` on `PrereqCourse`), but the evaluator needs a grade ordering function.

**Caching:** Requirement evaluation for a given student+semester is deterministic. Cache it. Course catalog data changes once per semester — cache aggressively with a semester-scoped key.

**Audit trail:** If an advisor uses this tool, you need a record of what the system recommended and when. Not for the MVP, but design the data model so it's addable without schema rewrites.

---

## Recommended Implementation Order

1. **Domain model first** — define all dataclasses in `core/models.py`. Nothing else can be built without this.
2. **Requirement node tree + evaluator** — the most complex logic; build it before anything touches it.
3. **Swarthmore catalog parser** — structured CSV is easier than PDF; start here.
4. **Swarthmore degree audit parser** — hardest single component; plan 2-3x your estimate.
5. **Constraint solver (backtracking)** — build on top of the verified domain model.
6. **Optimizer (scoring)** — add after solver is tested.
7. **FastAPI backend** — thin wrapper around the pipeline.
8. **Frontend** — last, after the backend API is stable.
9. **AI explanation layer** — easiest to add once the structured data exists.

---

## Critical Decisions to Lock In Before Coding

1. **Prerequisite representation:** Boolean expression tree (as described above). Do not use a string or a flat list.
2. **Requirement representation:** Composable predicate tree, not a hardcoded evaluator.
3. **Adapter isolation:** `core/` never imports from `adapters/`. Enforce this.
4. **Credits as Decimal:** Prevent floating-point drift on credit totals.
5. **Backtracking for MVP, OR-Tools for multi-semester:** Don't reach for OR-Tools until the single-semester case is working.
