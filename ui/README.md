# Qontextually UI

Human-in-the-loop review console for the Qontextually graph. Built with TanStack Start (React + Vite) + TailwindCSS + shadcn/ui. Talks to the FastAPI backend in `../lib/api.py`.

Originally generated in [Lovable](https://lovable.dev) and lives at <https://github.com/b3ll9trix/qontextual-navigator>. This directory is a vendored snapshot for one-clone demo use; updates flow from the upstream repo.

## Run it

From the repo root:

```bash
make api      # backend on http://127.0.0.1:8000
make ui-dev   # UI on http://localhost:5173 (or whatever Vite picks)
```

Or directly inside this directory:

```bash
bun install && bun run dev   # bun preferred (matches bun.lockb)
# or
npm install && npm run dev   # npm fallback (package-lock.json is present)
```

Set `VITE_API_URL` to point at a non-default backend; defaults to `http://localhost:8000`.

## Screens

Dashboard · Conflicts · Vocabulary · Entities · Sources · Graph (3D force-directed). The full API contract this UI was built against is in the top-level `README.md`.
