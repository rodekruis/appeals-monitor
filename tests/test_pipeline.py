"""Tests for the Appeals Monitor pipeline."""

import json
import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from appeals_monitor.notify import (
    format_summary,
    get_recipients_from_kobo,
    _filter_results_by_sectors,
)
from appeals_monitor.config import ConfigError
from appeals_monitor.feedback import (
    cycle_start_for,
    feedback_form_url,
    format_invite,
    personalised_url,
    recipient_id,
    replied_ids,
    run_feedback,
    scheduled_send_for,
)
from appeals_monitor.models import (
    ResponseInfo,
    PlannedIntervention,
    CashInfo,
    AppealExtraction,
    country_to_iso3,
)
from appeals_monitor.etl import _download_new_documents, convert_document


@patch("appeals_monitor.etl._download_document", return_value="/tmp/revision.pdf")
@patch("appeals_monitor.etl.document_exists", return_value=False)
def test_emergency_appeal_revision_is_downloaded(mock_exists, mock_download):
    records = [
        {
            "type": "Emergency Appeal Revision",
            "document_url": "https://example.com/revision.pdf",
        },
        {"type": "Final Report", "document_url": "https://example.com/final.pdf"},
    ]

    assert _download_new_documents(records) == [
        (
            "https://example.com/revision.pdf",
            "/tmp/revision.pdf",
            "Emergency Appeal Revision",
        )
    ]
    mock_download.assert_called_once_with("https://example.com/revision.pdf")


# --- Pydantic model tests ---


