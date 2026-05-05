"""Entrypoint for the Appeals Monitor pipeline."""

import json
import os
import sys

from dotenv import load_dotenv

from appeals_monitor.logger import logger
from appeals_monitor.monitor import run_monitor


def main():
    load_dotenv()

    last_n_days = int(os.getenv("LAST_N_DAYS", "7"))

    logger.info(f"Starting Appeals Monitor pipeline (last {last_n_days} days)...")

    try:
        results = run_monitor(last_n_days=last_n_days)
        logger.info(f"Pipeline completed. Processed {len(results)} documents.")

        # Output results as JSON (useful for Azure Logic Apps to capture output)
        output = json.dumps(results, indent=2, default=str)
        print(output)

        # Optionally save to file
        output_path = os.getenv("OUTPUT_PATH")
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(output)
            logger.info(f"Results saved to {output_path}")

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
