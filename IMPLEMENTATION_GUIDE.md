# Academic Planning Engine — Implementation Guide

**Document type:** Engineering handbook  
**Audience:** Anyone (including future Claude Code sessions) implementing, extending, or debugging this project  
**Scope:** Everything needed to understand how to build and maintain this codebase

---

## 1. Engineering Philosophy

This project is built like professional software, not a hackathon prototype. Every decision optimizes for long-term maintainability over short-term speed.

**The priority stack:**

1. **Correctness** — A schedule with a time conflict is worse than no schedule at all.
2. **Maintainability** — Future sessions (human or AI) must be able to understand and extend this without a guide from the original author.
3. **Simplicity** — The simplest implementation that is correct and maintainable wins. Clever code is a liability.
4. **Testability** — If a component cannot be tested in isolation, its design is wrong.
5. **Readability** — Clear names, clear boundaries, clear data flow.
6. **Extensibility** — Multi-university support and multi-semester planning are coming. Design the seams now.
7. **Performance** — Comes last. The engine is fast enough at this scale. Optimize only when benchmarks prove a problem.

**What this means in practice:**

- A function that does two things should be two functions.
- A module that imports from three layers is doing too much.
- A test that mocks core domain logic is testing the wrong thing.
- A comment that says "this is complicated" is asking for a refactor.
- When in doubt, prefer explicit over clever.

---

## 2. Architectural Principles

These complement the hard invariants in `INVARIANTS.md`. Where invariants are rules that must not be broken, these are principles that should guide every implementation choice.

### Separation of Responsibilities

Every module has exactly one job:

| Module | Responsibility |
|--------|---------------|
| `models.py` | Domain data structures and small model-local predicates or queries. It contains no parsing, orchestration, institution-specific definitions, solver search, ranking, I/O, or external API behavior. (`RequirementStatus.items_satisfied_by()` is intentionally here as a model-local domain query.) |
| `parsers/catalog.py` | Normalize CSV rows into `CourseSection` objects. |
| `parsers/audit.py` | Normalize PDF text into `StudentRecord`. |
| `adapters/swarthmore/requirement_defs.py` | Define which requirements remain and how to recognize satisfaction. |
| `core/requirements.py` | Build `RequirementStatus` from `StudentRecord` + definitions. |
| `core/solver.py` | Produce all valid schedules given a filtered candidate list. |
| `core/ranker.py` | Score schedules and select three categorical archetypes. |
| `core/diagnostics.py` | Explain why no valid schedule exists. |
| `core/explainer.py` | Call the Claude API and return a natural language string. |
| `main.py` | Orchestrate the pipeline. Handle HTTP, files, env vars. |

If you find yourself writing requirement evaluation logic in the parser, or conflict detection in the ranker, stop and relocate it.

### Deterministic Core

The engine (everything except `explainer.py`) must be deterministic. Given the same inputs:
- The solver must return the same schedules in the same order.
- The ranker must return the same archetypes.
- The requirement evaluator must return the same status.

No randomness. No time-based logic. No global mutable state.

### Stable Interfaces

The function signatures defined in BASELINE.md Section 7 are the contracts between pipeline stages. Don't change them without updating BASELINE.md and INVARIANTS.md. Prefer adding optional parameters over breaking existing signatures.

### Simple Domain Models

`models.py` should be boring. Dataclasses with typed fields, a few `@property` helpers, one `conflicts_with` method. No inheritance beyond dataclasses. No metaclasses. No abstract base classes. The models are data, not behavior.

---

## 3. Implementation Roadmap

Each stage is listed with its rationale, dependencies, and what future work it enables.

### Why this order?

Think of the codebase as a building. The pipeline flows top to bottom: parse → evaluate → solve → rank → explain. But we build bottom-up: foundation first, features second.

The most dangerous thing in this project is starting with FastAPI routes before the domain model is stable. A route that accepts a `dict` of unknown shape is a liability that will propagate uncertainty through every downstream component. By defining `models.py` first and keeping it stable, every subsequent component has a concrete, testable contract.

---

### Stage 0 — Domain Models (`models.py`)

**Why this exists:** Every other component depends on it. Getting it wrong costs time at every subsequent stage.

**What it establishes:** The canonical data structures that all components share. The single place where `MeetingTime`, `CourseSection`, `StudentRecord`, `RequirementItem`, `Schedule`, etc. are defined.

**Why before parsers:** Parsers produce models. If the models don't exist yet, parsers have nowhere to write to.

**Why before everything:** The domain model is the universal interface. Components are loosely coupled because they communicate through well-defined model objects, not through shared mutable state or loosely typed dicts.

**What it contains:**
- All dataclasses from BASELINE.md Section 7
- `MeetingTime.conflicts_with()`
- `CourseSection.course_code`, `CourseSection.is_parent`, `CourseSection.conflicts_with()`
- `CompletedCourse.is_passing`, `CompletedCourse.is_letter_grade`
- `Preferences` with sensible defaults
- `Schedule`, `RankedSchedule`, `ExplainerInput`, `ConstraintDiagnostic`

**What it does NOT contain:** Any parsing logic, any business rules, any API types, any Pydantic models.

**Acceptance criteria:**
- `python -c "from app.models import *"` succeeds with no warnings
- All type annotations are valid under `mypy --strict`
- `MeetingTime.conflicts_with()` passes unit tests for: identical times, adjacent times, one-minute overlap, same time different days, multi-day conflicts

---

### Stage 1 — Catalog Parser (`parsers/catalog.py`)

**Why before the audit parser:** The CSV is structured and mechanical. Parsing it builds confidence in the normalization logic (Excel formula stripping, time parsing, distribution derivation) before tackling the harder PDF. A working catalog parser also gives the solver realistic input to test against.