class TestModels:
    def test_response_info_with_none_fields(self):
        info = ResponseInfo(
            document_url="http://example.com/doc.pdf",
            appeal_code="MDRKE001",
            hazard="flood",
            country="Kenya",
            event_description="Heavy rains caused widespread flooding in western Kenya.",
            people_affected=None,
            people_targeted=50000,
            start_date=None,
            end_date=None,
            gaps_in_response="Lack of shelter materials",
        )
        assert info.appeal_code == "MDRKE001"
        assert info.people_affected is None

    def test_country_to_iso3_single(self):
        assert country_to_iso3("Kenya") == "KEN"

    def test_country_to_iso3_multi(self):
        assert country_to_iso3("Kenya and Somalia") == "KEN, SOM"

    def test_country_to_iso3_aliases_and_parentheticals(self):
        # Reversed-word-order names, parentheticals, and non-country tokens ("Africa
        # Region") that should be dropped while real countries are kept.
        assert (
            country_to_iso3("Democratic Republic of the Congo and Uganda") == "COD, UGA"
        )
        assert (
            country_to_iso3(
                "Democratic Republic of the Congo (DRC), Uganda, and Africa Region"
            )
            == "COD, UGA"
        )

    def test_country_to_iso3_fuzzy(self):
        # Common short/alternate names still resolve.
        assert country_to_iso3("Tanzania") == "TZA"

    def test_country_to_iso3_colloquial_and_historical(self):
        # Colloquial/short and historical names resolve via the maintained dataset.
        assert country_to_iso3("South Korea") == "KOR"
        assert country_to_iso3("Burma") == "MMR"
        assert country_to_iso3("Ivory Coast") == "CIV"

    def test_country_to_iso3_name_with_and_not_split(self):
        # A single country whose name contains "and" must not be split into two.
        assert country_to_iso3("Trinidad and Tobago") == "TTO"
        assert country_to_iso3("Bosnia and Herzegovina") == "BIH"

    def test_country_to_iso3_empty_or_unknown(self):
        assert country_to_iso3(None) is None
        assert country_to_iso3("") is None
        assert country_to_iso3("   ") is None
        assert country_to_iso3("Neverland") is None

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
                event_description="Heavy rains caused widespread flooding.",
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
        assert "Kenya" in summary
        assert "Shelter" in summary
        # No CVA intervention → cash section should be hidden even if cash_info is populated
        assert "M-Pesa" not in summary
        assert "1 new appeal document" in summary

    def test_document_type_rendered_in_heading(self):
        """document_type should appear in the document heading when present."""
        results = [
            {
                "document_url": "http://example.com/appeal.pdf",
                "document_type": "Emergency Appeal",
                "general_info": {
                    "hazard": "flood",
                    "country": "Kenya",
                },
                "interventions": None,
                "cash_info": None,
            }
        ]
        summary = format_summary(results)
        assert "Kenya" in summary
        assert "flood" in summary
        assert "Emergency Appeal" in summary

    def test_document_type_absent_no_dash(self):
        """When document_type is missing, no trailing ' - ' should be rendered."""
        results = [
            {
                "document_url": "http://example.com/appeal.pdf",
                "general_info": {
                    "hazard": "flood",
                    "country": "Kenya",
                },
                "interventions": None,
                "cash_info": None,
            }
        ]
        summary = format_summary(results)
        assert "Kenya - flood**" in summary

    def test_cash_section_shown_only_with_cva_intervention(self):
        """Cash section should only appear when there is a CVA intervention."""
        base = {
            "document_url": "http://example.com/appeal.pdf",
            "general_info": {
                "appeal_code": "MDRXX001",
                "hazard": "flood",
                "country": "TestCountry",
                "people_affected": 1000,
                "people_targeted": 500,
                "start_date": "2026-01-01",
                "end_date": "2026-06-30",
                "gaps_in_response": "",
            },
            "cash_info": {
                "modality": "cash transfer",
                "financial_service_provider": "M-Pesa",
                "digital_tools": "RedRose",
            },
        }

        # With CVA intervention → cash section visible
        with_cva = {
            **base,
            "interventions": {
                "interventions": [
                    {
                        "sector": "Cash and Vouchers Assistance (CVA)",
                        "budget": 100000,
                        "people_targeted": 500,
                        "activities": "Cash grants",
                    }
                ]
            },
        }
        summary = format_summary([with_cva])
        assert "M-Pesa" in summary
        assert "Cash and Voucher Assistance" in summary

        # Without CVA intervention → cash section hidden
        without_cva = {
            **base,
            "interventions": {
                "interventions": [
                    {
                        "sector": "Shelter",
                        "budget": 200000,
                        "people_targeted": 1000,
                        "activities": "Tents",
                    }
                ]
            },
        }
        summary = format_summary([without_cva])
        assert "M-Pesa" not in summary
        assert "Cash and Voucher Assistance" not in summary

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
        assert "1 new appeal document" in summary


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

        # Mock index.json read for _read_index call in _upsert_index_entry
        mock_container.return_value.get_blob_client.return_value.exists.return_value = (
            False
        )

        name = upload_document(
            "https://go-api.ifrc.org/api/DownloadFile/12345/MDRKE001do",
            "# Doc",
            "DREF Operation",
        )
        assert name == "dref-operation/MDRKE001do.json"
        mock_container.return_value.upload_blob.assert_called()
        # Find the call that uploaded the document (not index.json)
        calls = mock_container.return_value.upload_blob.call_args_list
        doc_upload_call = [c for c in calls if "dref-operation" in str(c)][0]
        payload = json.loads(doc_upload_call.kwargs["data"])
        assert (
            payload["document_url"]
            == "https://go-api.ifrc.org/api/DownloadFile/12345/MDRKE001do"
        )
        assert payload["markdown"] == "# Doc"
        assert payload["document_type"] == "DREF Operation"
        assert "parsed_at" in payload

    def test_blob_name_keeps_query_string(self):
        """Legacy Download.aspx?FileId=N URLs differ only in the query string;
        stripping it would collapse distinct documents onto one blob name."""
        from appeals_monitor.storage import _blob_name

        a = _blob_name("https://example.com/Download.aspx?FileId=118182")
        b = _blob_name("https://example.com/Download.aspx?FileId=118183")
        assert a == "Download.aspx?FileId=118182.json"
        assert a != b

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

        # Mock index.json read for _read_index call in _upsert_index_entry
        mock_container.return_value.get_blob_client.return_value.exists.return_value = (
            False
        )

        mark_processed("1.json", {"general_info": {"appeal_code": "MDR001"}})

        # Find the upload_blob call for the document (first call, not the index)
        calls = mock_container.return_value.upload_blob.call_args_list
        doc_call = [c for c in calls if "1.json" in str(c)][0]
        updated = json.loads(doc_call.kwargs["data"])
        assert "processed_at" in updated
        assert updated["analysis"]["general_info"]["appeal_code"] == "MDR001"

    @patch("appeals_monitor.storage._get_container_client")
    def test_list_unanalyzed_blob_names(self, mock_container):
        from appeals_monitor.storage import list_unanalyzed_blob_names

        index = {
            "a.json": {"has_analysis": False, "processed_at": "2026-01-02"},
            "b.json": {"has_analysis": True, "processed_at": "2026-01-02"},
            # No processed_at: fresh document, belongs to the daily pipeline.
            "c.json": {"has_analysis": False},
        }
        mock_blob = mock_container.return_value.get_blob_client.return_value
        mock_blob.exists.return_value = True
        mock_blob.download_blob.return_value.readall.return_value = json.dumps(
            index
        ).encode()

        assert list_unanalyzed_blob_names() == ["a.json"]
        assert list_unanalyzed_blob_names(require_processed=False) == [
            "a.json",
            "c.json",
        ]

    @patch("appeals_monitor.storage._get_container_client")
    def test_get_document(self, mock_container):
        from appeals_monitor.storage import get_document

        mock_blob = mock_container.return_value.get_blob_client.return_value
        mock_blob.exists.return_value = False
        assert get_document("missing.json") is None

        mock_blob.exists.return_value = True
        mock_blob.download_blob.return_value.readall.return_value = json.dumps(
            {"document_url": "http://example.com", "markdown": "# doc"}
        ).encode()
        doc = get_document("1.json")
        assert doc["blob_name"] == "1.json"
        assert doc["markdown"] == "# doc"


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
    @patch("appeals_monitor.monitor.create_model")
    @patch("appeals_monitor.monitor.list_unprocessed")
    def test_analyzes_and_notifies(
        self,
        mock_list,
        mock_create_model,
        mock_create_agent,
        mock_analyze,
        mock_mark,
        mock_notify,
    ):
        from appeals_monitor.monitor import run_analysis

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
    def test_no_documents_skips_notification(self, mock_list, mock_notify):
        from appeals_monitor.monitor import run_analysis

        mock_list.return_value = iter([])

        results = run_analysis()
        assert len(results) == 0
        mock_notify.assert_not_called()


