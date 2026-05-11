"""Document analysis functions: LLM-based extraction of structured data from appeal documents."""

from enum import Enum
from pathlib import Path
from typing import List, Union
from datetime import date, datetime

from jinja2 import Environment, FileSystemLoader
from langchain.agents import create_agent
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field, field_validator

from appeals_monitor.logger import logger

# --- Pydantic models ---


class Sector(str, Enum):
    """IFRC Surge Catalogue sectors."""

    CVA = "Cash and Vouchers Assistance (CVA)"
    COMMUNICATIONS = "Communications"
    CEA = "Community Engagement and Accountability (CEA)"
    DIGITAL_SYSTEMS = "Digital Systems, Tools & Information Technology"
    GREEN_RESPONSE = "Green Response (GR)"
    HEALTH = "Health"
    HUMANITARIAN_DIPLOMACY = "Humanitarian Diplomacy"
    INFORMATION_MANAGEMENT = "Information Management (IM)"
    LIVELIHOODS = "Livelihoods and Basic Needs"
    LOGISTICS = "Logistics"
    MIGRATION = "Migration"
    OPERATIONS_MANAGEMENT = "Operations Management"
    PMER = "Planning, Monitoring, Evaluation and Reporting (PMER)"
    PGI = "Protection, Gender, and Inclusion (PGI)"
    RELIEF = "Relief"
    SECURITY = "Security"
    SHELTER = "Shelter"
    STRATEGIC_PARTNERSHIPS = "Strategic Partnerships and Resource Mobilisation (SPRM)"
    WASH = "Water, Sanitation and Hygiene (WASH)"


# Mapping from Kobo choice names to Sector enum values
KOBO_CHOICE_TO_SECTOR = {
    "cva": Sector.CVA,
    "communications": Sector.COMMUNICATIONS,
    "cea": Sector.CEA,
    "digital_systems": Sector.DIGITAL_SYSTEMS,
    "green_response": Sector.GREEN_RESPONSE,
    "health": Sector.HEALTH,
    "humanitarian_diplomacy": Sector.HUMANITARIAN_DIPLOMACY,
    "information_management": Sector.INFORMATION_MANAGEMENT,
    "livelihoods": Sector.LIVELIHOODS,
    "logistics": Sector.LOGISTICS,
    "migration": Sector.MIGRATION,
    "operations_management": Sector.OPERATIONS_MANAGEMENT,
    "pmer": Sector.PMER,
    "pgi": Sector.PGI,
    "relief": Sector.RELIEF,
    "security": Sector.SECURITY,
    "shelter": Sector.SHELTER,
    "strategic_partnerships": Sector.STRATEGIC_PARTNERSHIPS,
    "wash": Sector.WASH,
}

# Date formats the LLM commonly returns
_DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d"]


def _parse_flexible_date(value) -> date | None:
    """Parse a date string in various formats the LLM might return."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        # Last resort: let Python try to parse it
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


class ResponseInfo(BaseModel):
    document_url: str
    appeal_code: str
    hazard: str
    country: str
    people_affected: Union[int, None]
    people_targeted: Union[int, None]
    start_date: Union[date, None] = None
    end_date: Union[date, None] = None
    gaps_in_response: str

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def parse_date_flexibly(cls, v):
        return _parse_flexible_date(v)


class PlannedIntervention(BaseModel):
    sector: Sector
    budget: Union[int, None]
    people_targeted: Union[int, None]
    activities: str


class CashInfo(BaseModel):
    modality: str
    financial_service_provider: str
    digital_tools: str


class AppealExtraction(BaseModel):
    """Combined extraction result for all sections of an appeal document."""

    general_info: ResponseInfo
    interventions: List[PlannedIntervention] = Field(default_factory=list)
    cash_info: CashInfo


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
