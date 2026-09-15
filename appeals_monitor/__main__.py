"""Entrypoint for the Appeals Monitor pipeline."""

import json
import os
import sys

from dotenv import load_dotenv

from appeals_monitor.config import logger


def main():
    load_dotenv(override=True)

    command = sys.argv[1] if len(sys.argv) > 1 else "all"

    if command == "etl":
        _run_etl()
    elif command == "analyze":
        _run_analysis()
    elif command == "all":
        _run_etl()
        _run_analysis()
    elif command == "backfill":
        _run_backfill()
    elif command == "feedback":
        _run_feedback(dry_run="--dry-run" in sys.argv[2:])
    else:
        print("Usage: appeals-monitor [etl|analyze|all|backfill|feedback]")
        print(
            "  etl       Fetch documents, convert to markdown, upload to blob storage"
        )
        print(
            "  analyze   Read from blob storage, run LLM analysis, send notifications"
        )
        print("  all       Run both steps sequentially (default)")
        print(
            "  backfill  Rebuild index.json from all existing documents"
        )
        print(
            "  feedback  Send the feedback survey invite/reminder due today [--dry-run]"
        )
        sys.exit(1)


def _run_etl():
    from appeals_monitor.etl import run_etl

    try:
        last_n_days = int(os.getenv("LAST_N_DAYS", "7"))
    except ValueError:
        logger.error("LAST_N_DAYS must be a valid integer, defaulting to 7")
        last_n_days = 7

    logger.info(f"Starting ETL pipeline (last {last_n_days} days)...")
    try:
        count = run_etl(last_n_days=last_n_days)
    except Exception as exc:
        logger.error(f"ETL pipeline execution failed: {exc}")
        sys.exit(1)

    try:
        logger.info(f"ETL completed. Uploaded {count} documents.")
    except Exception as exc:
        logger.error(f"Failed to log ETL completion: {exc}")
        sys.exit(1)


def _run_analysis():
    from appeals_monitor.monitor import run_analysis

    logger.info("Starting analysis + notification pipeline...")
    try:
        results = run_analysis()
    except Exception as exc:
        logger.error(f"Analysis pipeline execution failed: {exc}")
        sys.exit(1)

    try:
        logger.info(f"Analysis completed. Processed {len(results)} documents.")
    except Exception as exc:
        logger.error(f"Failed to log analysis completion: {exc}")
        sys.exit(1)

    try:
        output = json.dumps(results, indent=2, default=str)
    except TypeError as exc:
        logger.error(f"Failed to serialize analysis results: {exc}")
        sys.exit(1)

    try:
        print(output)
    except Exception as exc:
        logger.error(f"Failed to print analysis results: {exc}")
        sys.exit(1)


def _run_backfill():
    from appeals_monitor.storage import rebuild_index

    logger.info("Rebuilding index.json from existing documents...")
    try:
        count = rebuild_index()
    except Exception as exc:
        logger.error(f"Index rebuild failed: {exc}")
        sys.exit(1)
    logger.info(f"Index rebuild complete. Processed {count} blobs.")


def _run_feedback(dry_run: bool = False):
    from appeals_monitor.feedback import run_feedback

    logger.info("Starting feedback campaign...")
    try:
        errors = run_feedback(dry_run=dry_run)
    except RuntimeError as exc:
        logger.error(f"Feedback campaign misconfigured: {exc}")
        sys.exit(2)
    except Exception as exc:
        logger.error(f"Feedback campaign execution failed: {exc}")
        sys.exit(1)

    if errors:
        logger.error(f"Feedback campaign finished with {len(errors)} error(s).")
        sys.exit(1)


if __name__ == "__main__":
    main()