**Why before the solver:** The solver needs realistic section data. Synthetic data works for property-based tests, but the catalog parser gives us the real Fall 2026 sections to benchmark against.

**What it establishes:** The canonical Fall 2026 candidate pool. Once the parser passes its golden file tests, every downstream component has access to trustworthy section data.

**Dependencies:** Stage 0 (models).

**Key parsing challenges to solve correctly:**
1. Excel formula stripping: `="035"` → `"035"` for Num and Sec columns
2. Multi-meeting times: `Days="M,M"`, `Times="01:15pm-04:15pm,01:15pm-04:15pm"` → two `MeetingTime` objects, deduplicated
3. Distribution derivation: `["HU", "W"]` → `frozenset({"HU", "W", "HUW"})`
4. Parent-child linking: second pass groups by `(Subj, Num)`, links Labs/Drills to Course parents
5. Decimal credits: empty field → `Decimal("0")`, not `0`

**Test fixtures to create:**
- `tests/fixtures/catalog_sample_10.csv` — 10 rows: one Course with Lab, one Language Course with Drill, one Course without linked sections, one multi-meeting lab row, one Seminar1 (excluded course type check)
- `tests/fixtures/catalog_sample_10_expected.json` — expected parsed output

**Acceptance criteria:**
- `pytest tests/test_catalog_parser.py` passes golden file test
- `parse_catalog(fall_2026.csv)` returns approximately 469 parent sections (310 `Course`-type + 30 FY Seminar + 32 Language Course + 35 Seminar1 + 41 Studio Course + 21 Workshop). The "310" figure referred only to `Course Type == "Course"` rows; all six `is_parent` types are included.
- CPSC 031 has one lab linked in `linked_sections`
- CPSC 063 has three lab options linked in `linked_sections`
- ARAB 001 (Language Course) has a drill linked in `linked_sections`
- All credits are `Decimal` instances
- All distribution frozensets include derived composites where applicable
- No child sections appear in the top-level returned list

---

### Stage 2 — Degree Audit Parser (`parsers/audit.py`)

**Why after catalog:** The audit parser is the hardest single component. If it is attempted first, any difficulties will block the entire project. Starting with catalog parser success builds momentum and validates the normalization approach before tackling the PDF.

**Why before requirement evaluation:** The requirement evaluator consumes `StudentRecord`. The parser produces `StudentRecord`. The parser must come first.

**What it establishes:** Trustworthy `StudentRecord` objects from real Degree Works output. This is the most fragile component — PDF layout changes can break it silently.

**Dependencies:** Stage 0 (models).

**Required implementation structure — two-function split:**

```python
def parse_audit(pdf_path: Path) -> StudentRecord:
    """Public entry point. Extracts text from PDF and delegates to _parse_audit_text."""
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return _parse_audit_text(text)


def _parse_audit_text(text: str) -> StudentRecord:
    """Parse a pre-extracted text string into a StudentRecord. Testable without a PDF."""
    ...
```

This split is mandatory, not optional. `_parse_audit_text()` is the only function with real parsing logic. Unit tests call it directly with synthetic strings. The PDF fixture is only needed for the integration golden-file test.

**Development order within this stage:**
1. Write `_parse_audit_text()` and its unit tests first, using synthetic text strings derived from the known Degree Works layout. Do not wait for the PDF fixture.
2. Create the anonymized PDF fixture (see Privacy protocol below).
3. Write `parse_audit()` and the golden-file integration test last.

**Known parsing challenges (from walkthrough analysis):**
1. Header block: name, ID, major, class year, catalog year all run together with no delimiter — use targeted regexes
2. Course table rows: work backward from right (Term, Credits, Grade, Title, Code)
3. AP credit sub-rows: `"NO TRANSCRIPT DETAI - Advanced Placement"` — skip
4. Placement exemptions: `"Exempted from CPSC 021 via placement"` — capture code to `exempted_courses`
5. Preregistered credits with parentheses: `(1)` → `Decimal("1")`
6. `additional_courses` and `not_applied_courses` are both stored in `other_courses`

**Test fixtures to create:**
- `tests/fixtures/audit_anonymized.pdf` — created after `_parse_audit_text()` unit tests pass
- `tests/fixtures/audit_anonymized_expected.json`

**Privacy protocol for test fixture creation:**  
The real `degreeauditexample.pdf` must NOT be committed. To create the anonymized fixture:
1. Use the real PDF as a reference document only (read it locally, do not commit)
2. Create a synthetic PDF using `reportlab` or `fpdf2` that mirrors the exact layout but with fake student data: name "Student, Demo", ID "000000000", same major, same requirement blocks, same course history with fictional course codes
3. Alternatively: use a PDF editor to redact name and ID from a copy, then save as `audit_anonymized.pdf`
4. The expected JSON must reflect the anonymized data, not the real student's data

**Sprint tasks (revised):**

| Task | Description | Acceptance |
|------|-------------|------------|
| S2-1 | Write `_parse_audit_text()` with synthetic string input | All parsing unit tests pass (no PDF needed) |
| S2-2 | Unit tests: header parsing | name, major, class_year, catalog_year correctly extracted |
| S2-3 | Unit tests: course row parsing | completed, preregistered, other_courses correctly populated |
| S2-4 | Unit tests: requirement block parsing | COMPLETE/INCOMPLETE status correct |
| S2-5 | Unit tests: exemption parsing | `"Exempted from CPSC 021 via placement"` → `exempted_courses=["CPSC 021"]` |
| S2-6 | Unit tests: edge cases | AP credit rows skipped; `(0)` credits parsed; `"----"` grade preserved |
| S2-7 | Create `audit_anonymized.pdf` | No PII; pdfplumber extracts same layout as real file |
| S2-8 | Write golden file integration test | `parse_audit(anonymized_fixture)` matches expected JSON |

