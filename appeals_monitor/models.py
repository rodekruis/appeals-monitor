"""Domain models for appeal document extraction."""

from enum import Enum
from typing import List, Union
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

# --- Sector enum ---


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

# --- Date parsing ---

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


# --- Pydantic models ---


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
