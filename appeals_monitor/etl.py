"""ETL functions: fetching and converting IFRC appeal documents."""

from typing import List
from datetime import datetime, timedelta
import os
import tempfile

import requests
from docling.document_converter import (
    DocumentConverter,
    ConversionStatus,
    InputFormat,
    PdfFormatOption,
)
from docling.datamodel.pipeline_options import PdfPipelineOptions, OcrAutoOptions

from appeals_monitor.logger import logger

# Per-page timeout in seconds (CPU-based processing)
_TIMEOUT_PER_PAGE = 30.0
_MIN_TIMEOUT = 60.0
_MAX_TIMEOUT = 600.0


def _calculate_timeout(num_pages: int) -> float:
    """Calculate document timeout based on page count (30s/page on CPU)."""
    return min(max(num_pages * _TIMEOUT_PER_PAGE, _MIN_TIMEOUT), _MAX_TIMEOUT)


def _get_page_count(pdf_path: str) -> int:
    """Get number of pages in a PDF without full conversion."""
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(pdf_path)
        count = len(pdf)
        pdf.close()
        return count
    except Exception:
        return 10  # Default estimate if page count detection fails


def _create_converter(timeout: float, with_ocr: bool = False) -> DocumentConverter:
    """Create a DocumentConverter with the specified timeout."""
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


def get_documents(last_n_days: int = 7) -> List[str]:
    """Fetches appeal documents created in the last n days from the IFRC GO platform."""
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=last_n_days)).strftime("%Y-%m-%d")

    url = "https://goadmin.ifrc.org/api/v2/appeal_document/"
    params = {"created_at__gte": from_date, "created_at__lte": to_date}
    headers = {
        "accept": "application/json",
        "Authorization": f"Basic {os.getenv('GO_AUTH_TOKEN')}",
    }
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.Timeout:
        logger.error("Timeout fetching documents from IFRC GO API")
        return []
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error fetching documents: {e}")
        return []
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error fetching documents: {e}")
        return []
    except requests.exceptions.JSONDecodeError as e:
        logger.error(f"Failed to parse API response as JSON: {e}")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Unexpected error fetching documents: {e}")
        return []

    documents = [d.get("document_url") for d in data.get("results", [])]
    return documents


def convert_document(document_url: str) -> str:
    """Converts a document from PDF to markdown format.

    Uses a two-stage conversion strategy:
    1. Standard pipeline (fast, layout-based extraction)
    2. Fallback: OCR pipeline with full-page OCR (for scanned/image PDFs)

    Timeout is calculated dynamically: 30s per page (min 60s, max 600s).
    Returns empty string if both attempts fail.
    """
    # Download PDF to determine page count for timeout calculation
    try:
        response = requests.get(document_url, timeout=60)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to download {document_url}: {e}")
        return ""

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(response.content)
        tmp_path = tmp.name

    try:
        num_pages = _get_page_count(tmp_path)
        timeout = _calculate_timeout(num_pages)
        logger.info(f"Document has {num_pages} pages, timeout set to {timeout:.0f}s")

        # Stage 1: Standard pipeline
        converter = _create_converter(timeout, with_ocr=False)
        try:
            result = converter.convert(tmp_path, raises_on_error=False)
            if result.status == ConversionStatus.SUCCESS:
                return result.document.export_to_markdown()
            if result.status == ConversionStatus.PARTIAL_SUCCESS:
                logger.warning(
                    f"Partial conversion for {document_url}, using partial result"
                )
                return result.document.export_to_markdown()
            logger.warning(
                f"Standard conversion failed for {document_url} "
                f"(status={result.status}), trying OCR fallback"
            )
        except Exception as e:
            logger.warning(
                f"Standard conversion error for {document_url}: {e}, trying OCR fallback"
            )

        # Stage 2: OCR fallback (handles scanned PDFs)
        ocr_converter = _create_converter(timeout, with_ocr=True)
        try:
            result = ocr_converter.convert(tmp_path, raises_on_error=False)
            if result.status in (
                ConversionStatus.SUCCESS,
                ConversionStatus.PARTIAL_SUCCESS,
            ):
                logger.info(f"OCR fallback succeeded for {document_url}")
                return result.document.export_to_markdown()
            logger.error(
                f"OCR fallback also failed for {document_url} (status={result.status})"
            )
        except Exception as e:
            logger.error(f"OCR fallback error for {document_url}: {e}")

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return ""