**Acceptance criteria:**
- All unit tests for `_parse_audit_text()` pass without any PDF file
- `pytest tests/test_audit_parser.py` passes golden file integration test against anonymized fixture
- Parsed student has correct major, class year, catalog year, credit totals
- All incomplete requirement blocks appear with correct status
- CPSC 031 appears in `completed_courses` with grade `"CR"`
- Preregistered courses appear in `preregistered_courses` with grade `"----"`
- `exempted_courses` contains the exempted course code
- Withdrawn courses (W grade) appear in `other_courses`
- No PII from the real audit file exists in any test fixture or expected output

---

### Stage 3 — Requirement Evaluation (`adapters/swarthmore/requirement_defs.py`, `core/requirements.py`)

**Why after both parsers:** The evaluator uses both `StudentRecord` (from audit parser) and `CourseSection.distribution` (from catalog parser) to determine satisfaction. Both inputs must be trustworthy before writing the evaluator.

**Why before the solver:** The solver needs `RequirementStatus` to sort candidates by requirement value. The evaluator must exist before candidate sorting works correctly.

**What it establishes:** The bridge between what the student has done (parsed audit) and what the engine cares about (remaining requirements). This is where Degree Works output becomes engine input.

**Dependencies:** Stages 0, 1, 2.

**What to implement:**

`adapters/swarthmore/requirement_defs.py`:
```python
def get_requirement_definitions(student: StudentRecord) -> list[RequirementDefinition]:
    """Return RequirementDefinitions matching the student's major and catalog_year.

    Raises UnsupportedProgramError for unrecognized (major, catalog_year) combinations.
    Returns fresh objects on every call.
    """
    ...
```
Returns a list of `RequirementDefinition` objects (from `models.py`) for the student's `(major, catalog_year)`. Each definition pairs a `RequirementItem` with `block_patterns` — the Degree Works block name substrings that establish its `satisfied` state. For CS major + catalog year 202304, returns the four items documented in BASELINE.md Section 9. Swarthmore-specific block name aliases live here, not in `core/`.

`core/requirements.py`:
```python
def build_requirement_status(
    student: StudentRecord,
    definitions: list[RequirementDefinition],
) -> RequirementStatus:
    ...
```
For each `RequirementDefinition`, matches `block_patterns` against the student's parsed `requirement_blocks` (case-insensitive normalized substring). Sets `satisfied` based on the matched block's Degree Works status. Degree Works is authoritative; transcript courses are never used to infer satisfaction. Emits `UserWarning` for unmatched definitions (item stays `satisfied=False`). Raises `RequirementEvaluationError` if a definition's patterns match multiple blocks.

`RequirementStatus.items_satisfied_by(section)`:
```python
def items_satisfied_by(self, section: CourseSection) -> list[RequirementItem]:
```
Returns only unsatisfied items that `section` would address. Logic:
- If `item.satisfying_courses` contains `section.course_code` → satisfied
- If `item.matching_attributes & section.distribution` is non-empty → satisfied
- If `item.subject_predicate == section.subject` and `section.credits >= 1` → satisfied

**Acceptance criteria:**
- Given demo student record, `RequirementStatus.items` contains all four CS major items with `satisfied=False`
- `items_satisfied_by(cpsc_031_section)` returns `[cs_cpsc031_item, cs_cpsc_credits_item]`
- `items_satisfied_by(latn_011_section)` returns `[writing_item]` (LATN 011 has HU + W → HUW)
- `items_satisfied_by(engr_028_section)` returns `[]` (ENGR 028 satisfies no remaining CS items)
- All tests are pure unit tests with no file I/O

---

### Stage 4 — Candidate Filtering and Solver (`core/solver.py`)

**Why after requirement evaluation:** The solver's candidate sort depends on `items_satisfied_by()`. Without it, all candidates have equal priority and pruning is less effective.

**What it establishes:** The entire scheduling engine. After this stage, the core pipeline is complete.

**Dependencies:** Stages 0, 1, 2, 3.

**Implementation order within this stage:**

1. **Candidate filter function** (a pure function, not a method):
   ```python
   def filter_candidates(
       sections: list[CourseSection],
       student: StudentRecord,
       requirement_status: RequirementStatus,
       preferences: Preferences,
   ) -> list[CourseSection]:
   ```
   Apply the 10 exclusion rules from BASELINE.md Section 10, Step 1, in order. Return sorted by requirement coverage descending.

2. **Linked section expansion**:
   ```python
   @dataclass(frozen=True)
   class SelectionOption:
       parent: CourseSection
       linked_sections: tuple[CourseSection, ...]
       @property
       def all_sections(self) -> tuple[CourseSection, ...]: ...
       @property
       def credits(self) -> Decimal: ...  # parent.credits only

   def expand_selection_options(
       candidates: list[CourseSection],
       preferences: Preferences | None = None,
   ) -> list[SelectionOption]:
   ```
   Produces one `SelectionOption` per valid linked-child combination. With `preferences`, child time-window and free-day violations remove individual options; a parent producing no valid options contributes nothing. See BASELINE.md Section 10 for linked-child semantics.

3. **Conflict detection** (already on `CourseSection` and `MeetingTime` via `conflicts_with()`). Standalone tests in `test_solver.py`.

4. **Backtracking solver**:
   ```python
   def generate_schedules(
       options: list[SelectionOption],
       locked_sections: list[CourseSection],
       preferences: Preferences,
       max_results: int = 500,
   ) -> list[Schedule]:
   ```
   Returns **up to `max_results`** valid schedules in deterministic priority order (not all valid schedules when capped). The `locked_sections` parameter contains preregistered courses when `lock_preregistered=True`. The solver builds around them.

