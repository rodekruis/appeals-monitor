"""ETL functions: fetching and converting IFRC appeal documents."""

from typing import Any, List
from datetime import datetime, timedelta
import os
import tempfile

import requests

from appeals_monitor.config import logger
from appeals_monitor.storage import upload_document, document_exists

# Only fetch these document types (server-side filtering not supported)
ALLOWED_DOCUMENT_TYPES = {
    "DREF Operation",
    "Operational strategy",
    "Emergency Appeal",
}

# Per-page timeout in seconds (CPU-based processing)
_TIMEOUT_PER_PAGE = 30.0
_MIN_TIMEOUT = 60.0
_MAX_TIMEOUT = 600.0

# Large PDFs are processed in chunks to avoid memory exhaustion (std::bad_alloc)
_CHUNK_SIZE = 5  # pages per chunk


def _load_pdfium() -> Any:
    import pypdfium2 as pdfium

    return pdfium


def _load_docling() -> tuple[Any, Any, Any, Any, Any, Any]:
    from docling.document_converter import (
        ConversionStatus,
        DocumentConverter,
        InputFormat,
        PdfFormatOption,
    )
    from docling.datamodel.pipeline_options import OcrAutoOptions, PdfPipelineOptions

    return (
        ConversionStatus,
        DocumentConverter,
        InputFormat,
        PdfFormatOption,
        PdfPipelineOptions,
        OcrAutoOptions,
    )


def _calculate_timeout(num_pages: int) -> float:
    """Calculate document timeout based on page count (30s/page on CPU)."""
    return min(max(num_pages * _TIMEOUT_PER_PAGE, _MIN_TIMEOUT), _MAX_TIMEOUT)


def _get_page_count(pdf_path: str) -> int:
    """Get number of pages in a PDF without full conversion."""
    try:
        pdfium = _load_pdfium()
        pdf = pdfium.PdfDocument(pdf_path)
        count = len(pdf)
        pdf.close()
        return count
    except Exception:
        return 10  # Default estimate if page count detection fails


def _create_converter(timeout: float, with_ocr: bool = False) -> Any:
    """Create a DocumentConverter with the specified timeout."""
    (
        _,
        DocumentConverter,
        InputFormat,
        PdfFormatOption,
        PdfPipelineOptions,
        OcrAutoOptions,
    ) = _load_docling()

    if with_ocr:
        pipeline_options = PdfPipelineOptions(
            document_timeout=timeout,
            do_ocr=True,
            ocr_options=OcrAutoOptions(force_full_page_ocr=True),
        )
    else:
        pipeline_options = PdfPipelineOptions(document_timeout=timeout)

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )


