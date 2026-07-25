# Academic Planning Engine — Architectural Invariants

These rules must remain true throughout development. Before merging any change, verify it does not violate an invariant. If a proposed change requires violating an invariant, that is a design signal: either the change is wrong, or the invariant needs formal revision with explicit justification documented here.

Violations that are accepted during development must be marked `TODO(invariant: INV-XX)` so they are never forgotten.

---

## Layer Boundaries

**INV-01** `core/` never imports from `adapters/`.  
University-specific code is injected into the engine; the engine never reaches back for it. `adapters/` produces domain model objects and requirement definitions; `core/` consumes them through function arguments, never through imports.

**INV-02** `parsers/` never contains business logic.  
Parsers normalize raw data into domain model objects. Filtering, evaluation, conflict detection, scoring, and ranking are `core/` responsibilities. A parser that rejects a section because of a credit rule has violated this invariant.

**INV-03** `adapters/` never invokes engine logic.  
Adapters define data (requirement definitions, parser output format). They do not call `core/requirements.py`, `core/solver.py`, or `core/ranker.py`.

**INV-04** `main.py` is the only file that reads environment variables or performs file I/O.  
All downstream components receive data through function arguments. A component that reads `os.environ` directly (except `main.py`) has violated this invariant.

**INV-05** The AI explainer has no pathway to alter, filter, or reorder schedules.  
`explainer.py` receives `ExplainerInput` objects and returns `str`. It does not accept `RankedSchedule`, `RequirementStatus`, `StudentRecord`, or any type containing ranking or engine state.

---

## Data Model Integrity

**INV-06** All credit values are `Decimal` throughout the codebase.  
Never use `float` for credits anywhere in the engine, solver, ranker, or parsers. Conversion to `float` or `str` is permitted only at the JSON serialization boundary in `main.py`.

**INV-07** `MeetingTime` is immutable (`frozen=True`).  
`MeetingTime` objects are compared by value in conflict detection. Mutability would enable silent bugs where two references to the "same" meeting time diverge.

**INV-08** Distribution attributes on `CourseSection` are a `frozenset` containing both raw and derived codes.  
Derived composites (HUW, NSW, SSW) are added at parse time. Nothing downstream recomputes them. If a check for "HUW" fails because the frozenset only contains "HU" and "W", the parser has violated this invariant.

**INV-09** `StudentRecord` is never mutated after parsing.  
Candidate filtering produces new collections; it does not modify `StudentRecord.completed_courses` or any other field. Treat every `StudentRecord` as effectively read-only after `audit.py` returns it.

**INV-10** `CourseSection.linked_sections` is populated only for parent sections.  
Child sections (labs, drills) have `linked_sections = []`. This invariant makes parent/child detection unambiguous: a section with a non-empty `linked_sections` is always a parent.

**INV-11** `RequirementBlock.still_needed_text` is display data, never logic.  
It is never parsed by the engine to determine requirement satisfaction. It is passed to the frontend as a human-readable string. The engine uses structured `RequirementItem` definitions from `adapters/swarthmore/requirement_defs.py`.

---

## Parser Guarantees

**INV-12** Every `CourseSection` returned by the catalog parser has a unique `ref_no`.  
`ref_no` is the CSV's `Crs Ref No` field and is the primary key for sections throughout the system. The parser must not produce two sections with the same `ref_no`.

**INV-13** The catalog parser returns only parent sections.  
Child sections (labs, drills) are embedded inside `parent.linked_sections`. The top-level list returned by `parse_catalog()` contains zero child sections.

**INV-14** The audit parser never writes PII to disk.  
Test fixtures use anonymized or synthetic data only. The real degree audit file is processed in memory. No test, no fixture, no log, and no assertion output may contain the real student's name, ID, or contact information.

**INV-15** Credit values parsed from the catalog are `Decimal`, not `float`.  
The empty credits field (labs, drills) parses as `Decimal("0")`, not the integer `0` or the float `0.0`.

---

## Solver Guarantees

**INV-16** Every schedule returned by the solver is conflict-free.  
No two sections in a returned schedule share a meeting time on the same day. This includes labs and drills — all sections (parent + linked) are checked against each other.

**INV-17** Every schedule's total credits is within `[min_credits, max_credits]`.  
The solver enforces both bounds as hard constraints. No schedule outside the credit range appears in the output, even if it would satisfy more requirements.

**INV-18** No schedule contains a course code that appears in `StudentRecord.completed_courses` with `is_passing == True`, unless that course also appears in an unsatisfied `RequirementItem.satisfying_courses`.  
This is the CPSC 031 rule: a course completed with CR (passing) that still appears as a required item (CS major requires letter grade C) must remain available as a candidate. The exception is intentional and documented.

**INV-19** No schedule contains a course code in `StudentRecord.exempted_courses`.  
Placement exemptions exclude courses from the candidate pool even when they are not in `completed_courses`.

**INV-20** No schedule contains a preregistered course code when `Preferences.lock_preregistered == True`.  
Locked preregistered courses are already committed. The solver does not re-select them; it schedules around them.

**INV-21** The solver is deterministic.  
Given identical inputs (same candidates list, same preferences, same seed for any sort tie-breaking), the solver always produces identical schedules in identical order. No randomness enters the solver.