5. **Pre-solver check** (boundary case):
   Inside `generate_schedules`, if `locked_credits >= max_credits`: raise `InvalidLockedScheduleError` if above max; return a single locked-only `Schedule` if equal. The `category="current_registration"` label is NOT set here — it belongs to the ranker or orchestration layer. The solver only produces plain `Schedule` objects.

**Property-based tests with Hypothesis:**
```python
@given(
    sections=st.lists(st.builds(CourseSection, ...)),
    preferences=st.builds(Preferences, ...),
)
def test_no_schedule_has_conflict(sections, preferences):
    results = generate_schedules(expand_with_labs(sections), [], preferences)
    for schedule in results:
        for a, b in combinations(schedule.all_sections, 2):
            assert not a.conflicts_with(b)
```

**Acceptance criteria:**
- All property-based tests pass across 1000 random inputs
- For demo student with `lock_preregistered=False`, credit range 3–4, no other preferences: returns ≥ 100 valid schedules within 5 seconds
- No returned schedule contains CPSC 021 (exempted)
- CPSC 031 appears as a candidate despite being in `completed_courses` with CR grade
- With `lock_preregistered=True` and 4 preregistered credits = max_credits: returns a single "current registration" result without running the solver

---

### Stage 5 — Ranking (`core/ranking.py`, `core/diagnostics.py`)

**Why after solver:** The ranker is a consumer of solver output. The solver must produce real schedules before the scoring functions can be validated against real data.

**Why diagnostics here:** Diagnostics describe why the solver produced zero results. They belong in this stage because they are the "failure path" analogue to the ranker's "success path."

**Dependencies:** Stages 0, 3, 4.

**What was implemented:**

`core/ranking.py`:
```python
def rank_schedules(
    schedules: list[Schedule],
    requirement_status: RequirementStatus,
    preferences: Preferences,
    *,
    locked_ref_nos: frozenset[str] = frozenset(),
    max_ranked: int | None = None,
) -> list[RankedSchedule]:
```
For each `Schedule`, computes `requirement_gains` by calling `requirement_status.items_satisfied_by(section)` for every parent section (deduplicated, in items order). Scores five named components (requirement_gains, preferred_subjects, free_days, compactness, credit_load). Assigns one category label per schedule. Returns all ranked schedules sorted by (score DESC, gains DESC, credits DESC, idle ASC, used_days ASC, canonical_key ASC). Three-archetype selection is the orchestration layer's responsibility (`main.py`), not the ranker's. `explanation` is always `""`.

`core/diagnostics.py`:
```python
def diagnose_no_schedules(
    *,
    options: list[SelectionOption],
    locked_sections: list[CourseSection],
    preferences: Preferences,
    candidate_sections: list[CourseSection] | None = None,
) -> ConstraintDiagnostic:
```
Called only when `generate_schedules()` returns an empty list. Checks 11 priority-ordered conditions (see BASELINE.md Section 12). Probes use `generate_schedules(max_results=1)` with modified `Preferences` copies. Time-constraint probes require `candidate_sections` to re-expand options under relaxed preferences.

**Full-catalog statistics (Fall 2026, CS junior demo student, lock_preregistered=True, free_days=["F"]):**
- Solver schedules ranked: 500
- Top score: 351.0, lowest score: 254.0
- All 500 schedules had 2+ requirement gains → all labelled `requirements_first`
- Highest weekly idle: 1470 min, lowest: 55 min
- Unique score values: 5
- Ranking runtime: ~5 ms; total pipeline (filter→rank): ~17 ms
- Repeated ranking: deterministic ✓; score == sum(breakdown) for all ✓

**Acceptance criteria:**
- When solver returns 0 schedules: diagnostic is non-null, has at least one reason, has at least one suggested relaxation aligned with the reason
- All five score_breakdown keys present in every RankedSchedule
- score == sum(score_breakdown.values()) for every RankedSchedule
- explanation == "" for every RankedSchedule
- rank_schedules never mutates Schedule, RequirementStatus, or Preferences inputs
- 413/413 tests pass; mypy --strict clean

---

### Stage 6 — Backend API (`main.py`, `api_models.py`, `session_store.py`, `orchestration.py`)

**Why after ranking:** The API is a thin orchestration layer. It should not contain any business logic — all of that is already in `core/`. Adding the API before the engine is complete would risk leaking logic into route handlers.

**Dependencies:** Stages 0–5.

**File responsibilities:**

| File | Responsibility |
|------|---------------|
| `app/main.py` | FastAPI app declaration, route handlers, exception translation, DI for session store |
| `app/api_models.py` | Pydantic request/response schemas; conversion functions between API ↔ domain layers |
| `app/session_store.py` | `PlanningSession` dataclass; `InMemorySessionStore` (thread-safe, TTL-aware) |
| `app/orchestration.py` | `plan_schedules()`, `resolve_locked_sections_flexible()`, `group_ranked_by_category()` |

**Local development command:**
```bash
cd backend
.venv/bin/uvicorn app.main:app --reload --port 8000
```
Visit `http://localhost:8000/docs` for the interactive Swagger UI.

**Test commands:**
```bash
cd backend
.venv/bin/pytest tests/test_api_models.py tests/test_session_store.py \
  tests/test_orchestration.py tests/test_api.py -v
```

**Three endpoints (implemented):**
```
POST   /api/session                        Create session → 201 {session_id, created_at}
POST   /api/session/{id}/inputs            Upload PDF+CSV → 200 InputSummaryResponse
POST   /api/session/{id}/schedules         Plan → 200 ScheduleResultResponse | DiagnosticResultResponse
GET    /api/session/{id}                   Session info → 200
DELETE /api/session/{id}                   Delete → 204
GET    /health                             Health → 200 {status: "ok"}
```

