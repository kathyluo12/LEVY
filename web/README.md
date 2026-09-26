# LEVY

Tariff intelligence agent desk. Built with Lovable.

The UI renders a deterministic demo out of the box and upgrades to live data when
it can reach the LEVY FastAPI agent service. The demo never breaks: any missing,
unreachable, or malformed response falls back to the built-in demo snapshot.

## Data flow

- **Server (SSR loader):** `src/routes/index.tsx` calls `loadLevySnapshot()` in
  `src/lib/levy-source.server.ts`, which fetches `GET {LEVY_API_URL}/v1/snapshot`.
  `LEVY_API_URL` alone is enough to go live; `LEVY_API_KEY` is optional and the
  `Authorization: Bearer` header is only sent when it is set.
- **Client (live refresh):** `src/hooks/use-live-snapshot.ts` opens an
  `EventSource` to `{VITE_LEVY_API_URL}/api/stream` and, on
  `belief` / `evidence` / `job` / `lesson` / `resolution` events, debounces and
  refetches `{VITE_LEVY_API_URL}/v1/snapshot`. On any error it retains the last
  good (server) snapshot. With no `VITE_LEVY_API_URL`, the loader data stands and
  the hook is a no-op.

## Environment

Copy `.env.example` to `.env` and fill in the URLs:

| Variable            | Scope           | Purpose                                                        |
| ------------------- | --------------- | -------------------------------------------------------------- |
| `LEVY_API_URL`      | Server only     | SSR snapshot fetch (`GET {base}/v1/snapshot`).                 |
| `LEVY_API_KEY`      | Server only     | Optional bearer token. Blank = unauthenticated service.       |
| `VITE_LEVY_API_URL` | Client (public) | Live EventSource + client refetch base URL. Public URL only.  |

> Security: only `VITE_`-prefixed variables reach the browser. Never place a
> MongoDB Atlas URI, model API key, or any secret in a `VITE_` variable or in the
> frontend — those live exclusively in the agent service. `LEVY_API_KEY` is
> read server-side and is never shipped to the client.

`.env` and other `.env.*` files are git-ignored; `.env.example` is tracked.

## Local development

```bash
bun install          # install dependencies (bun.lock is the source of truth)
cp .env.example .env # point LEVY_API_URL / VITE_LEVY_API_URL at your service
bun run dev          # start the dev server
```

Run the FastAPI agent service (from the `MangoDB_LEVY` project) on
`http://localhost:8000` so `/v1/snapshot` and `/api/stream` are reachable. Without
a running service the UI simply shows the demo snapshot.

Useful scripts:

```bash
bun run lint         # eslint .
bun run build        # production build
bun run preview      # preview the production build
```

(If you use npm instead of bun, substitute `npm install` / `npm run <script>`.)

## Deployment

1. Set `LEVY_API_URL` (and `LEVY_API_KEY` if the service requires auth) in the
   server environment so the SSR loader can reach the agent service.
2. Set `VITE_LEVY_API_URL` at **build time** to the public URL of the agent
   service so the client bundle streams live updates. Rebuild when it changes.
3. Ensure the agent service enables CORS for the UI origin and exposes both
   `/v1/snapshot` and `/api/stream`.
4. Keep all database and model credentials on the agent service only.
