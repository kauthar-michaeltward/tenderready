from io import BytesIO
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pymupdf as fitz
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from docx import Document
from openpyxl import Workbook
from pptx import Presentation

from app import main, storage
from app.analysis import ANALYSIS_SCHEMA, _check_sources, _configured_provider, analyze_tender
from app.document_types import DocumentSegment
from app.documents import extract_bundle, extract_document
from app.main import app
from app.models import BusinessProfile, TenderAnalysis
from app.pdf import extract_pdf_pages


def sample_profile():
    return {
        "business_name": "Acacia Engineering Ltd",
        "country": "Kenya",
        "sector": "Construction",
        "years_operating": 5,
        "annual_revenue": 25_000_000,
        "currency": "KES",
        "registration_number": "REG-123",
        "certifications": ["ISO 9001"],
        "capabilities": ["Road construction", "Civil works"],
        "geographic_regions": ["Nairobi"],
    }


def sample_analysis(quote="Bids close on 30 June 2027 at 10:00 EAT."):
    return TenderAnalysis.model_validate(
        {
            "eligibility": "conditional",
            "rationale": {
                "text": "Confirm the registration category before applying.",
                "sources": [{
                    "document": "Tender.pdf",
                    "location": "Page 1",
                    "page": 1,
                    "quote": quote,
                }],
            },
            "mandatory_documents": [
                {
                    "name": "Tax clearance certificate",
                    "reason": "Required with the bid.",
                    "sources": [{
                        "document": "Tender.pdf",
                        "location": "Page 1",
                        "page": 1,
                        "quote": quote,
                    }],
                }
            ],
            "key_deadlines": [
                {
                    "name": "Bid submission",
                    "date": "30 June 2027 at 10:00 EAT",
                    "sources": [{
                        "document": "Tender.pdf",
                        "location": "Page 1",
                        "page": 1,
                        "quote": quote,
                    }],
                }
            ],
            "missing_requirements": [
                {
                    "requirement": "Tax clearance certificate",
                    "profile_gap": "Not listed in the profile.",
                    "sources": [{
                        "document": "Tender.pdf",
                        "location": "Page 1",
                        "page": 1,
                        "quote": quote,
                    }],
                }
            ],
            "submission_plan": [{"step": "Verify the tax clearance certificate.", "due_date": "", "sources": []}],
        }
    )


def make_pdf(text):
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def test_profile_validation_normalizes_list_values():
    profile = BusinessProfile.model_validate({**sample_profile(), "capabilities": [" Road works ", ""]})
    assert profile.capabilities == ["Road works"]
    with pytest.raises(ValueError):
        BusinessProfile.model_validate({**sample_profile(), "currency": "KES1"})


def test_postgres_timestamps_keep_the_frontend_compatible_utc_format():
    timestamp = datetime(2026, 11, 17, 16, 0, tzinfo=timezone(timedelta(hours=3)))
    assert storage._api_timestamp(timestamp) == "2026-11-17 13:00:00"


def test_pdf_extraction_rejects_empty_or_unreadable_content():
    with pytest.raises(HTTPException, match="empty"):
        extract_pdf_pages(b"")
    image_only = fitz.open()
    image_only.new_page()
    content = image_only.tobytes()
    image_only.close()
    with pytest.raises(HTTPException, match="No readable text"):
        extract_pdf_pages(content)


def test_image_dominated_pdf_page_is_ocr_processed(monkeypatch):
    document = fitz.open()
    page = document.new_page(width=200, height=300)
    pixmap = fitz.Pixmap(fitz.csRGB, (0, 0, 100, 100), 0)
    pixmap.clear_with(255)
    page.insert_image(page.rect, pixmap=pixmap)
    page.insert_text((20, 20), "Managing Director")
    content = document.tobytes()
    document.close()

    monkeypatch.setattr("app.pdf.extract_ocr_text", lambda _page: "Invitation to tender. Bids close on 30 June 2027.")
    assert extract_pdf_pages(content) == ["Invitation to tender. Bids close on 30 June 2027."]


def test_sources_must_match_the_correct_tender_page():
    valid = sample_analysis()
    _check_sources(valid, ["Bids close on 30 June 2027 at 10:00 EAT."])
    with pytest.raises(HTTPException, match="could not be verified"):
        _check_sources(valid, ["This is a different tender passage entirely."])
    with pytest.raises(HTTPException, match="location that does not exist"):
        _check_sources(sample_analysis("Bids close on 30 June 2027 at 10:00 EAT."), [])


