"""``levy-register`` CLI: fetch and process Federal Register documents.

Safe defaults: a non-dry-run full pipeline over a bounded recent window with
conservative page/document caps. ``--dry-run`` performs zero DB writes.

No credentials are printed. Offline mode is honoured — the CLI will not make
network calls when ``LEVY_OFFLINE=true`` unless run against a fake client in
tests (the client itself is injectable).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date
from typing import Optional, Sequence

from levy.agents.register import RegisterService, resolve_window
from levy.core.db import InMemoryRepository, make_repository
from levy.core.events import get_event_bus
from levy.settings import get_settings

log = logging.getLogger("levy.register.cli")

# CLI-level safe caps (smaller than the hard client caps).
CLI_MAX_PAGES = 3
CLI_MAX_DOCUMENTS = 100


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}, expected YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="levy-register",
        description="Fetch and process live Federal Register tariff/trade documents.",
    )
    p.add_argument("--start", type=_parse_date, default=None, help="start date YYYY-MM-DD")
    p.add_argument("--end", type=_parse_date, default=None, help="end date YYYY-MM-DD")
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="lookback window in days (exclusive with --start/--end)",
    )
    p.add_argument("--query", type=str, default=None, help="search term (default from settings)")
    p.add_argument("--max-pages", type=int, default=CLI_MAX_PAGES)
    p.add_argument("--max-documents", type=int, default=CLI_MAX_DOCUMENTS)
    p.add_argument("--per-page", type=int, default=100)
    p.add_argument(
        "--include-full-text",
        action="store_true",
        help="fetch document detail + raw text (bounded)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch + prefilter only; performs zero DB writes",
    )
    p.add_argument(
        "--ingest-only",
        action="store_true",
        help="write raw_items only; skip triage/forecast",
    )
    p.add_argument("--json", action="store_true", help="print the run summary as JSON")
    return p


async def _run(args: argparse.Namespace) -> dict:
    settings = get_settings()
    start, end = resolve_window(start=args.start, end=args.end, days=args.days)

    # A dry run must be incapable of touching the configured database. The
    # fetch-only service path never uses its repository, so provide an isolated
    # in-memory instance and do not bootstrap Atlas.
    repo = InMemoryRepository() if args.dry_run else make_repository(settings)
    if not args.dry_run:
        await repo.bootstrap()
    bus = get_event_bus()
    service = RegisterService(repo, settings=settings, bus=bus)
    try:
        summary = await service.run(
            start=start,
            end=end,
            query=args.query,
            per_page=args.per_page,
            max_pages=args.max_pages,
            max_documents=args.max_documents,
            include_full_text=args.include_full_text,
            fetch_only=args.dry_run,
            ingest_only=args.ingest_only,
        )
        return summary.to_dict()
    finally:
        await repo.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO)
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate conflicting date args before doing any work.
    try:
        resolve_window(start=args.start, end=args.end, days=args.days)
    except ValueError as exc:
        parser.error(str(exc))

    result = asyncio.run(_run(args))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        mode = result.get("mode")
        print(
            f"register {mode}: fetched={result['fetched']} inserted={result['inserted']} "
            f"duplicates={result['duplicates']} accepted={result['accepted']} "
            f"rejected={result['rejected']} beliefs={result['beliefs']} "
            f"pages/requests={result['pages']}/{result['request_count']} "
            f"window={result['start']}..{result['end']}"
        )
        if result.get("errors"):
            print(f"errors: {len(result['errors'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
