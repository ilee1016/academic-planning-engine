# Academic Planning Engine — Frontend

Next.js 16 (App Router) frontend for the Academic Planning Engine API.

See the [root README](../README.md) for project overview, architecture, and privacy details.

## Pages

| Route | Purpose |
|-------|---------|
| `/` | Upload Degree Works audit PDF + course catalog CSV |
| `/planner` | Academic summary + preferences + section-ambiguity resolver |
| `/results` | Ranked schedule cards, weekly calendar, comparison, diagnostic |

## Development

```bash
npm install
cp .env.example .env.local
npm run dev          # http://localhost:3000
```

`.env.example` is pre-configured for a local backend on port 8000.

## Quality gates

```bash
npm run lint
npm run typecheck
npm test             # 77 tests (Vitest + React Testing Library)
npm run build
```

## Key conventions

- All API calls go through `lib/api.ts` — components never call `fetch()` directly
- Types in `lib/types.ts` are snake_case, matching FastAPI responses exactly
- Session ID only is stored in `sessionStorage` — no student identity persists in the browser
- `useState` initializers read from `sessionStorage` synchronously (avoids `setState-in-effect` lint rule)
- `cn()` from `lib/schedule.ts` combines `clsx` + `tailwind-merge`