def test_sources_are_scoped_to_the_exact_document_and_location():
    analysis = sample_analysis("The bidder must submit a current tax certificate.")
    segments = [
        DocumentSegment("Requirements.docx", "Paragraph 4", "The bidder must submit a current tax certificate.")
    ]
    analysis_data = analysis.model_dump()
    for section in (
        analysis_data["rationale"],
        *analysis_data["mandatory_documents"],
        *analysis_data["key_deadlines"],
        *analysis_data["missing_requirements"],
        *analysis_data["submission_plan"],
    ):
        for source in section["sources"]:
            source.update(document="Requirements.docx", location="Paragraph 4", page=None)
    scoped = TenderAnalysis.model_validate(analysis_data)
    _check_sources(scoped, segments)

    analysis_data["rationale"]["sources"][0]["location"] = "Paragraph 5"
    with pytest.raises(HTTPException, match="location that does not exist"):
        _check_sources(TenderAnalysis.model_validate(analysis_data), segments)


def test_provider_schema_matches_source_and_result_limits():
    source_schema = ANALYSIS_SCHEMA["$defs"]["source"]
    assert source_schema["properties"]["quote"]["maxLength"] == 500
    assert source_schema["properties"]["location"]["maxLength"] == 160
    assert ANALYSIS_SCHEMA["properties"]["submission_plan"]["maxItems"] == 30


def test_document_extraction_reads_office_files_and_text():
    word = Document()
    word.add_paragraph("Invitation closes on 30 June 2027.")
    table = word.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Tax certificate"
    table.cell(0, 1).text = "Mandatory"
    docx = BytesIO()
    word.save(docx)

    workbook = Workbook()
    workbook.active.title = "Checklist"
    workbook.active.append(["Document", "Required"])
    workbook.active.append(["Tax certificate", "Yes"])
    xlsx = BytesIO()
    workbook.save(xlsx)

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(0, 0, 4000000, 500000).text = "Mandatory briefing: 13 November 2026."
    pptx = BytesIO()
    presentation.save(pptx)

    segments = extract_bundle([
        ("Invitation.docx", docx.getvalue()),
        ("Checklist.xlsx", xlsx.getvalue()),
        ("Briefing.pptx", pptx.getvalue()),
        ("Schedule.txt", b"Submission deadline: 30 June 2027."),
        ("Readme.html", b"<h1>Submission instructions</h1><script>hidden secret</script>"),
    ])
    assert any(segment.location == "Paragraph 1" and "30 June 2027" in segment.text for segment in segments)
    assert any(segment.location.startswith("Table 1, row 1") and "Tax certificate" in segment.text for segment in segments)
    assert any(segment.location == "Sheet Checklist, row 2" and "Tax certificate" in segment.text for segment in segments)
    assert any(segment.location == "Slide 1" and "13 November 2026" in segment.text for segment in segments)
    assert any(segment.location.startswith("Lines 1-") and "Submission deadline" in segment.text for segment in segments)
    assert any(segment.text == "Submission instructions" for segment in segments)
    assert all("hidden secret" not in segment.text for segment in segments)


def test_standalone_image_is_ocr_processed(monkeypatch):
    pixmap = fitz.Pixmap(fitz.csRGB, (0, 0, 200, 100), 0)
    pixmap.clear_with(255)
    image = pixmap.tobytes("png")
    monkeypatch.setattr("app.documents.extract_ocr_text", lambda _page: "Bid submission form")
    segments = extract_document("form.png", image)
    assert segments[0].document == "form.png"
    assert segments[0].location == "Page 1"
    assert segments[0].text == "Bid submission form"


def test_document_upload_rejects_unsupported_and_duplicate_files():
    with pytest.raises(HTTPException) as unsupported:
        extract_document("tender.exe", b"not a tender")
    assert unsupported.value.status_code == 415
    with pytest.raises(HTTPException, match="duplicate filenames"):
        extract_bundle([("notice.txt", b"Notice"), ("notice.txt", b"Another notice")])


def test_anthropic_key_takes_precedence_over_openai(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    assert _configured_provider() == ("anthropic", "anthropic-test-key")


def test_claude_key_uses_anthropic_api_and_validates_citations(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ant-test-key")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(
                {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "tender_analysis",
                            "input": sample_analysis().model_dump(),
                        }
                    ]
                }
            ).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {
            key.lower(): value for key, value in request.header_items()
        }
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.analysis.urllib.request.urlopen", fake_urlopen)
    result = analyze_tender(
        ["Bids close on 30 June 2027 at 10:00 EAT."],
        BusinessProfile.model_validate(sample_profile()),
    )

    assert result.eligibility == "conditional"
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-ant-test-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["payload"]["model"] == "claude-sonnet-4-5"
    assert captured["payload"]["max_tokens"] == 16000
    assert captured["payload"]["tool_choice"] == {"type": "tool", "name": "tender_analysis"}
    assert captured["timeout"] == 300


