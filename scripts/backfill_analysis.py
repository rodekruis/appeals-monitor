"""Standalone analysis backfill: analyze stored documents that have no analysis yet.

Finds every document in blob storage flagged ``has_analysis: false`` in
index.json (typically documents imported by scripts/backfill_history.py), runs
the LLM analysis, and stores the result via mark_processed(), which also updates
index.json.

No emails or notifications are sent:

* this script never calls notify() — that only happens in monitor.run_analysis();
* the daily pipeline only picks up documents WITHOUT a ``processed_at`` timestamp
  (list_unprocessed), and every document handled here already has one (set by the
  historical backfill, refreshed by mark_processed). Documents without
  ``processed_at`` are skipped with a warning: they belong to the daily
  analysis/notification pipeline, and analyzing them here would suppress their
  email.

Progress is tracked in blob storage itself: each successfully analyzed document
flips to ``has_analysis: true`` in index.json, so index.json is the resume cursor
— stop it at any time (Ctrl-C or machine shutdown) and re-run it later to
continue with the remaining documents. Every document is logged with an
``i/N`` progress prefix.

Unlike the daily pipeline (which marks failures to avoid endless automatic
retries), documents whose analysis fails here are NOT marked, so they stay
``has_analysis: false`` and are retried on the next run.

Usage:
    uv run python scripts/backfill_analysis.py
    uv run python scripts/backfill_analysis.py --dry-run   # list pending docs only

Exit codes: 0 success, 1 pipeline error (some documents failed), 2 configuration error.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

from appeals_monitor.analysis import analyze_document, create_agent_pipeline
from appeals_monitor.config import logger
from appeals_monitor.monitor import create_model
from appeals_monitor.storage import (
    get_document,
    list_unanalyzed_blob_names,
    mark_processed,
)


@dataclass(frozen=True)
class AnalysisBackfillArgs:
    dry_run: bool


def _parse_args(argv: list[str] | None = None) -> AnalysisBackfillArgs:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list the documents pending analysis; do not call the LLM.",
    )
    ns = parser.parse_args(argv)
    return AnalysisBackfillArgs(dry_run=ns.dry_run)


def _analyze_document(blob_name: str, agent) -> str:
    """Analyze a single stored document. Returns 'analyzed', 'failed', or 'skipped'."""
    try:
        doc = get_document(blob_name)
    except Exception as exc:
        logger.error(f"{blob_name}: failed to download document: {exc}")
        return "failed"

    if doc is None:
        logger.warning(f"{blob_name}: blob missing (stale index entry); skipping.")
        return "skipped"
    if doc.get("analysis"):
        logger.info(f"{blob_name}: already analyzed (stale index entry); skipping.")
        return "skipped"
    if "processed_at" not in doc:
        logger.warning(
            f"{blob_name}: no processed_at timestamp — this document belongs to the "
            "daily analysis/notification pipeline; skipping it so its email "
            "notification is not suppressed."
        )
        return "skipped"
    if not doc.get("markdown"):
        logger.warning(f"{blob_name}: empty markdown; skipping.")
        return "skipped"

    doc_url = doc.get("document_url", "")
    logger.info(f"Analyzing: {doc_url} ({blob_name})")
    result = analyze_document(doc["markdown"], doc_url, agent)
    result["document_type"] = doc.get("document_type", "")

    if result.get("general_info") is None:
        logger.error(
            f"{blob_name}: analysis failed; left unmarked so it is retried "
            "on the next run."
        )
        return "failed"

    try:
        mark_processed(blob_name, result)
    except Exception as exc:
        logger.error(f"{blob_name}: failed to store analysis: {exc}")
        return "failed"

    return "analyzed"


def run_analysis_backfill(args: AnalysisBackfillArgs) -> int:
    load_dotenv(override=True)

    pending = list_unanalyzed_blob_names()
    total = len(pending)
    if total == 0:
        logger.info(
            "No documents pending analysis (has_analysis: false). Nothing to do."
        )
        return 0

    logger.info(f"{total} documents pending analysis.")
    if args.dry_run:
        for name in pending:
            logger.info(f"  pending: {name}")
        logger.info("Dry run: no documents analyzed.")
        return 0

    try:
        model = create_model()
    except ValueError as exc:
        logger.error(str(exc))
        return 2
    agent = create_agent_pipeline(model)

    counts = {"analyzed": 0, "failed": 0, "skipped": 0}
    try:
        for i, blob_name in enumerate(pending, start=1):
            logger.info(f"--- Document {i}/{total} ---")
            counts[_analyze_document(blob_name, agent)] += 1
    except KeyboardInterrupt:
        logger.warning(
            f"Interrupted after {counts['analyzed']} analyzed documents. "
            "Re-run to resume — analyzed documents are marked in index.json."
        )
        return 1

    logger.info(
        f"Analysis backfill complete: {counts['analyzed']} analyzed, "
        f"{counts['failed']} failed, {counts['skipped']} skipped "
        f"out of {total} candidates."
    )
    if counts["failed"]:
        logger.error(
            f"{counts['failed']} documents failed; re-run the script to retry them."
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    sys.exit(run_analysis_backfill(args))


if __name__ == "__main__":
    main()
