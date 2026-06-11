# Frontend Team Guide

> Read this end-to-end before writing or moving any code. It tells you what to build, how to coordinate, and the small set of rules that keep the codebase coherent.

---

## You are 2 people. Here's how to split.

Two frontend devs working in parallel — split by **feature**, not by layer (don't have one person do "all the API plumbing" while the other does "all the UI"; that creates merge hell). Suggested split for the POC (Milestone 2):

| Person A | Person B |
|---|---|
| **Login** (`src/features/auth/`) | **Risk Manager** (`src/features/risk-manager/`) |
| **Dashboard** (`src/features/dashboard/`) | **Kill Switch & Alerts** (`src/features/kill-switch/`) |
| **Venue Connectivity** (`src/features/venue-connectivity/`) | The single WebSocket client + Zustand stores in `src/stores/` and `src/services/` |
| TanStack Query setup + the REST `ApiClient` in `src/api/` | Shared UI bits in `src/components/` (StatusBadge, LoadingSpinner, layout shell) |

If you'd rather split differently, fine — the rule is: **each merged PR touches one feature folder + at most one shared file.** That keeps reviews trivial and merges painless.

---

## Step 0 — Drop in the Figma Make output (one-time, by Fizza)

The `frontend/` folder is empty right now except for `README.md` and this guide. Here's how to bring in the React project from Figma Make without polluting anything:

1. From Figma Make, export the React project (the entire folder it gives you: `src/`, `public/`, `package.json`, `pnpm-lock.yaml` if present, `vite.config.ts`, `tailwind.config.ts`, `tsconfig.json`, `index.html`, `.eslintrc.*`, `postcss.config.*`).
2. On a **fresh branch** named `feature/figma-frontend-import`, copy all those files **into this `frontend/` directory** (next to `README.md` and `TEAM_GUIDE.md`).
3. **Do not flatten** the folder structure — keep `src/`, `public/`, etc. as-is.
4. If the Figma Make output uses a different folder convention than `src/features/<feature>/` (e.g., `src/pages/` or `src/screens/`), don't reshape it yet — just import it as-is. We'll align with `project-info/FRONTEND_GUIDE.md` §3 in a separate PR. (Two changes in one PR = unreviewable.)
5. Commit message: `feat(frontend): import Figma Make output`.
6. Push, open PR, let one teammate skim the file list (not the code — too much) and merge.

After merge, every other frontend change is a normal small PR.

---

## Step 1 — Wire up the dev environment

```powershell
cd frontend
pnpm install
pnpm dev
```

Open http://localhost:5173. You should see whatever Figma Make put at the root route.

Create `frontend/.env.local` (NOT committed — `.gitignore` already covers it):

```env
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws
```

The backend won't exist yet on day 1. Hit `http://localhost:5173` and confirm the UI renders with mock data. **Do not delete the mock data** until you replace it with a real API call — the screens have to keep working during the wiring phase.

---

## Step 2 — Build the API client + WebSocket client

These two files are the only "exits" from the React layer. Every component reads from them, nothing else.

### `src/api/client.ts` — REST

Copy the pattern from [`../project-info/FRONTEND_GUIDE.md`](../project-info/FRONTEND_GUIDE.md) §5. Single `ApiClient` class. JWT goes in the `Authorization` header. Errors throw a typed `ApiError`.

### `src/services/websocket.ts` — Real-time

One connection per app session at `${VITE_WS_URL}`. Multiplexed — every push has a `type` field. The client routes messages to the right Zustand store. Pattern: [`../project-info/FRONTEND_GUIDE.md`](../project-info/FRONTEND_GUIDE.md) §2 "WebSocket Message Router."

**Reconnection logic** — required, not optional. Backend will restart often during dev. Implement exponential backoff: 1s → 2s → 4s → 8s, capped at 30s. Clear all stores on reconnect (don't show stale data).

---

## Step 3 — Zustand stores (real-time data only)

Each WebSocket message type maps to one store:

| Store | What it holds | Fed by WS message type |
|---|---|---|
| `useVenueStore` | venue health per venue | `venue_health` |
| `useMarketDataStore` | quotes per venue per symbol + NBBO | `market_data`, `nbbo_update` |
| `useOrderStore` | live orders + recent fills | `order_update`, `execution_report` |
| `useRiskStore` | kill switch status + alerts | `risk_alert`, `kill_switch` |

Rule: **REST goes through TanStack Query, real-time goes through Zustand.** Don't mix. A component can read from both — merge in the component, not in the store.

---

## Step 4 — POC screens (5)

Per [`../project-info/MILESTONES.md`](../project-info/MILESTONES.md) "Frontend Screens" table — these five must work end-to-end for the M2 demo:

1. **Login** — POST `/api/auth/login`, store JWT, redirect to Dashboard.
2. **Dashboard** — venue dots, key metrics (orders today, fill rate, avg latency), recent orders, system health bar.
3. **Venue Connectivity** — venue card per venue with health score, latency, fill rate. V3 must visibly show **BLACKLISTED**.
4. **Risk Manager** — current positions, notional exposure, risk limits, recent pre-trade check results.
5. **Kill Switch & Alerts** — big confirmation-required kill-switch button, real-time alert feed, kill-switch activation history.

The other 13 screens (Order Blotter, Market Data, Routing Engine, etc.) are deferred to M3. **Don't build them now**, even partial — they'll get the design polish in the next phase.

---

## Step 5 — Replace mock data, per screen

For each POC screen, two parallel data paths:

```typescript
// 1. REST fallback / initial load — TanStack Query
const { data: venues } = useQuery({
  queryKey: ["venues"],
  queryFn: () => api.getVenues(),
  refetchInterval: 5000,
});

// 2. Real-time updates — Zustand
const liveVenues = useVenueStore((s) => s.venues);

// 3. Merge — live wins when available
const display = Object.keys(liveVenues).length > 0
  ? Object.values(liveVenues)
  : (venues ?? []);
```

If the backend endpoint isn't ready yet, mock it in the API client temporarily — but **mark it with `// TODO: remove when backend lands`** so we don't ship mocks accidentally.

---

## Conventions (non-negotiable)

These come from `../CLAUDE.md` and [`../project-info/FRONTEND_GUIDE.md`](../project-info/FRONTEND_GUIDE.md). Breaking them = PR rejected.

- **Strict TypeScript** — `"strict": true`. No `any`. Use `unknown` and narrow.
- **Functional components only.** No classes.
- **Named exports only.** Default exports only for route pages.
- **No business logic in components.** Logic goes in hooks, stores, or the API client.
- **Feature isolation** — a feature folder NEVER imports from another feature's `components/`. If two features need the same thing, move it to `src/components/`.
- **No custom CSS files.** Tailwind utilities only. Global tokens (background, text, accent colors) are defined in `tailwind.config.ts`.
- **No `localStorage`** without a wrapper hook that's aware of SSR (we're not doing SSR, but the hook contract is what enforces test-ability).
- **Monospace font for all numbers** — prices, quantities, latency, percentages. Inter for body text.
- **Green for positive / healthy, red for negative / critical, yellow for degraded, blue for info.** Defined in [`../project-info/FRONTEND_GUIDE.md`](../project-info/FRONTEND_GUIDE.md) §6.
- **Timestamps in UTC**, `HH:mm:ss.SSS` for real-time, `YYYY-MM-DD HH:mm` for historical.
- **Numbers update in place** — never trigger a layout shift on a value change.
- **Dense tables.** Compact row heights — traders need to see 30+ rows.
- **Error states inline**, never as a modal popup.

---

## Coordinating with the backend (= the user / Sarmad)

When you need an endpoint that doesn't exist yet, or want to change a response shape:

1. **Read the spec first** — [`../project-info/API_SPECIFICATION.md`](../project-info/API_SPECIFICATION.md) is the source of truth. If your need fits an existing endpoint, just use it.
2. If the spec needs to change, **open an issue or message Sarmad before writing the code.** Don't unilaterally invent a new endpoint shape and rely on him to retrofit — that creates rework on both sides.
3. If you're blocked waiting for an endpoint, **mock it in the API client** with a `// TODO: backend pending` comment. Keep moving.

WebSocket message types follow the same rule. The catalogue is in [`../project-info/API_SPECIFICATION.md`](../project-info/API_SPECIFICATION.md) §10 and [`../project-info/BACKEND_GUIDE.md`](../project-info/BACKEND_GUIDE.md) §5.

---

## Coordinating with each other

- **Daily 5-min sync** — what each of you touched yesterday, what you'll touch today. Catches "we both edited `Sidebar.tsx`" before it becomes a merge conflict.
- **One PR per feature**, max. If a PR feels like "this should really be two changes," split it.
- **Review each other's PRs** before merging. Even a 30-second skim catches obvious problems.
- **Branch naming** — `feature/<screen-name>` or `feature/<thing>`. Examples: `feature/dashboard-wiring`, `feature/kill-switch-confirmation-modal`, `feature/websocket-reconnect`.

---

## When you push to GitHub

- Commits: conventional commits — `feat(frontend):`, `fix(frontend):`, `refactor(frontend):`, `docs(frontend):`. Scope is always `frontend` so backend folks can filter.
- PR title: under 70 chars. PR body: what + why + how-to-test (3 short paragraphs).
- After every meaningful change, add a one-paragraph entry to `../DEVELOPMENT_JOURNAL.md` explaining what and why, dated. Bullet list of files changed at the end of the entry. This is for the team and supervisors, not git.

---

## Common gotchas

- **The single WebSocket connection is shared across screens.** Don't open a second connection from a feature folder. If you think you need to, you don't — talk to whoever built the WS client.
- **Zustand selectors must be stable.** `useStore(s => s.venues)` is fine; `useStore(s => Object.values(s.venues))` recomputes every render and causes infinite loops in some cases. Memo it with `useMemo` or use `useStore(s => s.venues, shallow)`.
- **TanStack Query keys must be stable arrays.** `["venues"]` not `["venues", new Date()]`.
- **JWT expires after 60 min** per backend config. The API client should refresh transparently using the refresh token on 401. Don't make every screen handle 401 individually.
- **V3 (GammaMarkets) shows BLACKLISTED.** That's not a bug — it's the demo scenario for the venue-blacklist use case. Show the status correctly; don't hide V3.

---

## Open questions to raise with Sarmad

Drop these into your daily sync if they're still open by then:

- Symbol universe for the POC demo — single ticker (e.g. AAPL only) or a small basket?
- Are there sequence diagrams from M1 that the screens should match?
- Sidebar order — does the screen order in `Sidebar.tsx` match the order in `MILESTONES.md` "Frontend Screens" table, or a different one?
