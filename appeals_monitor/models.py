"""Domain models for appeal document extraction."""

import logging
from enum import Enum
from typing import List, Union
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

# --- Country ISO3 mapping ---

# country_converter resolves messy real-world country names — colloquial ("South Korea"),
# historical ("Burma"), names containing "and" ("Trinidad and Tobago"), and multi-country
# strings — via a maintained regex database. Its matcher is verbose, so silence its logger.
logging.getLogger("country_converter").setLevel(logging.CRITICAL)

# Sentinel country_converter returns for names it cannot resolve.
_ISO3_NOT_FOUND = "__ISO3_NOT_FOUND__"

_country_converter = None


def _get_country_converter():
    """Lazily build and cache the CountryConverter (loads a pandas dataset once)."""
    global _country_converter
    if _country_converter is None:
        import country_converter

        _country_converter = country_converter.CountryConverter()
    return _country_converter


def country_to_iso3(country: Union[str, None]) -> Union[str, None]:
    """Derive ISO 3166-1 alpha-3 code(s) from a free-text country name.

    Handles colloquial names ("South Korea"), historical names ("Burma"), names that
    themselves contain "and" ("Trinidad and Tobago"), and multi-country strings
    ("Kenya and Somalia" -> "KEN, SOM"). Unresolvable tokens (e.g. "Africa Region") are
    dropped. Returns None when nothing matches.
    """
    if not country or not country.strip():
        return None
    # enforce_list=True makes convert() return one list of codes per input name.
    matches = _get_country_converter().convert(
        country, to="ISO3", not_found=_ISO3_NOT_FOUND, enforce_list=True
    )
    codes: list[str] = []
    for match in matches:
        candidates = match if isinstance(match, list) else [match]
        for code in candidates:
            if code != _ISO3_NOT_FOUND and code not in codes:
                codes.append(code)
    return ", ".join(codes) if codes else None


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
    NSD = "National Society Development"
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
    "national_society_development": Sector.NSD,
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
    appeal_code: str
    hazard: str
    country: str
    event_description: str
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
