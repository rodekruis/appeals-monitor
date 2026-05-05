"""Orchestrator: ties together ETL and analysis to run the full monitoring pipeline."""

from typing import List
import os

from langchain_openai import AzureChatOpenAI

from appeals_monitor.logger import logger
from appeals_monitor.etl import get_documents, convert_document
from appeals_monitor.analysis import create_agents, analyze_document
from appeals_monitor.notify import notify


def run_monitor(last_n_days: int = 7) -> List[dict]:
    """Main monitoring function: fetches, converts, and analyzes appeal documents.

    Returns a list of results per document with general info, interventions, and cash info.
    """
    # Validate required configuration
    endpoint = os.getenv("OPENAI_ENDPOINT")
    api_version = os.getenv("OPENAI_API_VERSION")
    if not endpoint or not api_version:
        raise ValueError(
            "Missing required environment variables: OPENAI_ENDPOINT and OPENAI_API_VERSION must be set."
        )

    model = AzureChatOpenAI(
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4"),
        azure_endpoint=endpoint,
        openai_api_version=api_version,
    )

    # Create agents once and reuse across all documents
    agents = create_agents(model)

    # Fetch and convert documents
    logger.info(f"Fetching documents from the last {last_n_days} days...")
    docs = get_documents(last_n_days=last_n_days)
    logger.info(f"Found {len(docs)} documents")

    results = []

    for doc_url in docs:
        logger.info(f"Processing: {doc_url}")
        markdown = convert_document(doc_url)
        if not markdown:
            logger.warning(f"Skipping empty document: {doc_url}")
            continue

        doc_result = analyze_document(markdown, doc_url, agents)
        results.append(doc_result)

    # Send email notification (non-critical: don't crash pipeline on failure)
    try:
        notify(results)
    except Exception as e:
        logger.error(f"Notification failed (results still returned): {e}")

    return results