# --- Feedback campaign tests ---

ANCHOR = date(2026, 9, 16)


class TestFeedbackSchedule:
    def test_before_anchor_has_no_cycle(self):
        assert cycle_start_for(ANCHOR - timedelta(days=1)) is None
        assert scheduled_send_for(ANCHOR - timedelta(days=1)) is None

    def test_anchor_day_is_the_invitation(self):
        send = scheduled_send_for(ANCHOR)
        assert send is not None
        assert send.cycle_start == ANCHOR
        assert send.round_number == 0
        assert send.is_reminder is False

    @pytest.mark.parametrize("week", [1, 2, 3, 4])
    def test_weekly_reminders_up_to_the_cap(self, week):
        send = scheduled_send_for(ANCHOR + timedelta(days=7 * week))
        assert send is not None
        assert send.round_number == week
        assert send.is_reminder is True

    def test_fifth_week_is_silent(self):
        assert scheduled_send_for(ANCHOR + timedelta(days=35)) is None

    @pytest.mark.parametrize("offset", [1, 3, 6, 8, 13, 100])
    def test_days_between_rounds_are_silent(self, offset):
        assert scheduled_send_for(ANCHOR + timedelta(days=offset)) is None

    def test_day_before_next_cycle_still_belongs_to_the_first(self):
        assert cycle_start_for(date(2027, 3, 15)) == ANCHOR
        assert scheduled_send_for(date(2027, 3, 15)) is None

    def test_next_cycle_starts_six_months_later(self):
        next_start = date(2027, 3, 16)
        assert cycle_start_for(next_start) == next_start
        send = scheduled_send_for(next_start)
        assert send is not None
        assert send.cycle_start == next_start
        assert send.round_number == 0

    def test_second_cycle_reminders_are_relative_to_its_own_start(self):
        send = scheduled_send_for(date(2027, 3, 23))
        assert send is not None
        assert send.cycle_start == date(2027, 3, 16)
        assert send.round_number == 1


class TestRecipientId:
    def test_is_stable_and_normalised(self):
        assert recipient_id("User@Example.com ") == recipient_id("user@example.com")

    def test_differs_per_email(self):
        assert recipient_id("a@example.com") != recipient_id("b@example.com")

    def test_does_not_leak_the_address(self):
        rid = recipient_id("user@example.com")
        assert len(rid) == 16
        assert "user" not in rid and "@" not in rid


