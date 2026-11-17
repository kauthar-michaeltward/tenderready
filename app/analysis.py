import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from fastapi import HTTPException

from app.models import BusinessProfile, TenderAnalysis


SYSTEM_PROMPT = """You assess government tender fit for African small and medium businesses.
Use only the supplied tender pages as evidence about tender requirements, documents, dates,
and criteria. Compare those explicit requirements with the supplied business profile.
Do not invent tender facts, eligibility criteria, documents, or deadlines. If the tender does
not say something, say that it is not stated without presenting it as a tender requirement.
Every cited quote must be copied verbatim from the supplied page text, be no longer than 500
characters, and identify its 1-based page number. Cite relevant tender passages for eligibility,
each mandatory document, each deadline, each missing requirement, and any plan step that relies
on tender details. Profile gaps are not proof that the business fails a requirement; distinguish
unknown information from a confirmed mismatch. Use the exact JSON shape requested by the caller.
"""

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
        "mandatory_documents": {"type": "array", "items": {"$ref": "#/$defs/document"}},
        "key_deadlines": {"type": "array", "items": {"$ref": "#/$defs/deadline"}},
        "missing_requirements": {"type": "array", "items": {"$ref": "#/$defs/missing"}},
        "submission_plan": {"type": "array", "items": {"$ref": "#/$defs/step"}},
    },
    "$defs": {
        "source": {
            "type": "object", "additionalProperties": False, "required": ["page", "quote"],
            "properties": {"page": {"type": "integer"}, "quote": {"type": "string"}},
        },
        "rationale": {
            "type": "object", "additionalProperties": False, "required": ["text", "sources"],
            "properties": {
                "text": {"type": "string"},
                "sources": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/source"}},
            },
        },
        "document": {
            "type": "object", "additionalProperties": False, "required": ["name", "reason", "sources"],
            "properties": {
                "name": {"type": "string"}, "reason": {"type": "string"},
                "sources": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/source"}},
            },
        },
        "deadline": {
            "type": "object", "additionalProperties": False, "required": ["name", "date", "sources"],
            "properties": {
                "name": {"type": "string"}, "date": {"type": "string"},
                "sources": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/source"}},
            },
        },
        "missing": {
            "type": "object", "additionalProperties": False, "required": ["requirement", "profile_gap", "sources"],
            "properties": {
                "requirement": {"type": "string"}, "profile_gap": {"type": "string"},
                "sources": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/source"}},
            },
        },
        "step": {
            "type": "object", "additionalProperties": False, "required": ["step", "due_date", "sources"],
            "properties": {
                "step": {"type": "string"}, "due_date": {"type": "string"},
                "sources": {"type": "array", "items": {"$ref": "#/$defs/source"}},
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
    return re.sub(r"\s+", " ", text).strip()


def _check_sources(analysis: TenderAnalysis, pages: list[str]) -> None:
    for source in _iter_sources(analysis.model_dump()):
        if source["page"] > len(pages):
            raise HTTPException(
                status_code=502,
                detail="The AI returned a source page that does not exist in the uploaded PDF. Please retry.",
            )
        if _normalize_quote(source["quote"]) not in _normalize_quote(pages[source["page"] - 1]):
            raise HTTPException(
                status_code=502,
                detail="The AI returned a passage that could not be verified in the uploaded PDF. Please retry.",
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


def _analysis_input(pages: list[str], profile: BusinessProfile) -> str:
    return json.dumps(
        {
            "business_profile": profile.model_dump(),
            "tender_pages": [{"page": index, "text": text} for index, text in enumerate(pages, 1)],
            "response_instructions": (
                "Return eligibility, rationale {text,sources}, mandatory_documents "
                "[{name,reason,sources}], key_deadlines [{name,date,sources}], "
                "missing_requirements [{requirement,profile_gap,sources}], and "
                "submission_plan [{step,due_date,sources}]. Give a useful ordered plan. "
                "Use only exact page text in source quotes."
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


def _send_request(request: urllib.request.Request, provider: str) -> TenderAnalysis:
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_data = json.loads(response.read())
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
            result = TenderAnalysis.model_validate(tool_result)
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
        raise HTTPException(
            status_code=502,
            detail="The AI provider returned an invalid response. Please try again.",
        ) from exc
    return result


def _analyze_with_anthropic(api_key: str, pages: list[str], profile: BusinessProfile) -> TenderAnalysis:
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    payload = {
        "model": model,
        "max_tokens": 4096,
        "temperature": 0.1,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _analysis_input(pages, profile)}],
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


def _analyze_with_openai(api_key: str, pages: list[str], profile: BusinessProfile) -> TenderAnalysis:
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
            {"role": "user", "content": _analysis_input(pages, profile)},
        ],
    }
    request = urllib.request.Request(
        f"{endpoint}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    return _send_request(request, "openai")


def analyze_tender(pages: list[str], profile: BusinessProfile) -> TenderAnalysis:
    provider, api_key = _configured_provider()
    if provider == "anthropic":
        result = _analyze_with_anthropic(api_key, pages, profile)
    else:
        result = _analyze_with_openai(api_key, pages, profile)
    _check_sources(result, pages)
    return result
