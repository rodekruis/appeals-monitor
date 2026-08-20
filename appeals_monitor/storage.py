"""Azure Blob Storage helpers for storing and retrieving parsed appeal documents."""

import json
import os
from datetime import datetime, timezone
from typing import Generator

from azure.storage.blob import BlobServiceClient, ContainerClient

from appeals_monitor.config import logger

_CONTAINER_NAME = "appeals"
_INDEX_BLOB_NAME = "index.json"


def _get_blob_service_client() -> BlobServiceClient:
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not connection_string:
        raise ValueError(
            "Missing AZURE_STORAGE_CONNECTION_STRING environment variable."
        )
    return BlobServiceClient.from_connection_string(connection_string)


def _get_container_client() -> ContainerClient:
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

    The last URL segment is used verbatim — including any query string (legacy
    GO URLs like Download.aspx?FileId=118182 differ ONLY in the query string,
    so stripping it would collapse distinct documents onto the same blob name).
    '?' is legal in blob names; it only needs encoding when constructing URLs.

    When doc_type is empty, the blob is stored at the root level.
    """
    parts = [p for p in doc_url.rstrip("/").split("/") if p]
    doc_id = parts[-1] if parts else "unknown"
    if doc_type:
        return f"{_slugify_type(doc_type)}/{doc_id}.json"
    return f"{doc_id}.json"


# --- Index helpers ---


def _read_index(container: ContainerClient) -> dict:
    """Download and parse index.json; returns an empty dict if it doesn't exist yet."""
    blob = container.get_blob_client(_INDEX_BLOB_NAME)
    if not blob.exists():
        return {}
    data = blob.download_blob().readall()
    return json.loads(data)


def _write_index(container: ContainerClient, index: dict) -> None:
    """Serialize and upload index.json (overwrites existing)."""
    container.upload_blob(
        name=_INDEX_BLOB_NAME,
        data=json.dumps(index, ensure_ascii=False, default=str),
        overwrite=True,
    )


def _index_entry_from_doc(blob_name: str, doc: dict) -> dict:
    """Build an index entry from a stored blob payload."""
    source_appeal_code = blob_name.rsplit("/", 1)[-1].removesuffix(".json")
    has_analysis = bool(doc.get("analysis"))

    entry: dict = {
        "blob_name": blob_name,
        "document_url": doc.get("document_url", ""),
        "doc_type": doc.get("document_type", ""),
        "source_appeal_code": source_appeal_code,
        "parsed_at": doc.get("parsed_at"),
        "processed_at": doc.get("processed_at"),
        "has_analysis": has_analysis,
    }

    if has_analysis:
        general_info = (doc.get("analysis") or {}).get("general_info") or {}
        entry["appeal_code"] = general_info.get("appeal_code")
        entry["country"] = general_info.get("country")
        entry["country_iso3"] = general_info.get("country_iso3")
        entry["hazard"] = general_info.get("hazard")
        entry["start_date"] = (
            str(general_info["start_date"]) if general_info.get("start_date") else None
        )
        entry["end_date"] = (
            str(general_info["end_date"]) if general_info.get("end_date") else None
        )

    return entry


def _upsert_index_entry(container: ContainerClient, blob_name: str, doc: dict) -> None:
    """Add or update a single entry in index.json."""
    index = _read_index(container)
    index[blob_name] = _index_entry_from_doc(blob_name, doc)
    _write_index(container, index)


# --- Public API ---


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
    _upsert_index_entry(container, name, payload)
    logger.info(f"Uploaded parsed document to blob: {name}")
    return name


