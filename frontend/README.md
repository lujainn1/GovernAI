# GovernAI Frontend

React + Vite UI for the GovernAI platform. Talks directly to the FastAPI
backend (no separate Node/Express layer) — in dev, requests to `/api/*` are
proxied to `http://localhost:8000` (see `vite.config.js`); the backend also
has CORS enabled for `http://localhost:5173` if you call it directly.

## Pages

- **Reports** (`/`) — table of all governance reports with risk/decision/status badges
- **Submit use case** (`/submit`) — intake form; runs the full agent pipeline on submit
- **Report detail** (`/use-cases/:id`) — risk assessment, policy compliance, decision,
  and an approve/reject panel when a report is `pending_human_approval`

## Setup

```bash
npm install
npm run dev
# -> http://localhost:5173
```

The backend must be running separately (see the root [README](../README.md))
for the app to have any data to show.

## Scripts

| Command | Description |
|---|---|
| `npm run dev` | Start the Vite dev server with HMR |
| `npm run build` | Type/lint-free production build to `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run lint` | Run Oxlint |
