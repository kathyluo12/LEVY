# LEVY — Long-Horizon Tariff Agent Desk (backend)

LEVY is a standing desk of AI agents that watches the U.S. tariff pipeline,
remembers what it believed and why, and publishes calibrated tariff
probabilities with confidence grades (A–D) per country. This repository contains
the **backend only** (agent runtime + FastAPI gateway). No UI.

The backend runs in two modes:

- **Offline (default):** fully in-memory persistence and deterministic
  Jev/LLM/Web adapters. No network access or MongoDB required. This mode powers
  the test suite and local demos.
- **Production:** async MongoDB (Motor) persistence and live OpenRouter (Jev +
  chat), Firecrawl and Tavily adapters. Enabled by `LEVY_OFFLINE=false` plus the
  relevant API keys in the environment.

## Requirements

- Python >= 3.12 (compatible with 3.12 / 3.13)
- Dependencies are pinned in `pyproject.toml`

## Install (do this yourself; packages are not installed for you)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

All secrets are read from environment variables (prefix `LEVY_`). Copy the
example and fill in what you need:

```bash
cp .env.example .env
```

Key settings:

| Variable | Meaning |
| --- | --- |
| `LEVY_OFFLINE` | `true` for in-memory/deterministic mode (default) |
| `LEVY_MONGO_URI` / `LEVY_MONGO_DB` | Mongo connection (production only) |
| `LEVY_OPENROUTER_API_KEY` | Jev + chat model access |
| `LEVY_FIRECRAWL_API_KEY` / `LEVY_TAVILY_API_KEY` | Web research |
| `LEVY_CORS_ORIGINS` | Comma-separated allowed origins |
| `LEVY_SEED_ON_START` | Seed fixtures during API startup |

## Atlas sandbox

The verified hackathon sandbox setup, validation evidence, and deployment
checklist are documented in [`docs/ATLAS_SANDBOX.md`](docs/ATLAS_SANDBOX.md).

## Run

Console scripts are declared in `pyproject.toml`:

```bash
levy-seed      # idempotent seed of questions/events/exposures/ticks
levy-api       # FastAPI gateway (uvicorn)
levy-scouts    # APScheduler scout worker
levy-agents    # leased-job consumer running the forecast pipeline
```

Or directly, e.g.:

```bash
python -m levy.workers.api
uvicorn levy.api.main:app --reload
```

## API endpoints

Public UI integration (outside the `/api` prefix):

- `GET /v1/snapshot` — the full `LevySnapshot` contract consumed by the
  frontend (`LEVY_UI/src/lib/levy-source.server.ts`): camelCase `questions`,
  `replay`, `calibration`, `resolved`, `brierScore`, `source="atlas"`, plus an
  optional dynamic `stats` object. Ensure the frontend origin is present in
  `LEVY_CORS_ORIGINS` so browser requests are not blocked.

Read:

- `GET /health`, `GET /api/health`
- `GET /api/board`
- `GET /api/countries/{iso}`
- `GET /api/questions/{key}/timeline`
- `GET /api/questions/{key}/runs/latest`
- `GET /api/evidence?since=`
- `GET /api/agents/status`
- `GET /api/learning`
- `GET /api/exposures/{iso}`
- `GET /api/stats`
- `GET /api/stream` (SSE: `belief`, `evidence`, `job`, `lesson`, `resolution`,
  plus `heartbeat`)

Write / demo:

- `POST /api/resolutions/{key}/confirm`
- `POST /api/demo/replay`
- `POST /api/demo/inject`

## Architecture

```
levy/
  settings.py          env-only settings, offline flag
  schemas.py           Pydantic domain documents (stable JSON)
  seed.py              idempotent seeding + CLI
  core/
    collections.py     18 TDD stores + required exposures + capped logs
    db.py              Repository protocol, InMemory + Mongo (lazy) impls
    jev.py             Jev Decisions API adapter (typed norm + fallback)
    llm.py             OpenRouter chat adapter (routing, budgets, offline)
    web.py             Firecrawl/Tavily adapter (cache, budgets, offline)
    jobs.py            leased jobs, idempotency keys, debounce
    events.py          in-process event bus (SSE fan-out)
    memory.py          deterministic embeddings, lesson recall, base rates
    calibrate.py       Platt + isotonic (PAV), bounded [0.01, 0.99], no sklearn
    clock.py           SimClock, document/prompt cutoffs, leak probe
  agents/
    scouts.py          roster + scheduler metadata + ingestion
    triage.py          Jev triage thresholds -> evidence / rejected
    analysts.py        legal/political/counterparty/base-rate briefs
    question_factory.py draft questions from unmatched evidence
    forecast.py        5 forecasters + red team + supervisor + calibrator
    exposure.py        sector/FX/ticker exposure + market divergence
    resolution.py      resolution clerk + Brier/log scoring
    learning.py        reflector lessons + calibration refit + leaderboard
    status.py          agent status + desk stats
    pipeline.py        end-to-end orchestration
    replay.py          replay scenario engine (isolated repo, leak probe)
  api/
    main.py            FastAPI app factory + lifespan + CORS
    routes.py          all endpoints
    sse.py             SSE stream with heartbeats + cleanup
    deps.py            repo/bus dependencies
  workers/
    api.py scouts.py agents.py   process entry points
  data/
    questions.yaml     ~20 forecast questions
    events.csv         historical reference events
    replay/            canada_338, section_122_expiry, china_truce_extension
  tests/               offline test suite (no network, no Mongo)
```

## Self-improvement

- **Calibration refit:** Platt scaling for few resolutions, monotonic isotonic
  (PAV) for enough data; outputs clamped to `[0.01, 0.99]`; a neutral map nudges
  predictions away from 0.5 before data exists.
- **Forecaster re-weighting:** softmax of negative Brier with a floor.
- **Lessons:** the Reflector writes at most one lesson per resolution, verified
  by Jev, expiring after 90 days, recalled by vector similarity.

## Replay & leakage defenses

Replay drives a `SimClock`, exposes only documents dated on/before the simulated
time, writes to an isolated `levy_replay_<scenario>` database, injects the
simulated date into prompts, and runs a leak probe on rationales for references
to later dates. Replay scores are labelled *in-sample demonstration*.

## Tests

```bash
pytest
```

The suite is offline-only and never requires network access or MongoDB. Required
tests: `test_jev_adapter`, `test_calibration`, `test_replay_leakage`,
`test_jobs`, plus route contracts and pipeline coverage.

Prices and API details reflect the design doc (Sept 2026); verify before use.
Example values in fixtures are placeholders, not forecasts.
