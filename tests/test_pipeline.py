"""Tests for the Appeals Monitor pipeline."""

import pytest
from unittest.mock import patch, MagicMock

from appeals_monitor.notify import (
    format_summary,
    get_recipients_from_kobo,
    _filter_results_by_sectors,
)
from appeals_monitor.analysis import (
    ResponseInfo,
    PlannedIntervention,
    PlannedInterventionList,
    CashInfo,
)
from appeals_monitor.etl import convert_document

# --- Pydantic model tests ---


class TestModels:
    def test_response_info_with_none_fields(self):
        info = ResponseInfo(
            document_url="http://example.com/doc.pdf",
            appeal_code="MDRKE001",
            hazard="flood",
            country="Kenya",
            people_affected=None,
            people_targeted=50000,
            start_date=None,
            end_date=None,
            gaps_in_response="Lack of shelter materials",
        )
        assert info.appeal_code == "MDRKE001"
        assert info.people_affected is None

    def test_planned_intervention_list(self):
        intv = PlannedIntervention(
            sector="Health",
            budget=100000,
            people_targeted=5000,
            activities="Primary healthcare services",
        )
        intv_list = PlannedInterventionList(interventions=[intv])
        assert len(intv_list.interventions) == 1
        assert intv_list.interventions[0].sector == "Health"

    def test_cash_info(self):
        cash = CashInfo(
            modality="cash transfer",
            financial_service_provider="M-Pesa",
            digital_tools="RedRose",
        )
        assert cash.modality == "cash transfer"


# --- format_summary tests ---


class TestFormatSummary:
    def test_empty_results(self):
        summary = format_summary([])
        assert "No new appeal documents" in summary

    def test_single_document(self):
        results = [
            {
                "document_url": "http://example.com/appeal.pdf",
                "general_info": {
                    "appeal_code": "MDRKE001",
                    "hazard": "flood",
                    "country": "Kenya",
                    "people_affected": 100000,
                    "people_targeted": 50000,
                    "start_date": "2026-01-01",
                    "end_date": "2026-06-30",
                    "gaps_in_response": "Shelter gap",
                },
                "interventions": {
                    "interventions": [
                        {
                            "sector": "Shelter",
                            "budget": 200000,
                            "people_targeted": 10000,
                            "activities": "Distribute tents",
                        }
                    ]
                },
                "cash_info": {
                    "modality": "cash transfer",
                    "financial_service_provider": "M-Pesa",
                    "digital_tools": "RedRose",
                },
            }
        ]
        summary = format_summary(results)
        assert "MDRKE001" in summary
        assert "Kenya" in summary
        assert "Shelter" in summary
        assert "M-Pesa" in summary
        assert "1 document(s) processed" in summary

    def test_document_with_none_analysis(self):
        """Documents with failed analysis (None values) should not crash."""
        results = [
            {
                "document_url": "http://example.com/broken.pdf",
                "general_info": None,
                "interventions": None,
                "cash_info": None,
            }
        ]
        summary = format_summary(results)
        assert "1 document(s) processed" in summary


# --- get_recipients_from_kobo tests ---


class TestGetRecipientsFromKobo:
    def test_missing_config_returns_empty(self, monkeypatch):
        monkeypatch.delenv("KOBO_API_TOKEN", raising=False)
        monkeypatch.delenv("KOBO_FORM_UID", raising=False)
        result = get_recipients_from_kobo()
        assert result == []

    def test_filters_active_only(self, monkeypatch, requests_mock):
        monkeypatch.setenv("KOBO_API_TOKEN", "test-token")
        monkeypatch.setenv("KOBO_FORM_UID", "abc123")
        monkeypatch.setenv("KOBO_API_URL", "https://kobo.test")

        requests_mock.get(
            "https://kobo.test/api/v2/assets/abc123/data.json",
            json={
                "results": [
                    {
                        "email": "active@example.com",
                        "active": "yes",
                        "sectors_of_interest": "health wash",
                        "_submission_time": "2026-01-01",
                    },
                    {
                        "email": "inactive@example.com",
                        "active": "no",
                        "_submission_time": "2026-01-01",
                    },
                ],
                "next": None,
            },
        )

        result = get_recipients_from_kobo()
        emails = [r["email"] for r in result]
        assert "active@example.com" in emails
        assert "inactive@example.com" not in emails
        # Check sector preferences were parsed
        active = next(r for r in result if r["email"] == "active@example.com")
        assert "Health" in active["sectors"]
        assert "Water, Sanitation and Hygiene (WASH)" in active["sectors"]

    def test_latest_submission_wins(self, monkeypatch, requests_mock):
        monkeypatch.setenv("KOBO_API_TOKEN", "test-token")
        monkeypatch.setenv("KOBO_FORM_UID", "abc123")
        monkeypatch.setenv("KOBO_API_URL", "https://kobo.test")

        requests_mock.get(
            "https://kobo.test/api/v2/assets/abc123/data.json",
            json={
                "results": [
                    {
                        "email": "user@example.com",
                        "active": "yes",
                        "_submission_time": "2026-01-01",
                    },
                    {
                        "email": "user@example.com",
                        "active": "no",
                        "_submission_time": "2026-01-15",
                    },
                ],
                "next": None,
            },
        )

        result = get_recipients_from_kobo()
        emails = [r["email"] for r in result]
        assert "user@example.com" not in emails

    def test_case_insensitive_dedup(self, monkeypatch, requests_mock):
        monkeypatch.setenv("KOBO_API_TOKEN", "test-token")
        monkeypatch.setenv("KOBO_FORM_UID", "abc123")
        monkeypatch.setenv("KOBO_API_URL", "https://kobo.test")

        requests_mock.get(
            "https://kobo.test/api/v2/assets/abc123/data.json",
            json={
                "results": [
                    {
                        "email": "User@Example.com",
                        "active": "yes",
                        "_submission_time": "2026-01-01",
                    },
                    {
                        "email": "user@example.com",
                        "active": "yes",
                        "_submission_time": "2026-01-02",
                    },
                ],
                "next": None,
            },
        )

        result = get_recipients_from_kobo()
        assert len(result) == 1
        assert result[0]["email"] == "user@example.com"


