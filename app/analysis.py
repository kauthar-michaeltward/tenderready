import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

from fastapi import HTTPException

from app.document_types import DocumentSegment
from app.models import BusinessProfile, TenderAnalysis


SYSTEM_PROMPT = """You assess government tender fit for African small and medium businesses.
Use only the supplied tender document segments as evidence about tender requirements, documents,
dates, and criteria. Compare those explicit requirements with the supplied business profile.
Do not invent tender facts, eligibility criteria, documents, or deadlines. If the tender does
not say something, say that it is not stated without presenting it as a tender requirement.
Be concise. Keep each quote under 200 characters, use at most 3 sources per item, and keep
rationale and reasons to 2 sentences, so the full answer fits the output limit.
Every cited quote must be copied verbatim from the supplied segment text, be no longer than 500
characters, and identify its document name and exact location label. For PDF/image pages, the
location label includes the 1-based page number. Cite relevant tender passages for eligibility,
each mandatory document, each deadline, each missing requirement, and any plan step that relies
on tender details. Profile gaps are not proof that the business fails a requirement; distinguish
unknown information from a confirmed mismatch. Use the exact JSON shape requested by the caller.
"""

AI_REQUEST_TIMEOUT_SECONDS = 300
MAX_AI_ATTEMPTS = 2
FALLBACK_ENABLED = os.getenv("TENDERREADY_BASIC_FALLBACK", "1") != "0"

ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "eligibility",
        "rationale",
        "mandatory_documents",
        "key_deadlines",
        "missing_requirements",
        "submission_plan",
    ],
    "properties": {
        "eligibility": {"type": "string", "enum": ["eligible", "conditional", "ineligible", "unclear"]},
        "rationale": {"$ref": "#/$defs/rationale"},
        "mandatory_documents": {"type": "array", "maxItems": 40, "items": {"$ref": "#/$defs/document"}},
        "key_deadlines": {"type": "array", "maxItems": 30, "items": {"$ref": "#/$defs/deadline"}},
        "missing_requirements": {"type": "array", "maxItems": 40, "items": {"$ref": "#/$defs/missing"}},
        "submission_plan": {"type": "array", "minItems": 1, "maxItems": 30, "items": {"$ref": "#/$defs/step"}},
    },
    "$defs": {
        "source": {
            "type": "object", "additionalProperties": False,
            "required": ["document", "location", "page", "quote"],
            "properties": {
                "document": {"type": "string", "minLength": 1, "maxLength": 160},
                "location": {"type": "string", "minLength": 1, "maxLength": 160},
                "page": {"type": ["integer", "null"], "minimum": 1},
                "quote": {"type": "string", "minLength": 8, "maxLength": 500},
            },
        },
        "rationale": {
            "type": "object", "additionalProperties": False, "required": ["text", "sources"],
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 2000},
                "sources": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"$ref": "#/$defs/source"}},
            },
        },
        "document": {
            "type": "object", "additionalProperties": False, "required": ["name", "reason", "sources"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
                "sources": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"$ref": "#/$defs/source"}},
            },
        },
        "deadline": {
            "type": "object", "additionalProperties": False, "required": ["name", "date", "sources"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "date": {"type": "string", "minLength": 1, "maxLength": 120},
                "sources": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"$ref": "#/$defs/source"}},
            },
        },
        "missing": {
            "type": "object", "additionalProperties": False, "required": ["requirement", "profile_gap", "sources"],
            "properties": {
                "requirement": {"type": "string", "minLength": 1, "maxLength": 300},
                "profile_gap": {"type": "string", "minLength": 1, "maxLength": 1000},
                "sources": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"$ref": "#/$defs/source"}},
            },
        },
        "step": {
            "type": "object", "additionalProperties": False, "required": ["step", "due_date", "sources"],
            "properties": {
                "step": {"type": "string", "minLength": 1, "maxLength": 500},
                "due_date": {"type": "string", "maxLength": 120},
                "sources": {"type": "array", "maxItems": 8, "items": {"$ref": "#/$defs/source"}},
            },
        },
    },
}


def _inline_schema_references(schema: Any) -> Any:
    if isinstance(schema, dict):
        reference = schema.get("$ref")
        if reference:
            definition_name = reference.rsplit("/", 1)[-1]
            return _inline_schema_references(ANALYSIS_SCHEMA["$defs"][definition_name])
        return {
            key: _inline_schema_references(value)
            for key, value in schema.items()
            if key != "$defs"
        }
    if isinstance(schema, list):
        return [_inline_schema_references(value) for value in schema]
    return schema


ANTHROPIC_ANALYSIS_SCHEMA = _inline_schema_references(ANALYSIS_SCHEMA)


