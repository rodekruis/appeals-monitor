"""ETL functions: fetching and converting IFRC appeal documents."""

from typing import List
from datetime import datetime, timedelta
import os

import requests
from docling.document_converter import DocumentConverter

from appeals_monitor.logger import logger


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
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        data = response.json()
    else:
        logger.error(f"Error: {response.status_code} - {response.text}")
        return []
    documents = [d.get("document_url") for d in data.get("results", [])]
    return documents


def convert_document(document_url: str) -> str:
    """Converts a document from PDF to markdown format."""
    converter = DocumentConverter()
    try:
        markdown = converter.convert(document_url).document.export_to_markdown()
        return markdown
    except Exception as e:
        logger.error(f"Error converting document: {e}")
        return ""