**Full-catalog API statistics (Fall 2026, CS junior, lock_preregistered=True, free_days=["F"]):**
- Parse runtime: ~54 ms; orchestration runtime: ~17 ms; total: ~71 ms
- Stored catalog sections: 469; candidates: 342; options: 355
- Generated schedules: 500; ranked: 500; top in response: 10 (cap=3 per category)
- Serialized response size: ~36 KB; solver cap reached: True
- Deterministic IDs stable: True; score==breakdown_sum: True; PII in JSON: None
- OpenAPI schema generates successfully; all 6 paths registered

**Acceptance criteria:**
- All endpoints return correct responses with synthetic fixture files
- `POST /api/session/{id}/inputs` with valid fixtures returns 200 with sanitized summary
- `POST /api/session/{id}/schedules` with valid session returns 200 in << 1 second
- 422 for non-PDF audit file, non-CSV catalog file
- 413 for oversized files (> 10 MiB)
- 404 for invalid session_id or expired session
- 409 for generate-before-upload or locked-section ambiguity
- No PII in any API response
- No raw tracebacks or file paths in error responses
- 501/501 tests pass; mypy --strict clean

---

### Stage 7 — AI Explanation Layer (`core/explainer.py`, `explanation_provider.py`, `explanation_service.py`)

**Why after Stage 6:** Explanations are cosmetic.  The full deterministic pipeline — parse, rank, schedule — works without AI.  Stage 7 adds a lazy explanation endpoint without changing any core logic.

**Dependencies:** Stages 0–6.

**File responsibilities:**

| File | Responsibility |
|------|---------------|
| `app/core/explainer.py` | `ExplainerSection`, `ExplainerRequirementGain`, `ExplainerInput` DTOs; `build_explainer_input()`; `generate_fallback_explanation()`; `ExplanationValidationError`; `validate_explanation()`. No network, no provider imports. |
| `app/explanation_provider.py` | `ExplanationProvider` (Protocol); `AnthropicProvider` (concrete); `_build_provider_message()`; `load_provider()` factory. |
| `app/explanation_service.py` | `ExplanationResult`; `_BoundedCache`; `ExplanationService` (input construction, provider call, validation, fallback, cache). |
| `app/main.py` (updated) | Reads env vars for provider config; creates `ExplanationService` singleton; adds `POST /api/session/{id}/schedules/{schedule_id}/explanation` endpoint; stores ranked schedules in session after generation; clears stale results on new upload. |
| `app/session_store.py` (updated) | Adds `latest_ranked_schedules`, `latest_search_cap_reached`, `latest_preferences` to `PlanningSession`. |
| `app/api_models.py` (updated) | Adds `ExplanationResponse`; exposes `compute_schedule_id()`. |
| `app/models.py` (updated) | Old `ExplainerInput` removed; replaced by the narrower DTO in `core/explainer.py`. |

**Configuration (env vars, read only in `main.py`):**
```
EXPLANATION_PROVIDER=none|anthropic   default: none
ANTHROPIC_API_KEY=                    required if provider=anthropic
ANTHROPIC_EXPLANATION_MODEL=          default: claude-haiku-4-5-20251001
EXPLANATION_TIMEOUT_SECONDS=8         default: 8.0
EXPLANATION_CACHE_SIZE=500            default: 500
```

**Local fallback mode (no credentials required):**
```bash
cd backend
EXPLANATION_PROVIDER=none .venv/bin/uvicorn app.main:app --reload --port 8000
```

**Optional Anthropic provider setup:**
```bash
export EXPLANATION_PROVIDER=anthropic
export ANTHROPIC_API_KEY=your-key-here
.venv/bin/uvicorn app.main:app --reload --port 8000
```

**Explanation API flow:**
1. `POST /api/session/{id}/schedules` → generates + stores `latest_ranked_schedules` in session (no provider call).
2. `POST /api/session/{id}/schedules/{schedule_id}/explanation` → looks up `RankedSchedule` in session; calls `ExplanationService.explain()`; returns `{schedule_id, explanation, source}`.

**Test commands:**
```bash
cd backend
source .venv/bin/activate
pytest tests/test_explainer.py tests/test_explanation_provider.py \
       tests/test_explanation_service.py tests/test_api_explanations.py -v
# No real provider calls occur in the test suite.
```

**Stage 7 statistics (Fall 2026, CS junior, with synthetic fixtures):**
- Fallback generation runtime: ~0.03 ms
- Provider-disabled explanation endpoint runtime: ~0.6 ms
- Explanation response size: ~342 bytes
- Deterministic repeated output: True
- Student identity in explanation: None
- Unknown course codes in fallback output: Zero
- Schedule generation invokes provider: Never
- OpenAPI paths with "explanation": 1 (`/api/session/{id}/schedules/{schedule_id}/explanation`)
- New test count: 96 (43 explainer, 17 provider, 16 service, 20 API)
- Total test count: 597/597 passing; mypy --strict clean

**No real provider calls in tests.** All provider tests use fake implementations.

**Acceptance criteria:**
- `POST /schedules` returns ranked schedules without calling the explanation service.
- `POST /schedules/{id}/explanation` returns 200 with fallback when no provider is configured.
- Provider failure returns 200 with `source="fallback"` (never HTTP 500 from AI unavailability).
- Explanation text passes output validation (no unknown codes, no completion claims, ≤ 1,500 chars).
- No student identity in explanation or provider payload.
- 597/597 tests pass; mypy --strict clean.

---

### Stage 9 — Frontend (Next.js)

