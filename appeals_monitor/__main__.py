"""Entrypoint for the Appeals Monitor pipeline."""

import json
import os
import sys

from dotenv import load_dotenv

from appeals_monitor.config import logger


def main():
    load_dotenv()

    command = sys.argv[1] if len(sys.argv) > 1 else "all"

    if command == "etl":
        _run_etl()
    elif command == "analyze":
        _run_analysis()
    elif command == "all":
        _run_etl()
        _run_analysis()
    else:
        print(f"Usage: appeals-monitor [etl|analyze|all]")
        print(
            f"  etl      Fetch documents, convert to markdown, upload to blob storage"
        )
        print(
            f"  analyze  Read from blob storage, run LLM analysis, send notifications"
        )
        print(f"  all      Run both steps sequentially (default)")
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
        logger.info(f"ETL completed. Uploaded {count} documents.")
    except Exception as e:
        logger.error(f"ETL pipeline failed: {e}")
        sys.exit(1)


def _run_analysis():
    from appeals_monitor.monitor import run_analysis

    logger.info("Starting analysis + notification pipeline...")
    try:
        results = run_analysis()
        logger.info(f"Analysis completed. Processed {len(results)} documents.")
        output = json.dumps(results, indent=2, default=str)
        print(output)
    except Exception as e:
        logger.error(f"Analysis pipeline failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
