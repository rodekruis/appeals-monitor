"""Tests for the Appeals Monitor pipeline."""

import json
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
    CashInfo,
    AppealExtraction,
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

    def test_planned_intervention(self):
        intv = PlannedIntervention(
            sector="Health",
            budget=100000,
            people_targeted=5000,
            activities="Primary healthcare services",
        )
        assert intv.sector.value == "Health"
        assert intv.budget == 100000

    def test_appeal_extraction(self):
        extraction = AppealExtraction(
            general_info=ResponseInfo(
                document_url="http://example.com/doc.pdf",
                appeal_code="MDRKE001",
                hazard="flood",
                country="Kenya",
                people_affected=None,
                people_targeted=50000,
                gaps_in_response="Lack of shelter",
            ),
            interventions=[
                PlannedIntervention(
                    sector="Health",
                    budget=100000,
                    people_targeted=5000,
                    activities="Primary healthcare",
                )
            ],
            cash_info=CashInfo(
                modality="cash transfer",
                financial_service_provider="M-Pesa",
                digital_tools="RedRose",
            ),
        )
        assert len(extraction.interventions) == 1
        assert extraction.interventions[0].sector.value == "Health"

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
    @patch("appeals_monitor.etl._create_converter")
    @patch("appeals_monitor.etl._get_page_count", return_value=5)
    @patch("appeals_monitor.etl.os.unlink")
    def test_success_on_first_attempt(self, mock_unlink, mock_page_count, mock_create):
        from docling.document_converter import ConversionStatus

        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status = ConversionStatus.SUCCESS
        mock_result.document.export_to_markdown.return_value = "# Test Document"
        mock_converter.convert.return_value = mock_result
        mock_create.return_value = mock_converter

        result = convert_document("/tmp/test.pdf")
        assert result == "# Test Document"
        mock_converter.convert.assert_called_once()

    @patch("appeals_monitor.etl._create_converter")
    @patch("appeals_monitor.etl._get_page_count", return_value=5)
    @patch("appeals_monitor.etl.os.unlink")
    def test_fallback_to_ocr_on_failure(
        self, mock_unlink, mock_page_count, mock_create
    ):
        from docling.document_converter import ConversionStatus

        mock_standard = MagicMock()
        mock_standard.convert.side_effect = Exception("Parse error")

        mock_ocr = MagicMock()
        mock_ocr_result = MagicMock()
        mock_ocr_result.status = ConversionStatus.SUCCESS
        mock_ocr_result.document.export_to_markdown.return_value = "# OCR Result"
        mock_ocr.convert.return_value = mock_ocr_result

        mock_create.side_effect = [mock_standard, mock_ocr]

        result = convert_document("/tmp/scanned.pdf")
        assert result == "# OCR Result"

    @patch("appeals_monitor.etl._create_converter")
    @patch("appeals_monitor.etl._get_page_count", return_value=5)
    @patch("appeals_monitor.etl.os.unlink")
    def test_returns_empty_when_both_fail(
        self, mock_unlink, mock_page_count, mock_create
    ):
        mock_conv = MagicMock()
        mock_conv.convert.side_effect = Exception("error")
        mock_create.return_value = mock_conv

        result = convert_document("/tmp/broken.pdf")
        assert result == ""


# --- storage tests ---


class TestStorage:
    @patch("appeals_monitor.storage._get_container_client")
    def test_document_exists_true(self, mock_container):
        from appeals_monitor.storage import document_exists

        mock_blob_client = MagicMock()
        mock_blob_client.exists.return_value = True
        mock_container.return_value.get_blob_client.return_value = mock_blob_client

        assert (
            document_exists(
                "https://go-api.ifrc.org/api/DownloadFile/12345/MDRKE001do",
                "DREF Operation",
            )
            is True
        )
        mock_container.return_value.get_blob_client.assert_called_with(
            "dref-operation/MDRKE001do.json"
        )

    @patch("appeals_monitor.storage._get_container_client")
    def test_document_exists_false(self, mock_container):
        from appeals_monitor.storage import document_exists

        mock_blob_client = MagicMock()
        mock_blob_client.exists.return_value = False
        mock_container.return_value.get_blob_client.return_value = mock_blob_client

        assert (
            document_exists(
                "https://go-api.ifrc.org/api/DownloadFile/56789/MDRXX001ea",
                "Emergency Appeal",
            )
            is False
        )

    @patch("appeals_monitor.storage._get_container_client")
    def test_document_exists_no_type(self, mock_container):
        """Backward compat: no doc_type falls back to root-level blob."""
        from appeals_monitor.storage import document_exists

        mock_blob_client = MagicMock()
        mock_blob_client.exists.return_value = True
        mock_container.return_value.get_blob_client.return_value = mock_blob_client

        assert document_exists("https://example.com/doc/1234") is True
        mock_container.return_value.get_blob_client.assert_called_with("1234.json")

    @patch("appeals_monitor.storage._get_container_client")
    def test_upload_document(self, mock_container):
        from appeals_monitor.storage import upload_document

        name = upload_document(
            "https://go-api.ifrc.org/api/DownloadFile/12345/MDRKE001do",
            "# Doc",
            "DREF Operation",
        )
        assert name == "dref-operation/MDRKE001do.json"
        mock_container.return_value.upload_blob.assert_called_once()
        call_kwargs = mock_container.return_value.upload_blob.call_args
        payload = json.loads(call_kwargs.kwargs["data"])
        assert (
            payload["document_url"]
            == "https://go-api.ifrc.org/api/DownloadFile/12345/MDRKE001do"
        )
        assert payload["markdown"] == "# Doc"
        assert payload["document_type"] == "DREF Operation"
        assert "parsed_at" in payload

    @patch("appeals_monitor.storage._get_container_client")
    def test_list_unprocessed_skips_processed(self, mock_container):
        from appeals_monitor.storage import list_unprocessed

        blob1 = MagicMock()
        blob1.name = "1.json"
        blob2 = MagicMock()
        blob2.name = "2.json"
        mock_container.return_value.list_blobs.return_value = [blob1, blob2]

        mock_container.return_value.download_blob.side_effect = lambda name: MagicMock(
            readall=MagicMock(
                return_value=json.dumps(
                    {
                        "document_url": f"http://example.com/{name}",
                        "markdown": "# doc",
                        "parsed_at": "2026-01-01",
                    }
                    | ({"processed_at": "2026-01-02"} if name == "1.json" else {})
                ).encode()
            )
        )

        docs = list(list_unprocessed())
        assert len(docs) == 1
        assert docs[0]["blob_name"] == "2.json"

    @patch("appeals_monitor.storage._get_container_client")
    def test_mark_processed(self, mock_container):
        from appeals_monitor.storage import mark_processed

        original = json.dumps(
            {"document_url": "http://example.com", "markdown": "# doc"}
        ).encode()
        mock_container.return_value.download_blob.return_value.readall.return_value = (
            original
        )

        mark_processed("1.json", {"general_info": {"appeal_code": "MDR001"}})

        call_kwargs = mock_container.return_value.upload_blob.call_args
        updated = json.loads(call_kwargs.kwargs["data"])
        assert "processed_at" in updated
        assert updated["analysis"]["general_info"]["appeal_code"] == "MDR001"


