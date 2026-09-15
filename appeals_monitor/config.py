"""Application configuration: logging setup."""

import logging


class ConfigError(RuntimeError):
    """Required configuration is missing or invalid; the run cannot continue."""


logger = logging.getLogger("appeals_monitor")
logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("docling").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
logging.getLogger("RapidOCR").setLevel(logging.WARNING)
