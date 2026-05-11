"""Orchestrator: analysis + notification pipeline (reads parsed documents from blob storage)."""

from typing import List
import os

from langchain_openai import AzureChatOpenAI

from appeals_monitor.logger import logger
from appeals_monitor.analysis import create_agent_pipeline, analyze_document
from appeals_monitor.notify import notify
from appeals_monitor.storage import list_unprocessed, mark_processed


def run_analysis() -> List[dict]:
    """Reads parsed documents from blob storage, runs LLM analysis, and sends notifications.

    Only processes documents that haven't been analyzed yet (no 'processed_at' timestamp).
    Returns a list of analysis results.
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

    # Create agent once and reuse across all documents
    agent = create_agent_pipeline(model)

    results = []
    for doc in list_unprocessed():
        doc_url = doc["document_url"]
        markdown = doc["markdown"]
        blob_name = doc["blob_name"]

        logger.info(f"Analyzing: {doc_url}")
        doc_result = analyze_document(markdown, doc_url, agent)
        results.append(doc_result)

        try:
            mark_processed(blob_name, doc_result)
        except Exception as e:
            logger.error(f"Failed to mark {blob_name} as processed: {e}")

    # Send notifications
    if results:
        notify(results)
    else:
        logger.info("No new documents to analyze.")

    return results
