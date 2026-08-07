# Academic Planning Engine

A deterministic academic planning system that parses Swarthmore Degree Works audit PDFs and course catalog CSVs, generates every valid semester schedule via backtracking constraint enumeration, ranks the results against degree requirements and student preferences, and optionally explains the top recommendations with an LLM.

## Demo

> **Live app:** _[deploy to get URL — see [Deployment](#deployment)]_
>
> Upload `backend/tests/fixtures/audit_synthetic.pdf` and `backend/tests/fixtures/catalog_demo.csv` to run a full demo with fictional student data.

## Why I Built It

Scheduling a college semester means simultaneously satisfying degree requirements, avoiding time conflicts, managing credit loads, and respecting personal preferences (free Fridays, preferred subjects, current pre-registration). Students typically do this manually in spreadsheets or by trial-and-error in the registrar system.

This project models the problem correctly — as a constraint satisfaction problem — and solves it automatically, then ranks solutions by how well they advance the student's specific degree requirements and stated preferences.

## What It Does

```
Degree Works PDF ──→ Audit Parser ─────┐
                                        │
Course Catalog CSV ─→ Catalog Parser ──┤
                                        ↓
                              Requirement Engine
                              (which requirements remain?)
                                        ↓
                              Candidate Filter
                              (drop completed/exempt/excluded courses)
                                        ↓
                            Precomputed Conflict Matrix
                                        ↓
                           Backtracking Solver
                           (enumerate all valid schedules)
                                        ↓
                              Score + Rank
                         (requirement gains, free days,
                          compactness, credit target,
                          preferred subjects)
                               ↙              ↘
                      Diagnostic View     Ranked Schedules
                      (if 0 found)        (top results + per-category)
                                               ↓
                                    Optional AI Explanation
                                    (lazy, per schedule, sanitized DTO)
                                               ↓
                                         FastAPI API
                                               ↓
                                          Next.js UI
```

## Architecture

### Backend (`backend/`)

- **`app/parsers/audit.py`** — PDF parser for Degree Works audits (`pdfplumber`). Extracts student record, completed courses, preregistered courses, requirement block status, exemptions, and exceptions.
- **`app/parsers/catalog.py`** — CSV parser for the registrar course catalog. Normalizes section types (Course, Lab, Drill, Language Course, FY Seminar, etc.), deduplicates multi-meeting rows, links child sections to parents, warns on orphans.
- **`app/core/requirements.py`** — Requirement evaluation engine. Matches catalog sections against `RequirementItem` definitions by course code, subject predicate, or distribution attributes.
- **`app/adapters/swarthmore/requirement_defs.py`** — Swarthmore CS major requirement definitions for catalog year 202304. Isolated in the adapter layer; core engine never imports from adapters.
- **`app/core/solver.py`** — Precomputes a conflict matrix, expands courses into `SelectionOption` units (parent + linked sections), then enumerates valid combinations up to a configurable cap (default: 500 schedules).
- **`app/core/ranking.py`** — Scores each schedule across five components, assigns categorical archetypes (`requirements_first`, `preferred_subjects`, `compact_schedule`, `balanced`, `current_registration`), and returns all schedules ranked.
- **`app/core/diagnostics.py`** — When the solver finds zero valid schedules, runs 11 priority checks to identify which constraint(s) are infeasible and suggests relaxations.
- **`app/core/explainer.py`** — Sanitized `ExplainerInput` DTO (no student identity), deterministic fallback explanation generator, and response validator. Validated text must not contain student identity, completion language, Markdown tables, or unknown course codes.
- **`app/explanation_provider.py` / `app/explanation_service.py`** — Optional Anthropic provider with configurable timeout, LRU cache, and automatic fallback. Provider name `none` disables external calls entirely.
- **`app/main.py`** — FastAPI app: CORS from `FRONTEND_ORIGIN` env, upload validation, pipeline orchestration, error translation, session lifecycle.
- **`app/session_store.py`** — Thread-safe in-memory session store with TTL (2 h). **Single-worker only** — multiple workers or replicas would not share sessions.

### Frontend (`frontend/`)

Next.js 16 (App Router), TypeScript strict, Tailwind CSS 4, Vitest + React Testing Library.

- **`/`** — Upload landing page. Drag-and-drop for audit PDF and catalog CSV. Validates extensions client-side, creates session, uploads files, stores only `session_id` in `sessionStorage`.
- **`/planner`** — Academic summary (credit progress, requirement counts) + preferences form (credit range, free days, time window, preferred subjects, excluded courses, current-registration lock). Handles 409 section-ambiguity via `LockedSectionResolver`.
- **`/results`** — Ranked schedule cards with weekly calendar, score breakdown, requirement gain labels, and lazy AI explanation. Comparison table (up to 3 schedules). Diagnostic view when no schedules found.

## Technical Highlights

- **`Decimal` credits** — no floating-point drift on credit totals
- **`pdfplumber` audit parser** — line-by-line section boundary detection, right-to-left field parsing
- **Requirement adapter isolation** — `core/` never imports from `adapters/`; adapter produces plain domain objects
- **Precomputed conflict matrix + `itertools.combinations`** — simpler and equivalently correct to backtracking; runs the full Fall 2026 catalog (~860 rows → 500 schedules) in ~5 ms
- **Linked-section semantics** — Labs and drills are atomic with their parent; the conflict matrix handles them correctly
- **Categorical ranking** — schedules are grouped into meaningful archetypes, not just sorted by score
- **Constraint diagnostics** — 11-priority diagnostic check explains _why_ no schedule was found and suggests specific relaxations
- **Sanitized AI explanation** — `ExplainerInput` contains no student identity, no raw audit text, no prompt; validated output rejects completion language and identity patterns
- **Lazy explanation loading** — AI calls happen only when the user requests an explanation for a specific schedule
- **Privacy-by-design frontend** — only `session_id` persists in `sessionStorage`; student name and ID are never returned to the browser

## Correctness

| Layer | Test count | Method |
|-------|-----------|--------|
| Backend | **612 tests** | Unit, property-based (Hypothesis), integration, golden-file |
| Frontend | **77 tests** | Vitest + React Testing Library |

- `mypy --strict` on all 20 backend source files
- Property-based solver tests assert every generated schedule satisfies all hard constraints for randomly generated inputs
- Golden-file parser tests validate exact output against committed fixtures
- Privacy regression tests assert student name and ID are absent from all API responses

## Scope

The current MVP supports **Swarthmore College** with the **CS major, catalog year 202304** requirement model. Other majors and institutions require adding a new `adapters/` module.

## Limitations

- Solver cap: up to 500 schedules enumerated; result is the highest-ranked subset, not a global optimum guarantee
- Single-semester planning only
- Prerequisite enforcement is a warning (display only), not a hard constraint
- FY Seminars are excluded for all students (catalog year vs. graduation year limitation)
- Seminar1/Seminar2 double-graded pairs are excluded from the candidate pool
- Orphan lab sections (no parent course in catalog) are excluded with a warning
- Session state is in-memory; server restart or redeployment invalidates active sessions
- Multiple backend replicas are not supported (would break session lookup)

## Running Locally

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
pip install pytest hypothesis mypy

uvicorn app.main:app --reload --port 8000
# API docs at http://localhost:8000/docs
```

Environment variables (optional for local dev — defaults shown):

```bash
FRONTEND_ORIGIN=http://localhost:3000
EXPLANATION_PROVIDER=none
# ANTHROPIC_API_KEY=        (only if EXPLANATION_PROVIDER=anthropic)
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local        # already configured for local backend
npm run dev
# Opens at http://localhost:3000
```

## Testing

```bash
# Backend (from backend/)
pytest                             # 612 tests
mypy --strict app/

# Frontend (from frontend/)
npm test                           # 77 tests
npm run lint
npm run typecheck
npm run build
```

## Deployment

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for Railway (backend) and Vercel (frontend) deployment instructions.

Quick reference:
- Backend root directory: `backend/`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
- Required env var: `FRONTEND_ORIGIN=<vercel-url>`
- Frontend root directory: `frontend/`
- Required env var: `NEXT_PUBLIC_API_URL=<railway-backend-url>`

## Privacy

- Uploaded files are processed in memory and discarded immediately after parsing — raw bytes are never stored
- Student name and ID are extracted from the audit for requirement matching but are **never returned in any API response**
- The AI explanation layer receives a sanitized `ExplainerInput` DTO: schedule metadata only, no student identity, no audit text, no raw prompt
- Only `session_id` is persisted in browser storage; the session expires server-side after 2 hours
- No user database, no analytics, no persistent logging of student data

## Future Work

- Multi-semester / four-year planning
- Additional degree programs and institutions
- Prerequisite enforcement as a hard constraint
- Persistent saved plans
- Calendar export (iCal / Google Calendar)
- Resident-memory session store → Redis for horizontal scaling
