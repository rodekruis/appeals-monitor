"""Document analysis functions: LLM-based extraction of structured data from appeal documents."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from langchain.agents import create_agent
from langchain_openai import AzureChatOpenAI

from appeals_monitor.config import logger
from appeals_monitor.models import Sector, AppealExtraction, country_to_iso3

# --- Prompt template ---

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_jinja_env = Environment(
    loader=FileSystemLoader(_PROMPTS_DIR),
    keep_trailing_newline=True,
)
_EXTRACTION_TEMPLATE = _jinja_env.get_template("analysis.md")

# Build the sector bullet list dynamically from the enum so it stays in sync
_SECTOR_LIST = "\n".join(f"        * {s.value}" for s in Sector)


def _render_prompt(document: str) -> str:
    return _EXTRACTION_TEMPLATE.render(document=document, sector_list=_SECTOR_LIST)


# --- Agent factory ---


def create_agent_pipeline(model: AzureChatOpenAI):
    """Creates and returns the extraction agent. Call once and reuse across documents."""
    return create_agent(model, response_format=AppealExtraction)


# --- Document analysis ---


def analyze_document(
    markdown: str,
    doc_url: str,
    agent,
) -> dict:
    """Analyzes a markdown document using an LLM agent to extract structured information.

    Args:
        markdown: The document content in markdown format.
        doc_url: The source URL of the document.
        agent: The extraction agent (from create_agent_pipeline).

    Returns a dict with general_info, interventions, and cash_info.
    """
    doc_result = {"document_url": doc_url}

    try:
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": _render_prompt(markdown),
                    }
                ]
            }
        )
    except Exception as exc:
        logger.error(f"Agent invocation failed for {doc_url}: {exc}")
        doc_result["general_info"] = None
        doc_result["interventions"] = None
        doc_result["cash_info"] = None
        return doc_result

    try:
        extraction: AppealExtraction = result["structured_response"]
    except KeyError as exc:
        logger.error(f"Structured response missing for {doc_url}: {exc}")
        doc_result["general_info"] = None
        doc_result["interventions"] = None
        doc_result["cash_info"] = None
        return doc_result

    try:
        general_info = extraction.general_info.model_dump(mode="json")
    except Exception as exc:
        logger.error(f"Failed to serialize general info for {doc_url}: {exc}")
        doc_result["general_info"] = None
        doc_result["interventions"] = None
        doc_result["cash_info"] = None
        return doc_result

    general_info["document_url"] = (
        doc_url  # injected post-extraction for notification purposes; not part of the Pydantic model
    )
    general_info["country_iso3"] = country_to_iso3(
        general_info.get("country")
    )  # derived from the extracted country name; not part of the Pydantic model

    try:
        interventions = {
            "interventions": [
                i.model_dump(mode="json") for i in extraction.interventions
            ]
        }
    except Exception as exc:
        logger.error(f"Failed to serialize interventions for {doc_url}: {exc}")
        doc_result["general_info"] = None
        doc_result["interventions"] = None
        doc_result["cash_info"] = None
        return doc_result

    try:
        cash_info = extraction.cash_info.model_dump(mode="json")
    except Exception as exc:
        logger.error(f"Failed to serialize cash info for {doc_url}: {exc}")
        doc_result["general_info"] = None
        doc_result["interventions"] = None
        doc_result["cash_info"] = None
        return doc_result

    doc_result["general_info"] = general_info
    doc_result["interventions"] = interventions
    doc_result["cash_info"] = cash_info
    logger.info(f"Extracted: {doc_result['general_info']}")

    return doc_result