def _download_document(document_url: str) -> str:
    """Download a PDF document to a temporary file. Returns the file path or empty string on failure."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    try:
        response = requests.get(document_url, headers=headers, timeout=60)
    except requests.RequestException as exc:
        logger.error(f"Failed to request {document_url}: {exc}")
        return ""

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        logger.error(f"Download returned an error for {document_url}: {exc}")
        return ""

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(response.content)
            return tmp.name
    except OSError as exc:
        logger.error(f"Failed to write downloaded PDF for {document_url}: {exc}")
        return ""


# IFRC GO endpoint to obtain a fresh auth token from username/password
_GO_AUTH_TOKEN_URL = "https://goadmin.ifrc.org/get_auth_token"


def _refresh_go_auth_token() -> str:
    """Fetch a fresh IFRC GO auth token using GO_USERNAME/GO_PASSWORD.

    Returns the token string, or an empty string on failure.
    """
    username = os.getenv("GO_USERNAME")
    password = os.getenv("GO_PASSWORD")
    if not username or not password:
        logger.error("Cannot refresh GO auth token (missing GO_USERNAME/GO_PASSWORD).")
        return ""

    try:
        response = requests.post(
            _GO_AUTH_TOKEN_URL,
            headers={"Content-Type": "application/json"},
            json={"username": username, "password": password},
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error(f"Failed to refresh GO auth token: {exc}")
        return ""

    try:
        token = response.json().get("token", "")
    except ValueError as exc:
        logger.error(f"Failed to decode GO auth token response JSON: {exc}")
        return ""

    if not token:
        logger.error("GO auth token refresh returned no token.")
    return token


def _request_appeal_documents(url: str, params: dict, auth_header: str) -> Any:
    """GET appeal documents with the given Authorization header.

    Returns the response object, or None on a network-level failure.
    """
    headers = {
        "accept": "application/json",
        "Authorization": auth_header,
    }
    try:
        return requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as exc:
        logger.error(f"Failed to request appeal documents: {exc}")
        return None


def get_documents(last_n_days: int = 7) -> List[tuple[str, str, str]]:
    """Fetches and downloads appeal documents created in the last n days from the IFRC GO platform.

    Returns a list of (document_url, local_pdf_path, document_type) tuples.
    Documents that fail to download are skipped.
    """
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=last_n_days)).strftime("%Y-%m-%d")

    api_url = os.getenv("GO_API_URL")
    auth_token = os.getenv("GO_AUTH_TOKEN")
    if not api_url or not auth_token:
        logger.error(
            "GO API not configured (missing GO_API_URL/GO_AUTH_TOKEN), no documents fetched."
        )
        return []

    url = f"{api_url.rstrip('/')}/appeal_document/"
    params = {"created_at__gte": from_date, "created_at__lte": to_date}

    response = _request_appeal_documents(url, params, f"Basic {auth_token}")
    if response is None:
        return []

    # Token rejected (expired/revoked): refresh from credentials and retry once.
    if response.status_code in (401, 403):
        logger.warning(
            f"GO auth token rejected ({response.status_code}), refreshing token..."
        )
        new_token = _refresh_go_auth_token()
        if not new_token:
            return []
        response = _request_appeal_documents(url, params, f"Token {new_token}")
        if response is None:
            return []

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        logger.error(f"Appeal document request returned an error: {exc}")
        return []

    try:
        data = response.json()
    except ValueError as exc:
        logger.error(f"Failed to decode appeal document response JSON: {exc}")
        return []

    all_docs = data.get("results", [])
    filtered = [d for d in all_docs if d.get("type") in ALLOWED_DOCUMENT_TYPES]
    skipped = len(all_docs) - len(filtered)
    if skipped:
        logger.info(f"Filtered out {skipped} documents with non-matching types")

    with_url = [d for d in filtered if d.get("document_url")]
    skipped_no_url = len(filtered) - len(with_url)
    if skipped_no_url:
        logger.info(f"Filtered out {skipped_no_url} documents with no URL")

    results = []
    for doc in with_url:
        doc_url = doc.get("document_url")
        doc_type = doc.get("type", "")
        if document_exists(doc_url, doc_type):
            logger.info(f"Already in blob storage, skipping: {doc_url}")
            continue
        pdf_path = _download_document(doc_url)
        if pdf_path:
            results.append((doc_url, pdf_path, doc_type))
    return results


def _convert_chunk(
    pdf_path: str,
    page_range: tuple,
    with_ocr: bool = False,
) -> str:
    """Convert a page range of a PDF to markdown. Returns empty string on failure."""
    ConversionStatus, _, _, _, _, _ = _load_docling()
    chunk_pages = page_range[1] - page_range[0] + 1
    timeout = _calculate_timeout(chunk_pages)
    converter = _create_converter(timeout, with_ocr=with_ocr)
    try:
        result = converter.convert(
            pdf_path, raises_on_error=False, page_range=page_range
        )
    except Exception as exc:
        logger.warning(f"Conversion call failed for pages {page_range}: {exc}")
        return ""

    if result.status not in (
        ConversionStatus.SUCCESS,
        ConversionStatus.PARTIAL_SUCCESS,
    ):
        logger.warning(
            f"Conversion failed for pages {page_range} (status={result.status})"
        )
        return ""

    if result.status == ConversionStatus.PARTIAL_SUCCESS:
        logger.warning(f"Partial conversion for pages {page_range}")

    try:
        return result.document.export_to_markdown()
    except Exception as exc:
        logger.warning(f"Markdown export failed for pages {page_range}: {exc}")
    return ""


def convert_document(pdf_path: str) -> str:
    """Converts a local PDF document to markdown format.

    Uses a two-stage conversion strategy:
    1. Standard pipeline (fast, layout-based extraction)
    2. Fallback: OCR pipeline with full-page OCR (for scanned/image PDFs)

    Large PDFs (>_CHUNK_SIZE pages) are processed in chunks to avoid
    out-of-memory errors (std::bad_alloc) in the C++ backend.

    Args:
        pdf_path: Path to a local PDF file.

    Returns empty string if conversion fails.
    """
    try:
        num_pages = _get_page_count(pdf_path)
        logger.info(f"Document has {num_pages} pages")

        # Build page ranges (1-based, inclusive)
        if num_pages <= _CHUNK_SIZE:
            chunks = [(1, num_pages)]
        else:
            chunks = []
            for start in range(1, num_pages + 1, _CHUNK_SIZE):
                end = min(start + _CHUNK_SIZE - 1, num_pages)
                chunks.append((start, end))
            logger.info(
                f"Splitting into {len(chunks)} chunks of up to {_CHUNK_SIZE} pages"
            )

        # Stage 1: Standard pipeline (per chunk)
        markdown_parts: list[str] = []
        ocr_chunks: list[tuple] = []

        for chunk in chunks:
            md = _convert_chunk(pdf_path, page_range=chunk, with_ocr=False)
            if md:
                markdown_parts.append(md)
            else:
                ocr_chunks.append(chunk)

        # Stage 2: OCR fallback for any chunks that failed
        for chunk in ocr_chunks:
            logger.info(f"Trying OCR fallback for pages {chunk}")
            md = _convert_chunk(pdf_path, page_range=chunk, with_ocr=True)
            if md:
                markdown_parts.append(md)
            else:
                logger.error(f"OCR fallback also failed for pages {chunk}")

        if not markdown_parts:
            logger.error(f"All conversion attempts failed for {pdf_path}")
            return ""

        return "\n\n".join(markdown_parts)

    finally:
        try:
            os.unlink(pdf_path)
        except OSError:
            pass


def run_etl(last_n_days: int = 7) -> int:
    """ETL pipeline: fetch documents, convert to markdown, upload to blob storage.

    Returns the number of documents successfully processed.
    """
    logger.info(f"Fetching documents from the last {last_n_days} days...")
    docs = get_documents(last_n_days=last_n_days)
    logger.info(f"Found {len(docs)} documents")

    uploaded = 0
    for doc_url, pdf_path, doc_type in docs:
        logger.info(f"Converting: {doc_url} (type={doc_type})")
        markdown = convert_document(pdf_path)
        if not markdown:
            logger.warning(f"Skipping empty document: {doc_url}")
            continue

        try:
            upload_document(doc_url, markdown, doc_type)
            uploaded += 1
        except Exception as e:
            logger.error(f"Failed to upload {doc_url} to blob storage: {e}")

    logger.info(f"ETL complete: {uploaded}/{len(docs)} documents uploaded.")
    return uploaded
