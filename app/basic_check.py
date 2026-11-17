"""Rule-based fallback used only when the AI analysis fails twice.

It never invents content: every item is a line found verbatim in the uploaded
documents, and the result is labelled as a basic check so users know the AI
analysis was unavailable.
"""

import re
from typing import Any

from app.document_types import DocumentSegment
from app.models import BusinessProfile, TenderAnalysis

LABEL = "Basic check (AI analysis unavailable)."

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(
    rf"\b(\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}}|\d{{4}}[/-]\d{{2}}[/-]\d{{2}}|\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}})\b",
    re.IGNORECASE,
)
DATE_KINDS = [
    ("Clarification questions", re.compile(r"clarification|enquir|questions", re.I)),
    ("Briefing session", re.compile(r"briefing|site meeting|site inspection", re.I)),
    ("Bid validity", re.compile(r"validity|valid for", re.I)),
    ("Closing date for submissions", re.compile(r"closing|submission|due date|deadline", re.I)),
]

# (document name, pattern, why it matters, profile check returning True when the profile covers it, or None)
RULES: list[tuple[str, re.Pattern, str, Any]] = [
    ("SBD 1 Invitation to Bid", re.compile(r"\bSBD\s*1\b", re.I), "Standard bidding form named in the tender.", None),
    ("SBD 4 Bidder's Disclosure", re.compile(r"\bSBD\s*4\b", re.I), "Standard bidding form named in the tender.", None),
    ("SBD 6.1 Preference Points Claim Form", re.compile(r"\bSBD\s*6\.1\b", re.I), "Needed to claim preference points.", None),
    ("Tax Compliance Status PIN (SARS)", re.compile(r"tax\s+compliance|\bSARS\b", re.I), "The tender refers to tax compliance.",
     lambda p: "compliant (" in _field(p, "tax_compliance").lower()),
    ("Central Supplier Database (CSD) registration", re.compile(r"central supplier database|\bCSD\b", re.I), "The tender refers to CSD registration.",
     lambda p: bool(_field(p, "csd_number")) or "csd" in _certs(p)),
    ("B-BBEE certificate or sworn affidavit", re.compile(r"B-?BBEE|broad[- ]based black", re.I), "The tender refers to B-BBEE status.",
     lambda p: bool(_field(p, "bbbee_level")) or "bbbee" in _certs(p).replace("-", "")),
    ("CIDB grading", re.compile(r"\bCIDB\b", re.I), "The tender refers to a CIDB grading.",
     lambda p: "cidb" in _certs(p)),
    ("Compensation Fund letter of good standing (COIDA)", re.compile(r"\bCOIDA\b|compensation fund", re.I), "The tender refers to COIDA.", None),
    ("Company registration documents (CIPC)", re.compile(r"\bCIPC\b|company registration", re.I), "The tender refers to company registration.",
     lambda p: bool(_field(p, "registration_number"))),
    ("ISO certification", re.compile(r"\bISO(?:/IEC)?\s*\d{4,5}", re.I), "The tender refers to an ISO standard.",
     lambda p: "iso" in _certs(p)),
]


def _field(profile: BusinessProfile, name: str) -> str:
    return str(getattr(profile, name, "") or "")


def _certs(profile: BusinessProfile) -> str:
    return " ".join(getattr(profile, "certifications", []) or []).lower()


def _source(segment: DocumentSegment, start: int, end: int) -> dict[str, Any] | None:
    text = segment.text
    left = max(0, text.rfind("\n", 0, start) + 1, start - 160)
    right = text.find("\n", end)
    right = min(len(text), right if right != -1 else len(text), end + 160)
    quote = re.sub(r"\s+", " ", text[left:right]).strip()[:480]
    if len(quote) < 8:
        return None
    return {"document": segment.document, "location": segment.location, "page": segment.page, "quote": quote}


def _find(segments: list[DocumentSegment], pattern: re.Pattern):
    for segment in segments:
        match = pattern.search(segment.text or "")
        if match:
            source = _source(segment, match.start(), match.end())
            if source:
                return source
    return None


def basic_check(segments: list[DocumentSegment], profile: BusinessProfile) -> TenderAnalysis:
    documents, missing = [], []
    for name, pattern, reason, covered in RULES:
        source = _find(segments, pattern)
        if not source:
            continue
        documents.append({"name": name, "reason": reason, "sources": [source]})
        if covered is not None and not covered(profile):
            missing.append({
                "requirement": name,
                "profile_gap": "The tender mentions this, and your business profile does not show it yet.",
                "sources": [source],
            })

    deadlines, seen = [], set()
    for segment in segments:
        for match in DATE_RE.finditer(segment.text or ""):
            source = _source(segment, match.start(), match.end())
            if not source:
                continue
            kind = next((label for label, rx in DATE_KINDS if rx.search(source["quote"])), None)
            if not kind or (kind, match.group(1)) in seen:
                continue
            seen.add((kind, match.group(1)))
            deadlines.append({"name": kind, "date": match.group(1), "sources": [source]})
            if len(deadlines) >= 8:
                break

    first = (documents or deadlines or [None])[0]
    rationale_source = first["sources"][0] if first else next(
        (s for s in (_source(seg, 0, min(len(seg.text or ""), 200)) for seg in segments if (seg.text or "").strip()) if s),
        None,
    )
    if rationale_source is None:
        raise ValueError("No readable text was found in the documents.")

    plan = [{"step": f"Prepare: {item['name']}.", "due_date": "", "sources": item["sources"]} for item in documents[:10]]
    closing = next((d for d in deadlines if d["name"].startswith("Closing")), None)
    if closing:
        plan.append({"step": "Submit before the closing date stated in the tender.", "due_date": closing["date"], "sources": closing["sources"]})
    plan.append({"step": "Retry the full AI analysis for a detailed eligibility assessment.", "due_date": "", "sources": []})

    return TenderAnalysis.model_validate({
        "eligibility": "unclear",
        "rationale": {
            "text": f"{LABEL} The AI could not return a valid analysis, so TenderReady scanned the documents for standard "
                    f"South African tender requirements and dates. Found {len(documents)} requirement(s), {len(missing)} "
                    f"possible gap(s) against your profile and {len(deadlines)} date(s). Every item quotes the line it came from. "
                    "Eligibility is not assessed in a basic check.",
            "sources": [rationale_source],
        },
        "mandatory_documents": documents,
        "key_deadlines": deadlines,
        "missing_requirements": missing,
        "submission_plan": plan,
    })