**Why after Stage 7:** The frontend consumes both the schedule API and the explanation endpoint. Build it after both are stable.

**Dependencies:** Stages 6, 7.

**Component hierarchy:**
```
app/page.tsx                 # Upload page
app/results/page.tsx         # Results page (query param: session_id)
  components/AuditSummary    # Student profile + requirement status
  components/PreferencesForm # Credit range, free days, start/end times, subjects
  components/ScheduleCard    # One recommended schedule
    components/WeekCalendar  # 5-day calendar with time blocks
    components/RequirementBadge  # Green tag per satisfied requirement
    components/ScoreBreakdown    # Collapsed accordion
  components/DiagnosticPanel # No-schedule error state
```

**Calendar design decision:** The `WeekCalendar` renders sessions as blocks positioned by start/end time. Parent courses and labs use distinct visual treatment (solid fill vs. hatched/lighter fill) so the student can immediately see which blocks are labs. This distinction is critical for schedules like the CPSC 031 + lab combination where Wednesday becomes a heavy lab day.

**UX requirements:**
- Preregistered course lock toggle must appear before the preferences form, not after
- If student is already at max credits, show current registration evaluation without the credit preferences form
- CPSC 031 "2027-2028 Students ONLY" warning appears prominently in the audit summary, not buried in course details
- When no schedule is generated: show diagnostic panel with actionable suggestions, not a generic error

**Acceptance criteria:**
- Full upload-to-results flow works in Chrome without JavaScript errors
- WeekCalendar correctly renders lab blocks distinct from lecture blocks
- Each schedule card displays which remaining requirements it addresses
- The "already fully enrolled" state shows current registration with requirement evaluation
- DiagnosticPanel renders with reasons and suggested relaxations when returned

---

### Stage 10 — Deployment

**When:** After Stages 0–9 pass all acceptance criteria and the full end-to-end flow works locally.

**Backend → Railway:**
- `backend/Dockerfile` or `railway.toml` with `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment variables: `ANTHROPIC_API_KEY`
- No other secrets needed for MVP

**Frontend → Vercel:**
- `NEXT_PUBLIC_API_URL` environment variable pointing to the Railway backend URL
- Standard Next.js Vercel deploy

**Pre-deployment checklist:**
- [ ] `degreeauditexample.pdf` is in `.gitignore` and not committed
- [ ] `ANTHROPIC_API_KEY` is set in Railway environment, not in any committed file
- [ ] End-to-end test with real files passes on production URLs
- [ ] Demo recording is made before any changes are deployed

---

## 4. Sprint Planning

### Sprint 1 — Foundation (Days 1–4)

| Task | Description | Acceptance |
|------|-------------|------------|
| S1-1 | Create repo structure: `backend/app/`, `frontend/src/`, `tests/fixtures/` | `ls` shows all directories |
| S1-2 | Write `backend/pyproject.toml` with all dependencies | `pip install -e .` succeeds |
| S1-3 | Write `backend/app/models.py` — all dataclasses | `mypy --strict app/models.py` passes |
| S1-4 | Write `MeetingTime.conflicts_with()` unit tests | All 5 conflict scenarios pass |
| S1-5 | Write `parsers/catalog.py` | `pytest test_catalog_parser.py` passes golden file |
| S1-6 | Create `tests/fixtures/catalog_sample_10.csv` and expected JSON | Fixture covers all edge cases |
| S1-7 | Verify `parse_catalog(fall_2026.csv)` returns ~469 parent sections (all `is_parent` types) | Manual count confirms; 5 orphan children warned |

**End of Sprint 1 checkpoint:** Can parse the catalog and inspect normalized `CourseSection` objects in a Python shell.

---

### Sprint 2 — Audit Parsing and Requirements (Days 5–8)

| Task | Description | Acceptance |
|------|-------------|------------|
| S2-1 | Create `tests/fixtures/audit_anonymized.pdf` | No real PII; mirrors real structure |
| S2-2 | Create `tests/fixtures/audit_anonymized_expected.json` | All expected fields correct |
| S2-3 | Write `parsers/audit.py` | `pytest test_audit_parser.py` passes golden file |
| S2-4 | Write `adapters/swarthmore/requirement_defs.py` | Returns 4 items for CS + 202304 |
| S2-5 | Write `core/requirements.py` | `build_requirement_status()` unit tests pass |
| S2-6 | Test `items_satisfied_by()` against real Fall 2026 sections | See Stage 3 acceptance criteria |

**End of Sprint 2 checkpoint:** Can parse both files and run `build_requirement_status()` on the result.

---

### Sprint 3 — Solver and Ranking (Days 9–14)

| Task | Description | Acceptance |
|------|-------------|------------|
| S3-1 | Write `core/solver.py` — candidate filter | All 10 exclusion rules implemented and tested |
| S3-2 | Write linked section expansion | CPSC 031 produces (parent, lab_A) tuples |
| S3-3 | Write backtracking solver | Returns ≥ 100 schedules for demo student in < 5s |
| S3-4 | Write pre-solver credit check | Demo student with 4 locked credits returns current reg |
| S3-5 | Write Hypothesis property tests | All invariants hold across 1000 random inputs |
| S3-6 | Write `core/ranker.py` | Three distinct archetypes returned |
| S3-7 | Write `core/diagnostics.py` | Diagnostic generated when solver returns 0 |

**End of Sprint 3 checkpoint:** Can run the full pipeline (minus API and AI) and inspect ranked schedules in a Python shell.

---

### Sprint 4 — API and Frontend (Days 15–24)

| Task | Description | Acceptance |
|------|-------------|------------|
| S4-1 | Write Pydantic response models | All fields correctly typed and serialized |
| S4-2 | Write `main.py` with three routes | API integration tests pass |
| S4-3 | Set up Next.js project with Tailwind + shadcn | `npm run dev` starts without errors |
| S4-4 | Build `UploadForm` component | Accepts PDF + CSV, shows validation errors |
| S4-5 | Build `AuditSummary` component | Shows all requirement status items |
| S4-6 | Build `PreferencesForm` component | All preference fields render and submit |
| S4-7 | Build `WeekCalendar` component | Renders lecture + lab blocks with distinct visual |
| S4-8 | Build `ScheduleCard` component | Shows calendar, requirement gains, score |
| S4-9 | Build `DiagnosticPanel` component | Renders reasons + relaxations |
| S4-10 | End-to-end browser test | Full flow works in Chrome |

**End of Sprint 4 checkpoint:** Full demo flow works end-to-end with the real files.

---

### Sprint 5 — AI Explanation and Deployment (Days 25–31)

| Task | Description | Acceptance |
|------|-------------|------------|
| S5-1 | Write `core/explainer.py` | Returns 2–3 sentence explanation per schedule |
| S5-2 | Integrate explainer into `POST /api/plan` | Three explanations returned in parallel |
| S5-3 | Display explanations in `ScheduleCard` | Shows text or empty state gracefully |
| S5-4 | Write `backend/Dockerfile` | `docker run` starts the server |
| S5-5 | Deploy to Railway | Health check returns 200 |
| S5-6 | Deploy frontend to Vercel | Production URL returns results for real files |
| S5-7 | Record demo video | Full flow recorded |

---

## 5. Coding Standards

### Python

**Imports:** Standard library first, then third-party, then local. One blank line between groups. Absolute imports only (no relative `from ..` unless within the same package).

**Type annotations:** All functions and methods have full type annotations. `mypy` with `--strict` should pass on `app/` (add this to CI once Sprint 1 is done).

**Naming:**
- Classes: `PascalCase`
- Functions and methods: `snake_case`
- Constants: `SCREAMING_SNAKE_CASE`
- Private helpers: `_leading_underscore`
- Module-level variables that are session state: avoid (put in `main.py` only)

**Function size:** If a function doesn't fit in one screen (~40 lines), it's doing too much. Extract named helpers.

**Comments:** Only for non-obvious WHY, not WHAT. Never write "this function does X" when the function name already says X. Never write comments that reference the PR, the ticket, or the current task.

**Error handling:** Raise specific exceptions at parsing boundaries. Use `ValueError` for malformed input, `KeyError` for missing session. Don't catch broad `Exception` in `core/`. Catch broadly only in `main.py` route handlers, where all exceptions become HTTP error responses.

**`Decimal` usage:**
- All credit arithmetic uses `Decimal`
- Literals: `Decimal("1")` not `Decimal(1)` (avoids float precision issues)
- Comparisons: `total >= preferences.min_credits` (Decimal vs Decimal — safe)
- Never: `total + 1.0` or `Decimal(section.credits)` if `credits` is already `float`

### TypeScript / Next.js

**Types:** All API response shapes defined in `lib/api.ts` as TypeScript interfaces. No `any`. No `as unknown as T`.

**Components:** One component per file. Named exports only (not default exports for components — named exports are more refactor-safe).

**API calls:** All API calls go through `lib/api.ts`. No `fetch()` calls in components directly.

**State management:** Use React state for UI state. Pass session_id as a query parameter between pages (URL is the shared state between pages).

**Error handling:** API errors display in the UI with specific messages. Never swallow errors silently.

---

## 6. Testing Strategy

### Test Pyramid

```
             /\
            /  \
           / E2E\      (1-2 tests: full upload-to-results flow)
          /------\
         /  API  \     (test_api.py: route behavior, not business logic)
        /----------\
       / Integration\  (test_pipeline.py: full pipeline with fixture files)
      /--------------\
     /  Unit/Property \ (test_*.py: each module in isolation)
    /------------------\