# --- run_etl tests ---


class TestRunEtl:
    @patch("appeals_monitor.etl.upload_document")
    @patch("appeals_monitor.etl.convert_document", return_value="# Markdown")
    @patch(
        "appeals_monitor.etl.get_documents",
        return_value=[("http://example.com/1.pdf", "/tmp/1.pdf", "DREF Operation")],
    )
    def test_uploads_converted_documents(self, mock_get, mock_convert, mock_upload):
        from appeals_monitor.etl import run_etl

        count = run_etl(last_n_days=7)
        assert count == 1
        mock_upload.assert_called_once_with(
            "http://example.com/1.pdf", "# Markdown", "DREF Operation"
        )

    @patch("appeals_monitor.etl.upload_document")
    @patch("appeals_monitor.etl.convert_document", return_value="")
    @patch(
        "appeals_monitor.etl.get_documents",
        return_value=[("http://example.com/1.pdf", "/tmp/1.pdf", "DREF Operation")],
    )
    def test_skips_empty_conversions(self, mock_get, mock_convert, mock_upload):
        from appeals_monitor.etl import run_etl

        count = run_etl(last_n_days=7)
        assert count == 0
        mock_upload.assert_not_called()

    @patch("appeals_monitor.etl.document_exists", return_value=True)
    @patch("appeals_monitor.etl._download_document")
    @patch("appeals_monitor.etl.requests.get")
    def test_skips_existing_documents(
        self, mock_api_get, mock_download, mock_exists, monkeypatch
    ):
        from appeals_monitor.etl import get_documents

        monkeypatch.setenv("GO_AUTH_TOKEN", "test")
        mock_api_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "results": [
                        {
                            "document_url": "http://example.com/1.pdf",
                            "type": "DREF Operation",
                        }
                    ],
                }
            ),
        )
        mock_api_get.return_value.raise_for_status = MagicMock()

        docs = get_documents(last_n_days=7)
        assert len(docs) == 0
        mock_download.assert_not_called()


# --- run_analysis tests ---


class TestRunAnalysis:
    @patch("appeals_monitor.monitor.notify")
    @patch("appeals_monitor.monitor.mark_processed")
    @patch("appeals_monitor.monitor.analyze_document")
    @patch("appeals_monitor.monitor.create_agent_pipeline")
    @patch("appeals_monitor.monitor.list_unprocessed")
    def test_analyzes_and_notifies(
        self,
        mock_list,
        mock_create_agent,
        mock_analyze,
        mock_mark,
        mock_notify,
        monkeypatch,
    ):
        from appeals_monitor.monitor import run_analysis

        monkeypatch.setenv("OPENAI_ENDPOINT", "https://test.openai.azure.com/")
        monkeypatch.setenv("OPENAI_API_VERSION", "2024-12-01")

        mock_list.return_value = iter(
            [
                {
                    "document_url": "http://ex.com/1",
                    "markdown": "# Doc",
                    "blob_name": "1.json",
                },
            ]
        )
        mock_analyze.return_value = {
            "document_url": "http://ex.com/1",
            "general_info": {},
        }

        results = run_analysis()
        assert len(results) == 1
        mock_analyze.assert_called_once()
        mock_mark.assert_called_once_with("1.json", results[0])
        mock_notify.assert_called_once()

    @patch("appeals_monitor.monitor.notify")
    @patch("appeals_monitor.monitor.list_unprocessed")
    def test_no_documents_skips_notification(self, mock_list, mock_notify, monkeypatch):
        from appeals_monitor.monitor import run_analysis

        monkeypatch.setenv("OPENAI_ENDPOINT", "https://test.openai.azure.com/")
        monkeypatch.setenv("OPENAI_API_VERSION", "2024-12-01")

        mock_list.return_value = iter([])

        results = run_analysis()
        assert len(results) == 0
        mock_notify.assert_not_called()
