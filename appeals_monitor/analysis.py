"""Document analysis functions: LLM-based extraction of structured data from appeal documents."""

from enum import Enum
from typing import List, Union
from datetime import date, datetime

from langchain.agents import create_agent
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field, field_validator

from appeals_monitor.logger import logger

# --- Pydantic models ---


class Sector(str, Enum):
    """IFRC Surge Catalogue sectors."""

    ADMINISTRATION = "Administration"
    CVA = "Cash and Vouchers Assistance (CVA)"
    COMMUNICATIONS = "Communications"
    CEA = "Community Engagement and Accountability (CEA)"
    DIGITAL_SYSTEMS = "Digital Systems, Tools & Information Technology"
    EMERGENCY_NEEDS_ASSESSMENT = "Emergency Needs Assessment"
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
    RISK_MANAGEMENT = "Risk Management"
    SECURITY = "Security"
    SHELTER = "Shelter"
    STRATEGIC_PARTNERSHIPS = "Strategic Partnerships and Resource Mobilisation"
    WASH = "Water, Sanitation and Hygiene (WASH)"


# Mapping from Kobo choice names to Sector enum values
KOBO_CHOICE_TO_SECTOR = {
    "administration": Sector.ADMINISTRATION,
    "cva": Sector.CVA,
    "communications": Sector.COMMUNICATIONS,
    "cea": Sector.CEA,
    "digital_systems": Sector.DIGITAL_SYSTEMS,
    "emergency_needs_assessment": Sector.EMERGENCY_NEEDS_ASSESSMENT,
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
    "risk_management": Sector.RISK_MANAGEMENT,
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


class PlannedInterventionList(BaseModel):
    interventions: List[PlannedIntervention] = Field(None, title="Interventions")


class CashInfo(BaseModel):
    modality: str
    financial_service_provider: str
    digital_tools: str


# --- Prompt templates ---

PROMPT_GENERAL_INFO = """Read this document and extract information in a structured format. The information is usually
at the beginning of the document, in tabular format. If it's not there, look for it in the rest of the document.
Do not make up information, if you can't find it in the document, just leave the field empty or with a value of None. The information to extract is:
    - Appeal code: The unique code of the appeal, usually in format "MDRXXYY"
    - Hazard: The type of hazard (e.g. flood, earthquake, etc.)
    - Country: The country or countries affected by the disaster
    - People affected: The total number of people affected by the disaster
    - People targeted: The total number of people targeted with assistance in the appeal
    - Start date: The start date of the operation (in YYYY-MM-DD format)
    - End date: The end date of the operation (in YYYY-MM-DD format)
    - Gaps in response: A brief description of the gaps in the humanitarian response that the appeal aims to address

Document:
{document}"""

PROMPT_INTERVENTIONS = """Read this document and extract information in a structured format. The information on interventions is usually towards the end of the document, divided by sector. If it's not there, look for it in the rest of the document.
Do not make up information, if you can't find it in the document, just leave the field empty or with a value of None. The information to extract is a list of interventions, with the following fields:
    - Sector: The sector of the intervention. You MUST match the sector name to the closest one from this list:
        * Administration
        * Cash and Vouchers Assistance (CVA)
        * Communications
        * Community Engagement and Accountability (CEA)
        * Digital Systems, Tools & Information Technology
        * Emergency Needs Assessment
        * Health
        * Humanitarian Diplomacy
        * Information Management (IM)
        * Livelihoods and Basic Needs
        * Logistics
        * Migration
        * Operations Management
        * Planning, Monitoring, Evaluation and Reporting (PMER)
        * Protection, Gender, and Inclusion (PGI)
        * Relief
        * Risk Management
        * Security
        * Shelter
        * Strategic Partnerships and Resource Mobilisation
        * Water, Sanitation and Hygiene (WASH)
      Use the closest matching sector from this list. Only discard an intervention if its sector clearly does not fit any of the above.
    - Budget: The budget allocated for the intervention in CHF
    - People targeted: The number of people targeted with the intervention
    - Activities: A brief description of the activities planned in the intervention

Document:
{document}"""

PROMPT_CASH_INFO = """Read this document and determine if a cash intervention is planned in the appeal. If yes, extract the following information:
    - Modality: The modality of the cash intervention (e.g. cash transfer, voucher, etc.)
    - Financial service provider: The financial service provider (FSP) that can or will be used for the cash intervention
    - Digital tools: The digital tools that can or will be used for the cash intervention (e.g. mobile money, RedRose, etc.)
Do not make up information, if you can't find it in the document, just leave the field empty.

Document:
{document}"""


# --- Agent factory ---


def create_agents(model: AzureChatOpenAI) -> tuple:
    """Creates and returns the three extraction agents. Call once and reuse across documents."""
    agent_general_info = create_agent(model, response_format=ResponseInfo)
    agent_interventions = create_agent(model, response_format=PlannedInterventionList)
    agent_cash = create_agent(model, response_format=CashInfo)
    return agent_general_info, agent_interventions, agent_cash


# --- Document analysis ---


def analyze_document(
    markdown: str,
    doc_url: str,
    agents: tuple,
) -> dict:
    """Analyzes a markdown document using LLM agents to extract structured information.

    Args:
        markdown: The document content in markdown format.
        doc_url: The source URL of the document.
        agents: Tuple of (agent_general_info, agent_interventions, agent_cash).

    Returns a dict with general_info, interventions, and cash_info.
    """
    agent_general_info, agent_interventions, agent_cash = agents
    doc_result = {"document_url": doc_url}

    # Extract general info
    try:
        result = agent_general_info.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": PROMPT_GENERAL_INFO.format(document=markdown),
                    }
                ]
            }
        )
        doc_result["general_info"] = result["structured_response"].model_dump(
            mode="json"
        )
        logger.info(f"General info: {doc_result['general_info']}")
    except Exception as e:
        logger.error(f"Failed to extract general info from {doc_url}: {e}")
        doc_result["general_info"] = None

    # Extract interventions
    try:
        result = agent_interventions.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": PROMPT_INTERVENTIONS.format(document=markdown),
                    }
                ]
            }
        )
        doc_result["interventions"] = result["structured_response"].model_dump(
            mode="json"
        )
        logger.info(f"Interventions: {doc_result['interventions']}")
    except Exception as e:
        logger.error(f"Failed to extract interventions from {doc_url}: {e}")
        doc_result["interventions"] = None

    # Extract cash info
    try:
        result = agent_cash.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": PROMPT_CASH_INFO.format(document=markdown),
                    }
                ]
            }
        )
        doc_result["cash_info"] = result["structured_response"].model_dump(mode="json")
        logger.info(f"Cash info: {doc_result['cash_info']}")
    except Exception as e:
        logger.error(f"Failed to extract cash info from {doc_url}: {e}")
        doc_result["cash_info"] = None

    return doc_result