# --- filter_results_by_sectors tests ---


class TestFilterResultsBySectors:
    def test_empty_sectors_returns_all(self):
        results = [{"interventions": {"interventions": [{"sector": "Health"}]}}]
        assert _filter_results_by_sectors(results, set()) == results

    def test_matching_sector_included(self):
        results = [
            {"interventions": {"interventions": [{"sector": "Health"}]}},
            {"interventions": {"interventions": [{"sector": "Shelter"}]}},
        ]
        filtered = _filter_results_by_sectors(results, {"Health"})
        assert len(filtered) == 1
        assert filtered[0]["interventions"]["interventions"][0]["sector"] == "Health"

    def test_no_match_returns_empty(self):
        results = [
            {"interventions": {"interventions": [{"sector": "Logistics"}]}},
        ]
        filtered = _filter_results_by_sectors(results, {"Health", "Shelter"})
        assert filtered == []

    def test_doc_with_multiple_interventions_matches_any(self):
        results = [
            {
                "interventions": {
                    "interventions": [
                        {"sector": "Logistics"},
                        {"sector": "Health"},
                    ]
                }
            },
        ]
        filtered = _filter_results_by_sectors(results, {"Health"})
        assert len(filtered) == 1

    def test_missing_interventions_not_included(self):
        results = [{"interventions": None}]
        filtered = _filter_results_by_sectors(results, {"Health"})
        assert filtered == []


# --- convert_document tests ---


class TestConvertDocument:
    @patch("appeals_monitor.etl.requests.get")
    @patch("appeals_monitor.etl._create_converter")
    @patch("appeals_monitor.etl._get_page_count", return_value=5)
    def test_success_on_first_attempt(self, mock_page_count, mock_create, mock_get):
        from docling.document_converter import ConversionStatus

        # Mock PDF download
        mock_response = MagicMock()
        mock_response.content = b"fake pdf"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # Mock converter
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status = ConversionStatus.SUCCESS
        mock_result.document.export_to_markdown.return_value = "# Test Document"
        mock_converter.convert.return_value = mock_result
        mock_create.return_value = mock_converter

        result = convert_document("http://example.com/doc.pdf")
        assert result == "# Test Document"
        mock_converter.convert.assert_called_once()

    @patch("appeals_monitor.etl.requests.get")
    @patch("appeals_monitor.etl._create_converter")
    @patch("appeals_monitor.etl._get_page_count", return_value=5)
    def test_fallback_to_ocr_on_failure(self, mock_page_count, mock_create, mock_get):
        from docling.document_converter import ConversionStatus

        # Mock PDF download
        mock_response = MagicMock()
        mock_response.content = b"fake pdf"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # First call (standard) raises, second call (OCR) succeeds
        mock_standard = MagicMock()
        mock_standard.convert.side_effect = Exception("Parse error")

        mock_ocr = MagicMock()
        mock_ocr_result = MagicMock()
        mock_ocr_result.status = ConversionStatus.SUCCESS
        mock_ocr_result.document.export_to_markdown.return_value = "# OCR Result"
        mock_ocr.convert.return_value = mock_ocr_result

        mock_create.side_effect = [mock_standard, mock_ocr]

        result = convert_document("http://example.com/scanned.pdf")
        assert result == "# OCR Result"

    @patch("appeals_monitor.etl.requests.get")
    @patch("appeals_monitor.etl._create_converter")
    @patch("appeals_monitor.etl._get_page_count", return_value=5)
    def test_returns_empty_when_both_fail(self, mock_page_count, mock_create, mock_get):
        # Mock PDF download
        mock_response = MagicMock()
        mock_response.content = b"fake pdf"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # Both converters fail
        mock_conv = MagicMock()
        mock_conv.convert.side_effect = Exception("error")
        mock_create.return_value = mock_conv

        result = convert_document("http://example.com/broken.pdf")
        assert result == ""