def _normalize_quote(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[\u2018\u2019\u201a\u201b`\u00b4]", "'", text)
    text = re.sub(r"[\u201c\u201d\u201e\u00ab\u00bb]", '"', text)
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    text = re.sub(r"[^\w'\"%/.,:;()-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def _quote_found(quote: str, text: str) -> bool:
    """True when every part of the quote (split on ellipses) appears in the text.

    Tolerates formatting differences only; a passage that is not in the
    document still fails.
    """
    haystack = _normalize_quote(text)
    parts = [_normalize_quote(part) for part in re.split(r"\.\.\.|\u2026|\[\.\.\.\]", quote)]
    parts = [part for part in parts if len(part) >= 6]
    return bool(parts) and all(part in haystack for part in parts)


def _as_segments(values: list[DocumentSegment | str]) -> list[DocumentSegment]:
    return [
        DocumentSegment("Tender.pdf", f"Page {index}", value, page=index)
        if isinstance(value, str)
        else value
        for index, value in enumerate(values, 1)
    ]


def _check_sources(analysis: TenderAnalysis, values: list[DocumentSegment | str]) -> None:
    segments = _as_segments(values)
    for source in _iter_sources(analysis.model_dump()):
        location = source.get("location") or (
            f"Page {source['page']}" if source.get("page") else ""
        )
        matches = [
            segment
            for segment in segments
            if segment.document == source.get("document", "Tender.pdf")
            and segment.location == location
            and (source.get("page") is None or segment.page == source["page"])
        ]
        if not matches:
            raise HTTPException(
                status_code=502,
                detail="The AI returned a document location that does not exist in the upload. Please retry.",
            )
        if not any(_quote_found(source["quote"], segment.text) for segment in matches):
            raise HTTPException(
                status_code=502,
                detail="The AI returned a passage that could not be verified in the uploaded documents. Please retry.",
            )


def _iter_sources(value: Any):
    if isinstance(value, dict):
        if "page" in value and "quote" in value:
            yield value
        else:
            for child in value.values():
                yield from _iter_sources(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_sources(child)


def _analysis_input(segments: list[DocumentSegment], profile: BusinessProfile) -> str:
    return json.dumps(
        {
            "business_profile": profile.model_dump(),
            "tender_segments": [
                {
                    "document": segment.document,
                    "location": segment.location,
                    "page": segment.page,
                    "text": segment.text,
                }
                for segment in segments
            ],
            "response_instructions": (
                "Return eligibility, rationale {text,sources}, mandatory_documents "
                "[{name,reason,sources}], key_deadlines [{name,date,sources}], "
                "missing_requirements [{requirement,profile_gap,sources}], and "
                "submission_plan [{step,due_date,sources}]. Each source must contain the exact "
                "document and location labels supplied in a segment, plus an exact quote. Include "
                "page for page-based locations. Give a useful ordered plan."
            ),
        },
        ensure_ascii=False,
    )


def _configured_provider() -> tuple[str, str]:
    anthropic_key = (
        os.getenv("ANTHROPIC_API_KEY", "").strip()
        or os.getenv("CLAUDE_API_KEY", "").strip()
    )
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not anthropic_key and openai_key.startswith("sk-ant-"):
        anthropic_key, openai_key = openai_key, ""
    if anthropic_key:
        return "anthropic", anthropic_key
    if openai_key:
        return "openai", openai_key
    raise HTTPException(
        status_code=503,
        detail=(
            "AI analysis is not configured. Set ANTHROPIC_API_KEY for Claude "
            "or OPENAI_API_KEY for OpenAI in your local .env file, then restart the app."
        ),
    )


def _decode_stringified_fields(data: Any) -> Any:
    """Claude occasionally returns a list or object field as a JSON string. Decode it."""
    if not isinstance(data, dict):
        return data
    fixed = {}
    for key, value in data.items():
        if isinstance(value, str) and value.strip()[:1] in "[{":
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        fixed[key] = _decode_stringified_fields(value) if isinstance(value, dict) else value
    return fixed


def _send_request(request: urllib.request.Request, provider: str) -> TenderAnalysis:
    try:
        with urllib.request.urlopen(request, timeout=AI_REQUEST_TIMEOUT_SECONDS) as response:
            response_data = json.loads(response.read())
        if provider == "anthropic" and response_data.get("stop_reason") == "max_tokens":
            print("[tenderready] Claude response cut off at max_tokens", file=sys.stderr, flush=True)
            raise HTTPException(
                status_code=502,
                detail="The analysis was cut off before it finished. Try fewer or shorter documents.",
            )
        if provider == "anthropic":
            tool_result = next(
                (
                    block["input"]
                    for block in response_data["content"]
                    if block.get("type") == "tool_use"
                    and block.get("name") == "tender_analysis"
                ),
                None,
            )
            if tool_result is None:
                raise ValueError("The provider did not return the analysis tool result.")
            result = TenderAnalysis.model_validate(_decode_stringified_fields(tool_result))
        else:
            content = response_data["choices"][0]["message"]["content"]
            result = TenderAnalysis.model_validate_json(content)
    except urllib.error.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The AI provider returned an error (HTTP {exc.code}). Check your API configuration and try again.",
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not connect to the AI provider. Check your connection and try again.",
        ) from exc
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
        print(f"[tenderready] invalid AI response: {type(exc).__name__}: {str(exc)[:1500]}", file=sys.stderr, flush=True)
        raise HTTPException(
            status_code=502,
            detail="The AI provider returned an invalid response. Please try again.",
        ) from exc
    return result


def _analyze_with_anthropic(
    api_key: str,
    segments: list[DocumentSegment],
    profile: BusinessProfile,
) -> TenderAnalysis:
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    payload = {
        "model": model,
        "max_tokens": 16000,
        "temperature": 0.1,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _analysis_input(segments, profile)}],
        "tools": [
            {
                "name": "tender_analysis",
                "description": "Return a tender assessment grounded in the supplied tender pages.",
                "input_schema": ANTHROPIC_ANALYSIS_SCHEMA,
            }
        ],
        "tool_choice": {"type": "tool", "name": "tender_analysis"},
    }
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return _send_request(request, "anthropic")


def _analyze_with_openai(
    api_key: str,
    segments: list[DocumentSegment],
    profile: BusinessProfile,
) -> TenderAnalysis:
    endpoint = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "tender_analysis", "strict": True, "schema": ANALYSIS_SCHEMA},
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _analysis_input(segments, profile)},
        ],
    }
    request = urllib.request.Request(
        f"{endpoint}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    return _send_request(request, "openai")


def _source_verified(source: dict, segments: list[DocumentSegment]) -> bool:
    location = source.get("location") or (f"Page {source['page']}" if source.get("page") else "")
    return any(
        segment.document == source.get("document", "Tender.pdf")
        and segment.location == location
        and (source.get("page") is None or segment.page == source["page"])
        and _quote_found(source["quote"], segment.text)
        for segment in segments
    )


def _prune_unverified(result: TenderAnalysis, segments: list[DocumentSegment]) -> TenderAnalysis:
    """Drop citations that cannot be found in the documents, and any item left without one.

    Only verified evidence is shown. The rationale must keep at least one verified
    source, otherwise _check_sources still rejects the analysis.
    """
    data = result.model_dump()
    removed = 0
    rationale_sources = [s for s in data["rationale"]["sources"] if _source_verified(s, segments)]
    removed += len(data["rationale"]["sources"]) - len(rationale_sources)
    if rationale_sources:
        data["rationale"]["sources"] = rationale_sources
    for field in ("mandatory_documents", "key_deadlines", "missing_requirements", "submission_plan"):
        kept = []
        for item in data.get(field, []):
            verified = [s for s in item.get("sources", []) if _source_verified(s, segments)]
            removed += len(item.get("sources", [])) - len(verified)
            if verified or (field == "submission_plan" and not item.get("sources")):
                item["sources"] = verified
                kept.append(item)
            elif field == "submission_plan":
                item["sources"] = []
                kept.append(item)
        data[field] = kept
    if not data["submission_plan"]:
        return result
    if removed:
        print(f"[tenderready] removed {removed} unverifiable citation(s)", file=sys.stderr, flush=True)
    return TenderAnalysis.model_validate(data)


def analyze_tender(
    values: list[DocumentSegment | str],
    profile: BusinessProfile,
) -> TenderAnalysis:
    segments = _as_segments(values)
    provider, api_key = _configured_provider()  # no key: fail, never fall back
    last_error: HTTPException | None = None
    for attempt in range(1, MAX_AI_ATTEMPTS + 1):
        try:
            if provider == "anthropic":
                result = _analyze_with_anthropic(api_key, segments, profile)
            else:
                result = _analyze_with_openai(api_key, segments, profile)
            result = _prune_unverified(result, segments)
            _check_sources(result, segments)
            return result
        except HTTPException as exc:
            last_error = exc
            print(f"[tenderready] AI attempt {attempt} failed: {exc.detail}", file=sys.stderr, flush=True)
            if "timed out" in str(exc.detail).lower() or "could not connect" in str(exc.detail).lower():
                break
    if not FALLBACK_ENABLED:
        raise last_error
    try:
        from app.basic_check import basic_check

        print("[tenderready] using basic check fallback", file=sys.stderr, flush=True)
        fallback = basic_check(segments, profile)
        _check_sources(fallback, segments)
        return fallback
    except Exception as fallback_error:
        print(f"[tenderready] basic check failed: {fallback_error}", file=sys.stderr, flush=True)
        raise last_error
