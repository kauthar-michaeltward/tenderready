import json

import pymupdf as fitz
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.analysis import _check_sources, _configured_provider, analyze_tender
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
                "sources": [{"page": 1, "quote": quote}],
            },
            "mandatory_documents": [
                {
                    "name": "Tax clearance certificate",
                    "reason": "Required with the bid.",
                    "sources": [{"page": 1, "quote": quote}],
                }
            ],
            "key_deadlines": [
                {"name": "Bid submission", "date": "30 June 2027 at 10:00 EAT", "sources": [{"page": 1, "quote": quote}]}
            ],
            "missing_requirements": [
                {"requirement": "Tax clearance certificate", "profile_gap": "Not listed in the profile.", "sources": [{"page": 1, "quote": quote}]}
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


def test_pdf_extraction_rejects_empty_or_image_only_content():
    with pytest.raises(HTTPException, match="empty"):
        extract_pdf_pages(b"")
    image_only = fitz.open()
    image_only.new_page()
    content = image_only.tobytes()
    image_only.close()
    with pytest.raises(HTTPException, match="No selectable text"):
        extract_pdf_pages(content)


def test_image_dominated_page_with_only_a_signature_line_requires_ocr():
    document = fitz.open()
    page = document.new_page(width=200, height=300)
    pixmap = fitz.Pixmap(fitz.csRGB, (0, 0, 10, 10), 0)
    pixmap.clear_with(255)
    page.insert_image(page.rect, pixmap=pixmap)
    page.insert_text((20, 20), "Managing Director")
    content = document.tobytes()
    document.close()

    with pytest.raises(HTTPException, match="OCR is required"):
        extract_pdf_pages(content)


def test_sources_must_match_the_correct_tender_page():
    valid = sample_analysis()
    _check_sources(valid, ["Bids close on 30 June 2027 at 10:00 EAT."])
    with pytest.raises(HTTPException, match="could not be verified"):
        _check_sources(valid, ["This is a different tender passage entirely."])
    with pytest.raises(HTTPException, match="page that does not exist"):
        _check_sources(sample_analysis("Bids close on 30 June 2027 at 10:00 EAT."), [])


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
    assert captured["payload"]["tool_choice"] == {"type": "tool", "name": "tender_analysis"}
    assert captured["timeout"] == 60


def test_profile_and_analysis_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDERREADY_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    client = TestClient(app)
    response = client.put("/api/profile", json=sample_profile())
    assert response.status_code == 200
    assert client.get("/api/profile").json()["business_name"] == "Acacia Engineering Ltd"

    monkeypatch.setattr(main, "analyze_tender", lambda pages, profile: sample_analysis())
    response = client.post(
        "/api/analyses",
        files={"file": ("request.pdf", make_pdf("Bids close on 30 June 2027 at 10:00 EAT."), "application/pdf")},
    )
    assert response.status_code == 200
    record = response.json()
    assert record["analysis"]["eligibility"] == "conditional"
    assert record["analysis"]["key_deadlines"][0]["sources"][0]["page"] == 1
    assert client.get(f"/api/analyses/{record['id']}").json()["file_name"] == "request.pdf"
    assert client.get("/api/analyses").json()[0]["id"] == record["id"]


def test_missing_ai_credentials_fail_without_fabricating_analysis(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDERREADY_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    client = TestClient(app)
    client.put("/api/profile", json=sample_profile())
    response = client.post(
        "/api/analyses",
        files={"file": ("request.pdf", make_pdf("Tender closes on 30 June 2027."), "application/pdf")},
    )
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]
    assert "OPENAI_API_KEY" in response.json()["detail"]
