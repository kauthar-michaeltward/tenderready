"""Tests for the AI resilience layer: response repair, citation pruning and the Basic check."""

from app.analysis import _decode_stringified_fields, _prune_unverified, _quote_found
from app.basic_check import LABEL, basic_check
from app.analysis import _check_sources
from app.document_types import DocumentSegment
from app.models import BusinessProfile, TenderAnalysis

TENDER = DocumentSegment(
    "Tender.pdf",
    "Page 1",
    "Bidders must submit SBD 1 and a valid Tax Compliance Status PIN issued by SARS. "
    "Proof of registration on the Central Supplier Database (CSD) is required. "
    "Closing date for submissions: 15 January 2027 at 11:00.",
    page=1,
)

PROFILE = BusinessProfile(
    business_name="Kumbula Cloud Solutions",
    country="South Africa",
    sector="Managed IT services",
    years_operating=7,
    annual_revenue=18_500_000,
    currency="ZAR",
    certifications=["CSD registered"],
    capabilities=["IT support"],
    geographic_regions=["Gauteng"],
)


def _source(quote: str) -> dict:
    return {"document": "Tender.pdf", "location": "Page 1", "page": 1, "quote": quote}


def test_quote_matching_tolerates_formatting_but_not_invented_text():
    assert _quote_found("Valid TAX Compliance Status PIN", TENDER.text)
    assert _quote_found("submit SBD 1 ... Central Supplier Database", TENDER.text)
    assert not _quote_found("Bidders must hold ISO 27001 certification", TENDER.text)


def test_stringified_json_fields_are_decoded():
    fixed = _decode_stringified_fields({"key_deadlines": '[{"name": "Closing"}]', "eligibility": "eligible"})
    assert fixed["key_deadlines"] == [{"name": "Closing"}]
    assert fixed["eligibility"] == "eligible"


def test_unverifiable_citations_and_items_are_removed():
    analysis = TenderAnalysis.model_validate({
        "eligibility": "conditional",
        "rationale": {"text": "Needs tax compliance.", "sources": [_source("Tax Compliance Status PIN issued by SARS")]},
        "mandatory_documents": [
            {"name": "SBD 1", "reason": "Required form.", "sources": [_source("Bidders must submit SBD 1")]},
            {"name": "ISO 27001", "reason": "Invented.", "sources": [_source("Bidders must hold ISO 27001 certification")]},
        ],
        "submission_plan": [{"step": "Prepare SBD 1.", "sources": [_source("Bidders must submit SBD 1")]}],
    })
    pruned = _prune_unverified(analysis, [TENDER])
    assert [doc.name for doc in pruned.mandatory_documents] == ["SBD 1"]
    _check_sources(pruned, [TENDER])


def test_basic_check_quotes_the_tender_and_flags_profile_gaps():
    result = basic_check([TENDER], PROFILE)
    _check_sources(result, [TENDER])
    assert result.eligibility == "unclear"
    assert result.rationale.text.startswith(LABEL)
    names = [doc.name for doc in result.mandatory_documents]
    assert "Tax Compliance Status PIN (SARS)" in names
    assert [gap.requirement for gap in result.missing_requirements] == ["Tax Compliance Status PIN (SARS)"]
    assert any(d.date == "15 January 2027" for d in result.key_deadlines)
