"""Standalone backfill: import all historical IFRC appeal documents in batches.

Runs the ETL step only (download -> convert -> upload to blob storage). It does
NOT run analysis and does NOT send any notifications, so it is safe to let it
churn through the full archive.

Each backfilled document is stamped as processed (with no analysis and a
``backfilled: True`` flag) so the daily analysis/notification pipeline skips it
and never emails historical appeals. The documents can be deliberately
re-analysed later by targeting the ``backfilled`` flag.

The archive is processed in fixed-size date windows (default 6 months), from the
earliest available document date up to today. Progress is persisted to a small
JSON file after every completed window, so the script is fully resumable: stop it
at any time (Ctrl-C or machine shutdown) and re-run it later to continue from the
last completed window.

Usage:
    uv run python scripts/backfill_history.py
    uv run python scripts/backfill_history.py --start-date 2015-01-01 --batch-months 6
    uv run python scripts/backfill_history.py --reset      # ignore saved progress

Exit codes: 0 success, 1 pipeline error, 2 configuration error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from appeals_monitor.config import logger
from appeals_monitor.etl import get_earliest_document_date, run_etl_in_range

# Anchor the default progress file to the project root (parent of scripts/) so it
# lands in the same place regardless of the current working directory.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PROGRESS_FILE = _PROJECT_ROOT / ".backfill_progress.json"
_DEFAULT_BATCH_MONTHS = 1
# Fallback start date used only when the API cannot report the earliest document.
_FALLBACK_START_DATE = "2010-01-01"
_ISO = "%Y-%m-%d"


@dataclass(frozen=True)
class BackfillArgs:
    start_date: str | None
    end_date: str | None
    batch_months: int
    progress_file: Path
    reset: bool


def _parse_args(argv: list[str] | None = None) -> BackfillArgs:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-date",
        help="First date to backfill (YYYY-MM-DD). "
        "Defaults to the earliest document reported by the GO API.",
    )
    parser.add_argument(
        "--end-date",
        help="Last date to backfill (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--batch-months",
        type=int,
        default=_DEFAULT_BATCH_MONTHS,
        help=f"Window size in months (default: {_DEFAULT_BATCH_MONTHS}).",
    )
    parser.add_argument(
        "--progress-file",
        default=_DEFAULT_PROGRESS_FILE,
        help=f"Path to the resume/progress file (default: {_DEFAULT_PROGRESS_FILE}).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Ignore and overwrite any existing progress file, starting fresh.",
    )
    ns = parser.parse_args(argv)

    if ns.batch_months < 1:
        parser.error("--batch-months must be a positive integer")

    return BackfillArgs(
        start_date=ns.start_date,
        end_date=ns.end_date,
        batch_months=ns.batch_months,
        progress_file=Path(ns.progress_file),
        reset=ns.reset,
    )


def _parse_iso(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, _ISO).date()
    except ValueError:
        logger.error(f"{label}: invalid date {value!r} (expected YYYY-MM-DD)")
        sys.exit(2)


def _add_months(anchor: date, months: int) -> date:
    """Return `anchor` advanced by `months`, clamping the day to the month length."""
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    # Clamp the day (e.g. Aug 31 + 6 months -> Feb 28/29).
    for day in (anchor.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, 28)


# --- Progress persistence ---


def _load_progress(path: Path) -> date | None:
    """Return the cursor (next date to process) from the progress file, if any."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cursor = data.get("next_from")
        return _parse_iso(cursor, "progress file") if cursor else None
    except (OSError, ValueError) as exc:
        logger.error(f"Failed to read progress file {path}: {exc}")
        sys.exit(2)


def _save_progress(path: Path, next_from: date, start: date, end: date) -> None:
    """Atomically persist the resume cursor to the progress file."""
    payload = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "next_from": next_from.isoformat(),
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.error(f"Failed to write progress file {path}: {exc}")
        sys.exit(1)


# --- Orchestration ---


def _resolve_start(args: BackfillArgs) -> date:
    """Determine where to begin: saved progress > --start-date > API earliest > fallback."""
    if not args.reset:
        resumed = _load_progress(args.progress_file)
        if resumed is not None:
            logger.info(f"Resuming from saved progress: {resumed.isoformat()}")
            return resumed

    if args.start_date:
        return _parse_iso(args.start_date, "--start-date")

    logger.info("Detecting earliest available document date from the GO API...")
    earliest = get_earliest_document_date()
    if earliest:
        logger.info(f"Earliest document date reported by API: {earliest}")
        return _parse_iso(earliest, "API earliest date")

    logger.warning(
        f"Could not detect earliest date; falling back to {_FALLBACK_START_DATE}. "
        "Pass --start-date to override."
    )
    return _parse_iso(_FALLBACK_START_DATE, "fallback start date")


def run_backfill(args: BackfillArgs) -> int:
    load_dotenv(override=True)

    end = _parse_iso(args.end_date, "--end-date") if args.end_date else date.today()
    start = _resolve_start(args)

    if start > end:
        logger.info(
            f"Nothing to do: start {start.isoformat()} is after end {end.isoformat()}. "
            "Backfill already complete."
        )
        return 0

    logger.info(
        f"Backfilling {start.isoformat()} -> {end.isoformat()} "
        f"in {args.batch_months}-month windows."
    )

    total_uploaded = 0
    cursor = start
    while cursor <= end:
        # Windows are inclusive on both ends (the API filters are __gte/__lte),
        # so the raw window end is one day before the next window's start.
        raw_end = date.fromordinal(
            _add_months(cursor, args.batch_months).toordinal() - 1
        )
        window_end = min(raw_end, end)
        logger.info(f"=== Window {cursor.isoformat()} -> {window_end.isoformat()} ===")
        try:
            uploaded = run_etl_in_range(
                cursor.isoformat(), window_end.isoformat(), mark_processed=True
            )
        except Exception as exc:
            logger.error(
                f"Window {cursor.isoformat()} -> {window_end.isoformat()} failed: {exc}"
            )
            logger.error(
                f"Stopping. Re-run to resume from this window ({cursor.isoformat()})."
            )
            return 1

        total_uploaded += uploaded

        # Advance to the day after this window and persist so a restart resumes here.
        next_from = date.fromordinal(window_end.toordinal() + 1)
        _save_progress(args.progress_file, next_from, start, end)
        cursor = next_from

    logger.info(
        f"Backfill complete. Uploaded {total_uploaded} documents "
        f"across {start.isoformat()} -> {end.isoformat()}."
    )
    return 0


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    sys.exit(run_backfill(args))


if __name__ == "__main__":
    main()