class TestPersonalisedUrl:
    def test_appends_prefill_parameter(self):
        url = personalised_url("https://ee.test/x/abc", "deadbeef")
        assert url == "https://ee.test/x/abc?d[rid]=deadbeef"

    def test_preserves_existing_query_string(self):
        url = personalised_url("https://ee.test/x/abc?foo=1", "deadbeef")
        assert url == "https://ee.test/x/abc?foo=1&d[rid]=deadbeef"


class TestRepliedIds:
    def _mock_form(self, monkeypatch, requests_mock, results):
        monkeypatch.setenv("KOBO_API_URL", "https://kobo.test")
        monkeypatch.setenv("KOBO_API_TOKEN", "test-token")
        monkeypatch.setenv("KOBO_FEEDBACK_FORM_UID", "fb123")
        requests_mock.get(
            "https://kobo.test/api/v2/assets/fb123/data.json",
            json={"results": results, "next": None},
        )

    def test_ignores_submissions_from_previous_cycles(
        self, monkeypatch, requests_mock
    ):
        self._mock_form(
            monkeypatch,
            requests_mock,
            [
                {"rid": "old", "_submission_time": "2026-09-15T10:00:00"},
                {"rid": "current", "_submission_time": "2026-09-20T10:00:00"},
            ],
        )
        assert replied_ids(ANCHOR) == {"current"}

    def test_ignores_submissions_without_a_code(self, monkeypatch, requests_mock):
        self._mock_form(
            monkeypatch,
            requests_mock,
            [
                {"rid": "", "_submission_time": "2026-09-20T10:00:00"},
                {"_submission_time": "2026-09-20T10:00:00"},
            ],
        )
        assert replied_ids(ANCHOR) == set()


class TestRunFeedback:
    @patch("appeals_monitor.feedback.send_markdown_email")
    @patch("appeals_monitor.feedback.get_recipients_from_kobo")
    def test_silent_day_sends_nothing(self, mock_recipients, mock_send):
        errors = run_feedback(today=ANCHOR + timedelta(days=3))
        assert errors == []
        mock_recipients.assert_not_called()
        mock_send.assert_not_called()

    @patch("appeals_monitor.feedback.feedback_form_url")
    @patch("appeals_monitor.feedback.replied_ids")
    @patch("appeals_monitor.feedback.send_markdown_email")
    @patch("appeals_monitor.feedback.get_recipients_from_kobo")
    def test_invitation_goes_to_everyone_without_checking_replies(
        self, mock_recipients, mock_send, mock_replied, mock_url
    ):
        mock_recipients.return_value = [
            {"email": "a@example.com", "name": "A", "sectors": set()},
            {"email": "b@example.com", "name": "B", "sectors": set()},
        ]
        mock_url.return_value = "https://ee.test/x/abc"

        errors = run_feedback(today=ANCHOR)

        assert errors == []
        assert mock_send.call_count == 2
        mock_replied.assert_not_called()

    @patch("appeals_monitor.feedback.feedback_form_url")
    @patch("appeals_monitor.feedback.replied_ids")
    @patch("appeals_monitor.feedback.send_markdown_email")
    @patch("appeals_monitor.feedback.get_recipients_from_kobo")
    def test_reminder_skips_people_who_replied(
        self, mock_recipients, mock_send, mock_replied, mock_url
    ):
        mock_recipients.return_value = [
            {"email": "replied@example.com", "name": "R", "sectors": set()},
            {"email": "silent@example.com", "name": "S", "sectors": set()},
        ]
        mock_replied.return_value = {recipient_id("replied@example.com")}
        mock_url.return_value = "https://ee.test/x/abc"

        errors = run_feedback(today=ANCHOR + timedelta(days=7))

        assert errors == []
        assert mock_send.call_count == 1
        body, email, subject = mock_send.call_args[0]
        assert email == "silent@example.com"
        assert "Reminder" in subject
        assert recipient_id("silent@example.com") in body

    @patch("appeals_monitor.feedback.feedback_form_url")
    @patch("appeals_monitor.feedback.send_markdown_email")
    @patch("appeals_monitor.feedback.get_recipients_from_kobo")
    def test_one_failure_does_not_stop_the_others(
        self, mock_recipients, mock_send, mock_url
    ):
        mock_recipients.return_value = [
            {"email": "bad@example.com", "name": "", "sectors": set()},
            {"email": "good@example.com", "name": "", "sectors": set()},
        ]
        mock_url.return_value = "https://ee.test/x/abc"
        mock_send.side_effect = [RuntimeError("SendGrid down"), None]

        errors = run_feedback(today=ANCHOR)

        assert len(errors) == 1
        assert "bad@example.com" in errors[0]
        assert mock_send.call_count == 2

    @patch("appeals_monitor.feedback.feedback_form_url")
    @patch("appeals_monitor.feedback.send_markdown_email")
    @patch("appeals_monitor.feedback.get_recipients_from_kobo")
    def test_config_error_aborts_instead_of_retrying_everyone(
        self, mock_recipients, mock_send, mock_url
    ):
        mock_recipients.return_value = [
            {"email": "a@example.com", "name": "", "sectors": set()},
            {"email": "b@example.com", "name": "", "sectors": set()},
        ]
        mock_url.return_value = "https://ee.test/x/abc"
        mock_send.side_effect = ConfigError("SENDGRID_API_KEY missing")

        with pytest.raises(ConfigError):
            run_feedback(today=ANCHOR)

        assert mock_send.call_count == 1

    @patch("appeals_monitor.feedback.feedback_form_url")
    @patch("appeals_monitor.feedback.send_markdown_email")
    @patch("appeals_monitor.feedback.get_recipients_from_kobo")
    def test_dry_run_sends_nothing(self, mock_recipients, mock_send, mock_url):
        mock_recipients.return_value = [
            {"email": "a@example.com", "name": "A", "sectors": set()}
        ]
        mock_url.return_value = "https://ee.test/x/abc"

        errors = run_feedback(today=ANCHOR, dry_run=True)

        assert errors == []
        mock_send.assert_not_called()

    @patch("appeals_monitor.feedback.feedback_form_url")
    @patch("appeals_monitor.feedback.send_markdown_email")
    @patch("appeals_monitor.feedback.get_recipients_from_kobo")
    def test_no_subscribers_is_not_an_error(
        self, mock_recipients, mock_send, mock_url
    ):
        mock_recipients.return_value = []

        errors = run_feedback(today=ANCHOR)

        assert errors == []
        mock_send.assert_not_called()


