"""Azure Blob Storage helpers for storing and retrieving parsed appeal documents."""

import json
import os
from datetime import datetime, timezone
from typing import Generator

from azure.storage.blob import BlobServiceClient

from appeals_monitor.logger import logger

_CONTAINER_NAME = "appeals"


def _get_blob_service_client() -> BlobServiceClient:
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not connection_string:
        raise ValueError(
            "Missing AZURE_STORAGE_CONNECTION_STRING environment variable."
        )
    return BlobServiceClient.from_connection_string(connection_string)


def _get_container_client():
    client = _get_blob_service_client()
    container = client.get_container_client(_CONTAINER_NAME)
    if not container.exists():
        container.create_container()
    return container


def _slugify_type(doc_type: str) -> str:
    """Convert a document type name to a URL-safe folder name.

    Example: 'DREF Operation' -> 'dref-operation'
    """
    return doc_type.lower().replace(" ", "-").replace(",", "")


def _blob_name(doc_url: str, doc_type: str = "") -> str:
    """Derive a stable blob path from the document URL and type.

    Example URL: https://go-api.ifrc.org/api/DownloadFile/94906/MDRMY013do
    Blob path:   dref-operation/MDRMY013do.json

    When doc_type is empty, the blob is stored at the root level.
    """
    parts = [p for p in doc_url.rstrip("/").split("/") if p]
    doc_id = parts[-1] if parts else "unknown"
    if doc_type:
        return f"{_slugify_type(doc_type)}/{doc_id}.json"
    return f"{doc_id}.json"


def document_exists(doc_url: str, doc_type: str = "") -> bool:
    """Check whether a document has already been stored in blob storage."""
    container = _get_container_client()
    return container.get_blob_client(_blob_name(doc_url, doc_type)).exists()


def upload_document(doc_url: str, markdown: str, doc_type: str = "") -> str:
    """Upload a parsed document (markdown + metadata) to blob storage.

    Returns the blob name.
    """
    container = _get_container_client()
    name = _blob_name(doc_url, doc_type)

    payload = {
        "document_url": doc_url,
        "document_type": doc_type,
        "markdown": markdown,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }

    container.upload_blob(
        name=name,
        data=json.dumps(payload, ensure_ascii=False),
        overwrite=True,
    )
    logger.info(f"Uploaded parsed document to blob: {name}")
    return name


def list_unprocessed() -> Generator[dict, None, None]:
    """Yield parsed documents from blob storage that haven't been analyzed yet.

    A document is considered unprocessed if it has no 'processed_at' timestamp.
    Each item is a dict with keys: document_url, markdown, parsed_at, blob_name.
    """
    container = _get_container_client()
    for blob in container.list_blobs():
        if not blob.name.endswith(".json"):
            continue
        data = container.download_blob(blob.name).readall()
        doc = json.loads(data)
        if "processed_at" in doc:
            continue
        doc["blob_name"] = blob.name
        yield doc


def mark_processed(blob_name: str, result: dict) -> None:
    """Store the analysis result alongside the original document.

    Updates the blob with an 'analysis' key and a 'processed_at' timestamp.
    """
    container = _get_container_client()
    data = container.download_blob(blob_name).readall()
    doc = json.loads(data)
    doc["analysis"] = result
    doc["processed_at"] = datetime.now(timezone.utc).isoformat()
    container.upload_blob(
        name=blob_name,
        data=json.dumps(doc, ensure_ascii=False),
        overwrite=True,
    )
    logger.info(f"Marked blob as processed: {blob_name}")
