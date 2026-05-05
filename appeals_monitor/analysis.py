"""Document analysis functions: LLM-based extraction of structured data from appeal documents."""

from typing import List, Union
from datetime import date

from langchain.agents import create_agent
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field

from appeals_monitor.logger import logger


class ResponseInfo(BaseModel):
    document_url: str
    appeal_code: str
    hazard: str
    country: str
    people_affected: Union[int, None]
    people_targeted: Union[int, None]
    start_date: Union[date, None]
    end_date: Union[date, None]
    gaps_in_response: str


class PlannedIntervention(BaseModel):
    sector: str
    budget: Union[int, None]
    people_targeted: Union[int, None]
    activities: str


class PlannedInterventionList(BaseModel):
    interventions: List[PlannedIntervention] = Field(None, title="Interventions")


class CashInfo(BaseModel):
    modality: str
    financial_service_provider: str
    digital_tools: str


def analyze_document(markdown: str, doc_url: str, model: AzureChatOpenAI) -> dict:
    """Analyzes a markdown document using LLM agents to extract structured information.

    Returns a dict with general_info, interventions, and cash_info.
    """
    agent_general_info = create_agent(model, response_format=ResponseInfo)
    agent_interventions = create_agent(model, response_format=PlannedInterventionList)
    agent_cash = create_agent(model, response_format=CashInfo)

    doc_result = {"document_url": doc_url}

    # Extract general info
    result = agent_general_info.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": f"""
                    Read this document and extract information in a structured format. The information is usually
                    at the beginning of the document, in tabular format. If it's not there, look for it in the rest of the document.
                    Do not make up information, if you can't find it in the document, just leave the field empty or with a value of None. The information to extract is:
                        - Appeal code: The unique code of the appeal, usually in format "MDRXXYY"
                        - Hazard: The type of hazard (e.g. flood, earthquake, etc.)
                        - Country: The country or countries affected by the disaster
                        - People affected: The total number of people affected by the disaster
                        - People targeted: The total number of people targeted with assistance in the appeal
                        - Start date: The start date of the operation
                        - End date: The end date of the operation
                        - Gaps in response: A brief description of the gaps in the humanitarian response that the appeal aims to address

                    Document: \n {markdown}
                    """,
                }
            ]
        }
    )
    doc_result["general_info"] = result["structured_response"].model_dump(mode="json")
    logger.info(f"General info: {doc_result['general_info']}")

    # Extract interventions
    result = agent_interventions.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": f"""
                    Read this document and extract information in a structured format. The information on interventions is usually towards the end of the document, divided by sector. If it's not there, look for it in the rest of the document.
                    Do not make up information, if you can't find it in the document, just leave the field empty or with a value of None. The information to extract is a list of interventions, with the following fields:
                        - Sector: The sector of the intervention (e.g. health, shelter, etc.)
                        - Budget: The budget allocated for the intervention in CHF
                        - People targeted: The number of people targeted with the intervention
                        - Activities: A brief description of the activities planned in the intervention

                    Document: \n {markdown}
                    """,
                }
            ]
        }
    )
    doc_result["interventions"] = result["structured_response"].model_dump(mode="json")
    logger.info(f"Interventions: {doc_result['interventions']}")

    # Extract cash info
    result = agent_cash.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": f"""
                    Read this document and determine if a cash intervention is planned in the appeal. If yes, extract the following information:
                        - Modality: The modality of the cash intervention (e.g. cash transfer, voucher, etc.)
                        - Financial service provider: The financial service provider (FSP) that can or will be used for the cash intervention
                        - Digital tools: The digital tools that can or will be used for the cash intervention (e.g. mobile money, RedRose, etc.)
                    Do not make up information, if you can't find it in the document, just leave the field empty.

                    Document: \n {markdown}
                    """,
                }
            ]
        }
    )
    doc_result["cash_info"] = result["structured_response"].model_dump(mode="json")
    logger.info(f"Cash info: {doc_result['cash_info']}")

    return doc_result
