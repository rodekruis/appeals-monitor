"""Document analysis functions: LLM-based extraction of structured data from appeal documents."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from langchain.agents import create_agent
from langchain_openai import AzureChatOpenAI

from appeals_monitor.config import logger
from appeals_monitor.models import Sector, AppealExtraction

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
        extraction: AppealExtraction = result["structured_response"]
        doc_result["general_info"] = extraction.general_info.model_dump(mode="json")
        doc_result["interventions"] = {
            "interventions": [
                i.model_dump(mode="json") for i in extraction.interventions
            ]
        }
        doc_result["cash_info"] = extraction.cash_info.model_dump(mode="json")
        logger.info(f"Extracted: {doc_result['general_info']}")
    except Exception as e:
        logger.error(f"Failed to extract data from {doc_url}: {e}")
        doc_result["general_info"] = None
        doc_result["interventions"] = None
        doc_result["cash_info"] = None

    return doc_result
