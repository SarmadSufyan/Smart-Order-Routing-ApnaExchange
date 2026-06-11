# Frontend

React 18 + TypeScript + Vite + Tailwind + shadcn/ui. Dark-theme trading dashboard.

## Status: placeholder

The initial code was generated via **Figma Make** by Fizza. It hasn't been dropped into this folder yet.

### Drop-in instructions (for Fizza)

1. Export / copy the Figma Make output (the entire React project — `src/`, `public/`, `package.json`, `vite.config.ts`, `tailwind.config.ts`, `tsconfig.json`, `index.html`, etc.) into this directory.
2. Keep the folder structure from `../project-info/FRONTEND_GUIDE.md` §3 — `src/features/<feature>/`, `src/components/`, `src/stores/`, `src/api/`, `src/types/`, `src/lib/`.
3. Do **not** modify the visual design language — backend wiring is added on top without touching the look.
4. Commit on its own branch (`feature/figma-frontend-import`) so the import diff is reviewable.

### After drop-in

```powershell
cd frontend
pnpm install
pnpm dev
```

Open http://localhost:5173.

### Connecting to backend (post-drop-in)

Pattern is in [`../project-info/FRONTEND_GUIDE.md`](../project-info/FRONTEND_GUIDE.md) §7:

1. Add `.env` with `VITE_API_URL` and `VITE_WS_URL` (template in root `.env.example`).
2. Per screen, replace mock data with a TanStack Query call to `api.getX()`.
3. Subscribe to real-time updates via Zustand stores fed by the single multiplexed WebSocket.

## Conventions

- **Strict TypeScript** — no `any`, use `unknown` and narrow.
- **Named exports** only (except route pages).
- **Feature isolation** — a feature never imports from another feature's `components/`.
- **No business logic in components** — logic goes in hooks, stores, or the API client.
- **Tailwind utilities only** — no custom CSS files.

POC screens (from [`../project-info/MILESTONES.md`](../project-info/MILESTONES.md) §"Frontend Screens"): Login, Dashboard, Venue Connectivity, Risk Manager, Kill Switch & Alerts. The remaining 13 screens land in M3.