def upload_backfilled_document(doc_url: str, markdown: str, doc_type: str = "") -> str:
    """Upload a parsed document and immediately mark it processed WITHOUT analysis.

    Used by the historical backfill: the document is stored and stamped with a
    ``processed_at`` timestamp so the daily analysis/notification pipeline skips
    it (``list_unprocessed`` excludes anything with ``processed_at``), but no LLM
    analysis is run. ``analysis`` is None and ``backfilled`` is True so these
    documents can be identified and deliberately re-analysed later.

    Returns the blob name.
    """
    container = _get_container_client()
    name = _blob_name(doc_url, doc_type)

    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "document_url": doc_url,
        "document_type": doc_type,
        "markdown": markdown,
        "parsed_at": now,
        "processed_at": now,
        "analysis": None,
        "backfilled": True,
    }

    container.upload_blob(
        name=name,
        data=json.dumps(payload, ensure_ascii=False),
        overwrite=True,
    )
    _upsert_index_entry(container, name, payload)
    logger.info(f"Uploaded backfilled document (no analysis) to blob: {name}")
    return name


def list_unprocessed() -> Generator[dict, None, None]:
    """Yield parsed documents from blob storage that haven't been analyzed yet.

    A document is considered unprocessed if it has no 'processed_at' timestamp.
    Each item is a dict with keys: document_url, markdown, parsed_at, blob_name.

    Skips any document missing required fields (document_url, markdown, parsed_at).
    """
    container = _get_container_client()
    for blob in container.list_blobs():
        if not blob.name.endswith(".json") or blob.name == _INDEX_BLOB_NAME:
            continue
        data = container.download_blob(blob.name).readall()
        doc = json.loads(data)
        if "processed_at" in doc:
            continue
        # Validate required fields before yielding
        required_fields = {"document_url", "markdown", "parsed_at"}
        if not required_fields.issubset(doc.keys()):
            missing = required_fields - set(doc.keys())
            logger.warning(
                f"Skipping incomplete document {blob.name}: missing fields {missing}"
            )
            continue
        doc["blob_name"] = blob.name
        yield doc


def list_unanalyzed_blob_names(require_processed: bool = True) -> list[str]:
    """Return blob names of documents flagged ``has_analysis: false`` in index.json.

    When ``require_processed`` is True (the default), only documents that already
    carry a ``processed_at`` timestamp are returned — i.e. documents the daily
    pipeline will never pick up (``list_unprocessed`` skips anything with
    ``processed_at``), so analyzing them cannot trigger an email notification.

    The index is only a candidate source and may be stale in either direction;
    callers must verify the blob content before acting on a name. Run
    ``rebuild_index()`` first if the index may be missing entries.
    """
    container = _get_container_client()
    index = _read_index(container)
    return sorted(
        name
        for name, entry in index.items()
        if not entry.get("has_analysis")
        and (not require_processed or entry.get("processed_at"))
    )


def get_document(blob_name: str) -> dict | None:
    """Download a single stored document by blob name.

    Returns the document dict with an added ``blob_name`` key, or None if the
    blob does not exist.
    """
    container = _get_container_client()
    blob = container.get_blob_client(blob_name)
    if not blob.exists():
        return None
    doc = json.loads(blob.download_blob().readall())
    doc["blob_name"] = blob_name
    return doc


def mark_processed(blob_name: str, result: dict) -> None:
    """Store the analysis result alongside the original document.

    Updates the blob with an 'analysis' key and a 'processed_at' timestamp.
    Also updates index.json.
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
    _upsert_index_entry(container, blob_name, doc)
    logger.info(f"Marked blob as processed: {blob_name}")


def rebuild_index() -> int:
    """Rebuild index.json from all existing documents.

    Idempotent — safe to re-run. Returns the number of document blobs processed.
    """
    container = _get_container_client()
    index: dict = {}
    count = 0
    for blob in container.list_blobs():
        if not blob.name.endswith(".json") or blob.name == _INDEX_BLOB_NAME:
            continue
        data = container.download_blob(blob.name).readall()
        doc = json.loads(data)
        index[blob.name] = _index_entry_from_doc(blob.name, doc)
        count += 1
    _write_index(container, index)
    logger.info(f"Index rebuild complete: indexed {count} blobs.")
    return count