```

Most tests should be unit tests. They are fast, specific, and catch regressions precisely.

### Golden File Tests (Parsers)

For `catalog.py` and `audit.py`:
1. Parse the fixture file
2. Serialize output to JSON
3. Compare against the expected JSON file byte-for-byte

When the expected JSON needs to change (e.g. because a parsing bug was fixed), update the expected file explicitly. This documents that "yes, we chose to change the output."

```python
def test_catalog_golden_file():
    result = parse_catalog("tests/fixtures/catalog_sample_10.csv")
    actual = serialize_sections(result)
    expected = json.loads(Path("tests/fixtures/catalog_sample_10_expected.json").read_text())
    assert actual == expected
```

### Property-Based Tests (Solver)

Use `hypothesis` to generate random `CourseSection` and `Preferences` objects. Assert invariants hold for every generated schedule. See Stage 4 for examples.

Configure Hypothesis database to reproduce failures:
```python
settings(database=DirectoryBasedExampleDatabase(".hypothesis"))
```

### Unit Tests (Requirements, Ranker, Diagnostics)

Pure function tests. No file I/O. Use synthetic domain objects, not fixture files. Keep them fast (< 1ms per test).

### Integration Tests (Pipeline)

One test per major scenario:
- Demo student + full catalog → generates 3 ranked schedules
- Demo student + full catalog + `lock_preregistered=True` → schedules avoid preregistered courses
- Demo student + restricted preferences → diagnostic generated

These use the real fixture files. They are slower but catch cross-component bugs.

### What NOT to Test

- The `ExplainerInput` prompt text (too brittle — model output changes)
- CSS or visual layout (not unit-testable)
- Anthropic API availability (tested in deployment, not unit tests)
- Database behavior (there is no database)

---

## 7. Technical Debt Policy

**Definition:** Technical debt is anything that makes the code harder to understand or change in the future.

**Rule:** Every shortcut must be documented with a `TODO` comment that includes:
- What the shortcut is
- Why it was taken (not "we ran out of time" — that's always true; write the real constraint)
- What the correct solution would be
- Which invariant it violates (if any)

**Format:**
```python
# TODO(tech-debt): CPSC 031 re-inclusion is currently implemented as a post-filter
# step that adds courses back after the initial exclusion pass. The cleaner approach
# would be a single-pass filter with explicit logic for each exclusion rule.
# Violates no invariant but makes the filter harder to read.
```

**Acceptable shortcuts:**
- Synchronous explainer calls instead of streaming (acceptable for MVP; streaming is a UX improvement)
- No session TTL (acceptable for demo; real deployment needs LRU eviction)
- No prerequisite enforcement (accepted scope decision, not a shortcut)
- Simple workload balance heuristic (the scoring formula is approximate; a better one can be added later)

**Unacceptable shortcuts:**
- Business logic in `main.py` (use `core/` instead)
- Parsing logic in `core/` (use `parsers/` instead)
- Floats for credits anywhere in the engine
- Hardcoded student data in the solver or ranker
- Mocking `core/` functions in integration tests

---

## 8. Long-Term Maintenance Strategy

### What Changes First

In order of likelihood after MVP:

1. **Swarthmore requirement definitions change** — add a new `(major, catalog_year)` key to `requirement_defs.py`. No engine changes needed.
2. **New semester's catalog** — a new CSV upload. Parser unchanged.
3. **Degree Works PDF format changes** — audit parser needs updates. Golden file test fails immediately.
4. **Second major supported** — add a new file in `adapters/swarthmore/`. Core engine unchanged.
5. **Second university** — add `adapters/new_university/`. Core engine unchanged.
6. **Multi-semester planning** — significant solver changes. The architecture anticipates this with the adapter isolation pattern.

### Protecting the Core

Every feature addition should be evaluated: does it require changes to `core/`? If yes, why? Changes to `core/` have the broadest blast radius. Changes to `adapters/` are isolated. Changes to `parsers/` affect only their own golden file tests.

### Updating This Document

When a significant implementation decision diverges from this guide, update this guide before closing the task. The guide should reflect what was built, not what was planned.

When a milestone is completed, mark it in the sprint table. When an invariant is discovered to be wrong (it happens), update `INVARIANTS.md` with the date and rationale.

---

## 9. Claude Code Collaboration Guidelines

These guidelines exist so that each new Claude Code session can pick up where the last one left off without repeating decisions or undoing work.

### At the Start of Every Session

1. Read the memory files in `.claude/projects/.../memory/` — they contain context that isn't in the code
2. Read `BASELINE.md` Section 20 (Locked Decisions) — don't reopen these
3. Check `INVARIANTS.md` — confirm you know the rules before writing code
4. Run `git log --oneline -10` — understand where implementation is
5. Run `pytest` — confirm the current state of the test suite before making changes

### What Belongs in BASELINE.md vs. IMPLEMENTATION_GUIDE.md vs. INVARIANTS.md

| Artifact | Contains |
|----------|----------|
| `BASELINE.md` | Architecture decisions, domain model definitions, API contracts, parser strategy, milestones |
| `INVARIANTS.md` | Rules that must not be broken; checked before any merge |
| `IMPLEMENTATION_GUIDE.md` | How to build it: rationale for ordering, coding standards, sprint tasks, testing strategy |
| Git commit messages | What changed and why for each commit |
| Code comments | Non-obvious WHY for specific lines or blocks |

### What to Avoid

- **Don't redesign what's already decided.** The architecture in BASELINE.md is locked. If implementation reveals a concrete problem, change BASELINE.md with a note explaining the reason, then implement the change.
- **Don't introduce new dependencies without justification.** The dependency list in `pyproject.toml` is intentionally minimal. Every new package has a maintenance cost.
- **Don't write business logic in `main.py`.** Route handlers are orchestration only.
- **Don't add features that aren't in the milestones.** Scope creep is the enemy of a clean MVP.
- **Don't skip tests to go faster.** Tests are part of the implementation, not optional polish.

### Handling Ambiguity

When implementation reveals something that BASELINE.md doesn't address:

1. Try to resolve it from first principles using the architecture as a guide
2. If the resolution requires a BASELINE.md change, make the change and note it in the commit message
3. If genuinely blocked, document the ambiguity in a `TODO` comment and move on — don't let one ambiguous edge case block the whole sprint

### Progress Tracking

Use `TodoWrite` for in-session task tracking. Mark tasks complete as soon as they're done. Each session should end with the test suite passing and no uncommitted changes.

---

## 10. Project Checklist

### Before Starting Any Stage

- [ ] `pytest` passes (no regressions from previous work)
- [ ] Current stage's dependencies (from roadmap above) are complete
- [ ] Relevant sections of BASELINE.md have been read

### Before Committing

- [ ] `mypy --strict app/` passes
- [ ] `pytest` passes
- [ ] No new `TODO` without an explanation
- [ ] No `print()` statements left in production code (use logging)
- [ ] No hardcoded paths, API keys, or student data outside of test fixtures

### Before Closing a Milestone

- [ ] All acceptance criteria from sprint table are met
- [ ] Golden file tests updated if fixture format changed
- [ ] BASELINE.md and this guide updated if anything significant diverged
- [ ] Sprint table entry marked complete

### Before Deploying

- [ ] `degreeauditexample.pdf` not in git (`git ls-files | grep degreeaudit` returns nothing)
- [ ] `ANTHROPIC_API_KEY` set in Railway env, not in any committed file
- [ ] End-to-end test passes with real files on production URL
- [ ] Demo video recorded