**INV-22** Candidate filtering never calls the solver and the solver never calls candidate filtering.  
These are two strictly sequential pipeline stages. The solver receives a pre-filtered list and trusts it completely.

---

## Ranking Guarantees

**INV-23** The ranker does not modify `Schedule` objects.  
Ranking adds `score`, `score_breakdown`, `category`, and `requirement_gains` to `RankedSchedule` objects. It never mutates the `Schedule` embedded inside. `Schedule` is a pure, read-only solver output. The ranker creates new `RankedSchedule` objects rather than appending to or altering `Schedule` fields.

**INV-23a** `Schedule` contains only selected sections and credit total.  
`Schedule.parent_sections`, `Schedule.lab_sections`, and `Schedule.total_credits` are the complete contents of a solver-produced schedule. There is no `requirement_gains` field on `Schedule`. Requirement analysis belongs to `RankedSchedule`, produced by the ranker.

**INV-24** Three archetypes are selected from distinct scoring objectives.  
`"requirements"`, `"preferences"`, and `"balanced"` are computed from different primary sort keys as defined in Section 11 of BASELINE.md. They are not three instances of the same scoring function with different weights.

**INV-25** No two returned schedules are identical.  
If the top candidates from two archetypes are the same schedule, the lower-ranked duplicate is replaced with the next distinct candidate from that archetype. The three returned schedules always differ by at least one course.

---

## Requirement Evaluation

**INV-26** Requirement evaluation is deterministic and side-effect-free.  
`build_requirement_status(student, program_defs)` is a pure function. Given the same inputs, it always produces the same `RequirementStatus`. It has no I/O, no randomness, and no mutation of its arguments.

**INV-27** Degree Works is the source of truth for completed requirements.  
The engine does not re-derive which requirements a student has already satisfied. The `"COMPLETE"` / `"INCOMPLETE"` status from the parsed audit is authoritative. The engine only evaluates what remains unsatisfied.

**INV-28** `RequirementItem` objects with `auto_registered == True` never enter the candidate pool.  
CPSC 099 (Senior Comprehensive) is auto-registered for senior majors. Its `RequirementItem` is included in `RequirementStatus` for informational display but is never passed to the solver as a schedulable course.

---

## AI Layer

**INV-29** The AI explainer receives only an `ExplainerInput` DTO.  
It does not receive `StudentRecord`, `RequirementStatus`, raw catalog data, solver state, scores, or any internal representation. `ExplainerInput` contains student name, archetype label, credit total, course display strings, and plain-English requirement descriptions. Nothing else.

**INV-30** Explanation failures are non-fatal.  
If the Claude API returns an error, times out, or is unavailable, `RankedSchedule.explanation` is set to `""`. The API response returns all three ranked schedules regardless. The frontend must handle empty explanations gracefully.

**INV-31** The AI explainer is never called if no valid schedules exist.  
The explainer is invoked only after the ranker produces at least one `RankedSchedule`. The diagnostic path does not call the explainer.

---

## API and Session

**INV-32** Session state is held in memory only.  
No session data is written to disk or a database. The in-memory session dict is the single source of session truth. Sessions are lost on process restart.

**INV-33** The API never returns a `session_id` that references missing session state.  
`POST /api/plan` with an unknown or expired `session_id` returns HTTP 404. The handler checks session existence before running any pipeline logic.

**INV-34** The API never exposes raw `StudentRecord` data in the response.  
The `POST /api/session` response contains only a sanitized student summary (name, major, class year, credits) and requirement status labels. Raw internal model objects do not flow directly to API responses.

---

## Privacy

**INV-35a** A single course attempt may appear in only one `StudentRecord` classification list.  
`completed_courses`, `preregistered_courses`, and `other_courses` are mutually exclusive with respect to course attempts. A repeated Degree Works rendering of the same attempt (same code, term, grade, and credits) is reconciled using section precedence: `preregistered_courses` > `other_courses` > `completed_courses`. Distinct attempts of the same course code — identified by a different term, grade, or credits — remain separate and may each appear in any list appropriate to their provenance.

**INV-35** The real degree audit PDF (`degreeauditexample.pdf`) must never be committed to git.  
It is listed in `.gitignore`. All test fixtures use `audit_anonymized.pdf` or synthetic data. This invariant exists because the file contains real student PII including name and student ID.

**INV-36** `ExplainerInput.student_name` contains only the student's first name.  
The student's last name, student ID, and email address are never sent to the Claude API.

---

## Correctness and Testing

**INV-37** Every module with business logic has unit tests before that module is considered complete.  
"Business logic" includes: requirement evaluation, conflict detection, candidate filtering, scoring functions, archetype selection, diagnostic generation. A module is not done until its tests exist.

**INV-38** The solver is covered by property-based tests (Hypothesis).  
These tests assert that INV-16, INV-17, INV-18, INV-19, and INV-20 hold across randomly generated inputs. The property test suite runs on every push.

**INV-39** Parser correctness is verified with golden file tests.  
`catalog.py` and `audit.py` have golden file tests against fixture files. Any change to a parser that silently changes the golden output is a test failure, not an expected behavior change.

**INV-40** The dependency graph has no cycles.  
`models.py` → nothing. `parsers/` → `models`. `adapters/` → `models`. `core/` → `models`. `main.py` → everything. There are no circular imports.