class TestFormatInvite:
    def test_invitation_wording(self):
        body = format_invite("Jane", "https://ee.test/x/abc?d[rid]=xyz", False)
        assert "Hi Jane" in body
        assert "https://ee.test/x/abc?d[rid]=xyz" in body
        assert "still collecting feedback" not in body

    def test_reminder_wording(self):
        body = format_invite("", "https://ee.test/x/abc", True)
        assert "Hi there" in body
        assert "still collecting feedback" in body

    def test_reminder_does_not_assume_earlier_contact(self):
        """Mid-cycle subscribers may receive a reminder as their first contact."""
        body = format_invite("", "https://ee.test/x/abc", True)
        assert "not heard back" not in body
        assert "we asked" not in body


class TestFeedbackFormUrl:
    def _deployed(self, monkeypatch, requests_mock, links):
        monkeypatch.setenv("KOBO_API_URL", "https://kobo.test")
        monkeypatch.setenv("KOBO_API_TOKEN", "test-token")
        monkeypatch.setenv("KOBO_FEEDBACK_FORM_UID", "fb123")
        requests_mock.get(
            "https://kobo.test/api/v2/assets/fb123/",
            json={"deployment__links": links},
        )

    def test_prefers_the_single_submission_link(self, monkeypatch, requests_mock):
        self._deployed(
            monkeypatch,
            requests_mock,
            {"url": "https://ee.test/x/multi", "single_url": "https://ee.test/x/one"},
        )
        assert feedback_form_url() == "https://ee.test/x/one"

    def test_undeployed_form_raises(self, monkeypatch, requests_mock):
        self._deployed(monkeypatch, requests_mock, {})
        with pytest.raises(RuntimeError, match="no public link"):
            feedback_form_url()

    def test_missing_uid_raises(self, monkeypatch):
        monkeypatch.setenv("KOBO_API_URL", "https://kobo.test")
        monkeypatch.setenv("KOBO_API_TOKEN", "test-token")
        monkeypatch.delenv("KOBO_FEEDBACK_FORM_UID", raising=False)
        with pytest.raises(RuntimeError, match="KOBO_FEEDBACK_FORM_UID"):
            feedback_form_url()
