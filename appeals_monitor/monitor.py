"""Orchestrator: analysis + notification pipeline (reads parsed documents from blob storage)."""

from typing import List
import os

from appeals_monitor.config import logger
from appeals_monitor.analysis import create_agent_pipeline, analyze_document
from appeals_monitor.notify import notify
from appeals_monitor.storage import list_unprocessed, mark_processed


def _create_model():
    """Create the Azure OpenAI model. Separated to keep credential validation lazy."""
    from langchain_openai import AzureChatOpenAI

    endpoint = os.getenv("OPENAI_ENDPOINT")
    api_version = os.getenv("OPENAI_API_VERSION")
    if not endpoint or not api_version:
        raise ValueError(
            "Missing required environment variables: OPENAI_ENDPOINT and OPENAI_API_VERSION must be set."
        )

    return AzureChatOpenAI(
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        azure_endpoint=endpoint,
        openai_api_version=api_version,
    )


def run_analysis() -> List[dict]:
    """Reads parsed documents from blob storage, runs LLM analysis, and sends notifications.

    Only processes documents that haven't been analyzed yet (no 'processed_at' timestamp).
    The OpenAI client is only initialized when there are documents to process.
    Returns a list of analysis results.
    """
    docs = list(list_unprocessed())
    if not docs:
        logger.info("No new documents to analyze.")
        return []

    model = _create_model()
    agent = create_agent_pipeline(model)

    results = []
    for doc in docs:
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
