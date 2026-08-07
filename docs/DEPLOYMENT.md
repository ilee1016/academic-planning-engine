# Deployment Guide

## Architecture

```
Railway (backend) ←──── CORS: FRONTEND_ORIGIN ────→ Vercel (frontend)
```

Single-worker FastAPI backend on Railway. Next.js static/SSR frontend on Vercel.

> **Single-worker requirement:** Session state is in process memory. Do NOT enable multiple workers or multiple Railway replicas — sessions would not be shared and requests would fail randomly.

---

## Backend — Railway

### 1. Create a Railway project

1. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
2. Select this repository
3. Set **Root Directory** to `backend`
4. Railway auto-detects Python from `pyproject.toml`

### 2. Start command

Railway uses `Procfile` (already committed at `backend/Procfile`):

```
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
```

### 3. Environment variables (Railway dashboard → Variables)

| Variable | Value | Required |
|----------|-------|----------|
| `FRONTEND_ORIGIN` | `https://your-app.vercel.app` | Yes |
| `EXPLANATION_PROVIDER` | `none` or `anthropic` | No (default: `none`) |
| `ANTHROPIC_API_KEY` | your key | Only if provider=anthropic |
| `ANTHROPIC_EXPLANATION_MODEL` | `claude-haiku-4-5-20251001` | No (default shown) |
| `EXPLANATION_TIMEOUT_SECONDS` | `8` | No |
| `EXPLANATION_CACHE_SIZE` | `500` | No |

`EXPLANATION_PROVIDER=none` runs the full app with deterministic fallback explanations — no API cost, no external dependency.

### 4. Verify deployment

```bash
curl https://your-backend.railway.app/health
# Expected: {"status":"ok"}
```

OpenAPI docs (if enabled): `https://your-backend.railway.app/docs`

---

## Frontend — Vercel

### 1. Import project

1. Go to [vercel.com](https://vercel.com) → Add New Project → Import GitHub repo
2. Set **Root Directory** to `frontend`
3. Framework: Next.js (auto-detected)

### 2. Environment variables (Vercel dashboard → Settings → Environment Variables)

| Variable | Value | Environment |
|----------|-------|-------------|
| `NEXT_PUBLIC_API_URL` | `https://your-backend.railway.app` | Production |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Development (optional) |

Only `NEXT_PUBLIC_API_URL` belongs in Vercel. Do not add backend-only variables (`ANTHROPIC_API_KEY`, `EXPLANATION_PROVIDER`) — they must not reach the browser bundle.

### 3. Verify deployment

Open the Vercel preview URL. Upload the demo fixtures:
- Audit: `backend/tests/fixtures/audit_synthetic.pdf`
- Catalog: `backend/tests/fixtures/catalog_demo.csv`

---

## CORS

The backend reads `FRONTEND_ORIGIN` at startup. Set it to the exact Vercel deployment URL (including `https://`). Comma-separate multiple origins if needed:

```
FRONTEND_ORIGIN=https://academic-planner.vercel.app,https://academic-planner-git-main.vercel.app
```

Do not set `*`. The backend rejects wildcard origins by design.

---

## Session behavior in production

- Sessions are stored in process memory with a 2-hour TTL
- Railway redeployment invalidates all active sessions (users see a "session not found" error and must re-upload)
- This is expected and acceptable for a demo application
- Users with expired sessions are redirected to the upload page

---

## Production smoke test checklist

After both services are live, test over HTTPS with fictional files:

- [ ] Landing page loads
- [ ] Session creation succeeds (network tab: `POST /api/session` → 201)
- [ ] File upload succeeds (network tab: `POST /api/session/.../inputs` → 200)
- [ ] Academic summary displays (major, credits, requirements)
- [ ] Preferences form accepts input
- [ ] Schedule generation succeeds
- [ ] Section ambiguity resolver appears when demo audit has preregistered courses
- [ ] Weekly calendar renders for at least one schedule
- [ ] Requirement gains display in schedule card
- [ ] Score details expand (`<details>` element)
- [ ] "Why this schedule?" fetches explanation
- [ ] Fallback explanation shows for `EXPLANATION_PROVIDER=none`
- [ ] Impossible preferences (e.g., no days available) shows diagnostic view
- [ ] "Adjust preferences" navigates back to planner
- [ ] "Start over" clears session and returns to upload
- [ ] No CORS errors in browser console
- [ ] No runtime errors in browser console
- [ ] Network requests all use HTTPS
- [ ] `sessionStorage` contains only `session_id` (no audit text, no student identity)
- [ ] `/health` returns `{"status":"ok"}`

---

## Local development

```bash
# Terminal 1 — Backend
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# Terminal 2 — Frontend
cd frontend
npm run dev
# http://localhost:3000
```

`FRONTEND_ORIGIN` defaults to `http://localhost:3000` when unset. No env configuration needed for local dev.