def test_profile_and_analysis_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDERREADY_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("TESTER_ACCESS_CODE", raising=False)
    client = TestClient(app)
    response = client.put("/api/profile", json=sample_profile())
    assert response.status_code == 200
    assert client.get("/api/profile").json()["business_name"] == "Acacia Engineering Ltd"

    monkeypatch.setattr(main, "analyze_tender", lambda pages, profile: sample_analysis())
    response = client.post(
        "/api/analyses",
        files=[("files", ("request.pdf", make_pdf("Bids close on 30 June 2027 at 10:00 EAT."), "application/pdf"))],
    )
    assert response.status_code == 200
    record = response.json()
    assert record["status"] == "processing"
    persisted = client.get(f"/api/analyses/{record['id']}").json()
    assert persisted["status"] == "complete"
    assert persisted["analysis"]["eligibility"] == "conditional"
    assert persisted["analysis"]["key_deadlines"][0]["sources"][0]["location"] == "Page 1"
    assert persisted["document_count"] == 1
    assert persisted["file_name"] == "request.pdf"
    assert client.get("/api/analyses").json()[0]["id"] == record["id"]


def test_multiple_document_upload_is_extracted_and_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDERREADY_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.delenv("TESTER_ACCESS_CODE", raising=False)
    client = TestClient(app)
    client.put("/api/profile", json=sample_profile())
    received = {}

    def fake_analyze(segments, profile):
        received["segments"] = segments
        received["profile"] = profile
        return sample_analysis()

    monkeypatch.setattr(main, "analyze_tender", fake_analyze)
    response = client.post(
        "/api/analyses",
        files=[
            ("files", ("invitation.txt", b"Bid deadline: 30 June 2027 at 10:00 EAT.", "text/plain")),
            ("files", ("forms.csv", b"Form,Required\nTax clearance,Yes", "text/csv")),
        ],
    )
    assert response.status_code == 200
    record = response.json()
    assert record["status"] == "processing"
    persisted = client.get(f"/api/analyses/{record['id']}").json()
    assert persisted["document_count"] == 2
    assert persisted["page_count"] == 0
    assert persisted["file_name"] == "invitation.txt, forms.csv"
    assert {segment.document for segment in received["segments"]} == {"invitation.txt", "forms.csv"}
    assert persisted["status"] == "complete"


def test_existing_analysis_database_gets_document_count_migration(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                file_name TEXT NOT NULL,
                page_count INTEGER NOT NULL,
                analysis_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO analyses (file_name, page_count, analysis_json) VALUES (?, ?, ?)",
            ("legacy.pdf", 1, "{}"),
        )
    monkeypatch.setenv("TENDERREADY_DB_PATH", str(database_path))
    assert storage.list_analyses()[0]["document_count"] == 1
    assert storage.get_analysis(1)["analysis"] == {}
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(analyses)")}
    assert "document_count" in columns
    assert "status" in columns
    assert "error" in columns


def test_existing_profile_database_migrates_to_session_scoped_profiles(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy-profile.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE business_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                profile_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "INSERT INTO business_profile (id, profile_json) VALUES (1, ?)",
            (json.dumps(sample_profile()),),
        )
    monkeypatch.setenv("TENDERREADY_DB_PATH", str(database_path))

    assert storage.get_profile()["business_name"] == "Acacia Engineering Ltd"
    storage.save_profile({"business_name": "Separate"}, "tester-two")
    assert storage.get_profile("tester-two") == {"business_name": "Separate"}
    assert storage.get_profile()["business_name"] == "Acacia Engineering Ltd"


def test_tester_sessions_are_gated_and_profiles_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDERREADY_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("TESTER_ACCESS_CODE", "private-test-code")
    first_tester = TestClient(app)
    second_tester = TestClient(app)

    assert "Tester access code" in first_tester.get("/").text
    assert first_tester.get("/api/profile").status_code == 401
    assert first_tester.get("/api/analyses").status_code == 401
    assert first_tester.post("/login", data={"access_code": "wrong"}).status_code == 401
    first_tester.post("/login", data={"access_code": "private-test-code"})
    second_tester.post("/login", data={"access_code": "private-test-code"})

    assert first_tester.put("/api/profile", json=sample_profile()).status_code == 200
    assert first_tester.get("/api/profile").json()["business_name"] == "Acacia Engineering Ltd"
    assert second_tester.get("/api/profile").status_code == 404
    assert first_tester.get("/api/analyses").json() == []

    analysis_id = storage.save_analysis(
        "private.pdf", 1, 1, owner_id="tester-one", status="processing"
    )
    assert storage.get_analysis(analysis_id, "tester-one")["file_name"] == "private.pdf"
    assert storage.get_analysis(analysis_id, "tester-two") is None
    assert storage.list_analyses("tester-two") == []


def test_missing_ai_credentials_fail_without_fabricating_analysis(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDERREADY_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    client = TestClient(app)
    client.put("/api/profile", json=sample_profile())
    response = client.post(
        "/api/analyses",
        files=[("files", ("request.pdf", make_pdf("Tender closes on 30 June 2027."), "application/pdf"))],
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "processing"
    persisted = client.get(f"/api/analyses/{payload['id']}").json()
    assert persisted["status"] == "failed"
    assert "ANTHROPIC_API_KEY" in persisted["error"]
    assert "OPENAI_API_KEY" in persisted["error"]
