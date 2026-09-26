# Live Federal Register Ingestion

LEVY ingests live tariff/trade documents from the public
[Federal Register API](https://www.federalregister.gov/developers/documentation/api/v1).
No API key is required. The client identifies itself with a descriptive
`User-Agent` that includes a configurable contact string.

## API contract used

- Search: `GET https://www.federalregister.gov/api/v1/documents.json`
  - `conditions[term]`, `conditions[publication_date][gte]`,
    `conditions[publication_date][lte]`, `per_page`, `page`, `order=newest`
  - Response: `count`, `total_pages`, `next_page_url`, `results`
  - Result fields: `document_number`, `title`, `abstract`, `excerpts`,
    `agencies`, `type`, `publication_date`, `html_url`, `pdf_url`
- Detail: `GET /api/v1/documents/{document_number}.json`
  - Adds `raw_text_url`, `body_html_url`, `full_text_xml_url`, `topics`,
    `effective_on`, and other metadata (fetched only with `--include-full-text`).

## Safety and bounds

- `per_page` is clamped to ≤ 1000, `max_pages` ≤ 10, `max_documents` ≤ 500.
  The CLI applies smaller defaults (3 pages / 100 documents).
- Pagination uses explicit page numbers against the configured base URL. The
  client never follows an arbitrary `next_page_url` host.
- Future-dated documents are excluded by default; the end date is clamped to
  "today".
- Retries use bounded exponential backoff for timeouts, network errors, HTTP
  429 and 5xx, honouring `Retry-After` up to a 30s cap. Other 4xx fail clearly.
- HTML snippets are stripped to plain text and combined title + abstract +
  excerpts is bounded. Fetched full text is treated strictly as data — no
  scraped content is ever executed.
- The raw-item hash is keyed on `document_number`, so a re-published document
  with an edited abstract dedupes rather than creating a duplicate.

## Tariff/trade prefilter

Documents are kept when a documented term (e.g. `tariff`, `duty`, `customs`,
`antidumping`, `countervailing`, `section 301/232/338/122`, `ieepa`,
`safeguard`, `harmonized tariff`) appears in the title/abstract/excerpts/type,
OR when they are issued by a documented tariff/trade agency (USTR, Commerce /
International Trade Administration, International Trade Commission, CBP /
Homeland Security, Treasury). The filter is broad by design to retain genuine
trade-remedy notices and is unit-tested independently.

## CLI: `levy-register`

```
levy-register [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--days N]
              [--query TERM] [--max-pages N] [--max-documents N]
              [--per-page N] [--include-full-text]
              [--dry-run] [--ingest-only] [--json]
```

- `--dry-run` performs **zero DB writes** (fetch + prefilter only).
- `--ingest-only` writes `raw_items` only (deduped); no triage/forecast.
- Default (no flags) runs the full pipeline over a small recent window.
- `--days` cannot be combined with `--start`/`--end` (conflicting-arg error).
- No credentials are printed.

### Example dry-run (does not write to the database)

```bash
source .venv/bin/activate
levy-register --days 7 --dry-run --json
```

Against Atlas (`LEVY_OFFLINE=false`), a full-pipeline run over the last two days:

```bash
levy-register --days 2 --max-documents 50
```

## Scheduled scout

In production the `register_scout` tick runs the real ingestion over a small
lookback window (`LEVY_REGISTER_SCOUT_LOOKBACK_DAYS`, default 2 days), capped at
`LEVY_REGISTER_SCOUT_MAX_DOCUMENTS` (default 50), full pipeline. Overlapping
runs are prevented with an `asyncio.Lock` and APScheduler `max_instances=1`. It
can run immediately at worker startup when
`LEVY_REGISTER_SCOUT_RUN_ON_START=true`. All other scout ticks remain
heartbeat-only. In offline mode the tick logs a heartbeat and makes no network
call.

## No-provider-key behavior

Atlas mode sets `LEVY_OFFLINE=false`, but if `LEVY_OPENROUTER_API_KEY` is empty
the Jev and LLM adapters use deterministic offline/fallback behavior with **no
network calls**. Classifier labeling stays honest: the Jev classifier is
reported as `fallback` (missing key) or `offline` (offline mode), and
`LLMResponse.offline` is `True`.

## LLM provider-error fallback (robust degradation)

When a provider key *is* present but the OpenRouter chat-completions call keeps
failing (e.g. persistent HTTP 400, timeouts), `LLMClient.chat_json` does **not**
raise. After bounded retries it logs a single `WARNING` (role, model and error
*type* only — never the key or request/response payload) and returns a
deterministic `offline_json_response` with `LLMResponse.offline=True`. This
keeps a partially-persisted pipeline run from aborting mid-way.

`BudgetExceeded` is different: it is a real control signal raised *before* any
provider call and is never swallowed by the fallback.

### OpenRouter chat adapter contract

- Endpoint: `POST https://openrouter.ai/api/v1/chat/completions`
  (`base_url="https://openrouter.ai"`, path `/api/v1/chat/completions`).
- Auth: `Authorization: Bearer $LEVY_OPENROUTER_API_KEY`. `HTTP-Referer` /
  `X-Title` are optional attribution headers sent best-effort.
- Body: `{"model", "messages": [{role, content}...], "response_format":
  {"type": "json_object"}}`.
- HTTP 400 causes: `response_format` is not honored by every upstream provider
  routed through OpenRouter (some return 400 `wrong_api_format`), and a
  renamed/deprecated model slug in `ROLE_MODELS` can 400/404. Fixing model IDs
  is intentionally out of scope; correctness relies on the offline fallback,
  not on any particular model ID being currently valid. `_parse_json` also
  tolerates non-JSON text bodies.

## Resumable / idempotent pipeline

`Pipeline.process_document` is safe to re-run over the same documents. On a
duplicate raw hash it no longer returns a bare no-op — it loads the existing
raw item and evidence and continues from where a prior (possibly interrupted)
run stopped:

- no evidence yet → resume triage on the stored raw item;
- evidence but no belief → continue question linking / factory / forecast
  without reclassifying or duplicating evidence;
- evidence-triggered belief already exists for every linked material question →
  return `complete` (no new belief).

Question links use `$addToSet` (mirrored in the in-memory repo) so re-runs never
create duplicate `question_ids`, and the question factory links an existing
deterministic draft question instead of leaving evidence unlinked. If a forecast
fails *after* raw/evidence/classification were persisted, the forecast job is
failed/requeued and the result is returned with `error` set and `ingested`/
`accepted` still true — a partial write is never reported as a non-insertion.

## Register run summary counters

`RegisterRunSummary` distinguishes: `inserted` (newly processed), `resumed`
(duplicate hash that resumed real work), `duplicates` (duplicate hash that was
already complete / unresumable), `accepted`/`rejected`, `beliefs`/`forecasts`,
and `errors`. A pipeline exception after insertion keeps these counters honest:
the insertion/resume is still counted and the error is recorded. Re-running the
same partial documents therefore shows `resumed`, not `inserted=0` or a silent
duplicate.


## Settings

| Env var | Default | Notes |
| --- | --- | --- |
| `LEVY_FEDERAL_REGISTER_BASE_URL` | `https://www.federalregister.gov` | |
| `LEVY_FEDERAL_REGISTER_USER_AGENT` | `LEVY-TariffDesk/1.0` | |
| `LEVY_FEDERAL_REGISTER_CONTACT` | `levy-ops@example.com` | included in User-Agent |
| `LEVY_FEDERAL_REGISTER_QUERY` | `tariff` | default search term |
| `LEVY_FEDERAL_REGISTER_TIMEOUT_SECONDS` | `20.0` | clamped 1–120 |
| `LEVY_FEDERAL_REGISTER_MAX_RETRIES` | `3` | clamped 0–8 |
| `LEVY_REGISTER_SCOUT_LOOKBACK_DAYS` | `2` | clamped 1–30 |
| `LEVY_REGISTER_SCOUT_MAX_DOCUMENTS` | `50` | clamped 1–500 |
| `LEVY_REGISTER_SCOUT_RUN_ON_START` | `true` | run once at startup |

## Testing

All tests use `httpx.MockTransport` or an injected fake client — no live network
is contacted in `pytest`:

```bash
LEVY_OFFLINE=true .venv/bin/python -m pytest
```
