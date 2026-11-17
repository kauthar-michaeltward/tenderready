from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BusinessProfile(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    business_name: str = Field(min_length=2, max_length=120)
    country: str = Field(min_length=2, max_length=80)
    sector: str = Field(min_length=2, max_length=100)
    years_operating: int = Field(ge=0, le=200)
    annual_revenue: float = Field(ge=0, le=1_000_000_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    registration_number: str = Field(default="", max_length=120)
    certifications: list[str] = Field(default_factory=list, max_length=40)
    capabilities: list[str] = Field(min_length=1, max_length=40)
    geographic_regions: list[str] = Field(min_length=1, max_length=40)

    @field_validator("certifications", "capabilities", "geographic_regions")
    @classmethod
    def clean_lists(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if any(len(value) > 160 for value in cleaned):
            raise ValueError("List entries must be 160 characters or fewer.")
        return cleaned


class SourceRef(BaseModel):
    document: str = Field(default="Tender.pdf", min_length=1, max_length=160)
    location: str = Field(default="", max_length=160)
    page: int | None = Field(default=None, ge=1)
    quote: str = Field(min_length=8, max_length=500)


class Rationale(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    sources: list[SourceRef] = Field(min_length=1, max_length=12)


class DocumentRequirement(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    sources: list[SourceRef] = Field(min_length=1, max_length=8)


class Deadline(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    date: str = Field(min_length=1, max_length=120)
    sources: list[SourceRef] = Field(min_length=1, max_length=8)


class MissingRequirement(BaseModel):
    requirement: str = Field(min_length=1, max_length=300)
    profile_gap: str = Field(min_length=1, max_length=1000)
    sources: list[SourceRef] = Field(min_length=1, max_length=8)


class PlanStep(BaseModel):
    step: str = Field(min_length=1, max_length=500)
    due_date: str = Field(default="", max_length=120)
    sources: list[SourceRef] = Field(default_factory=list, max_length=8)


class TenderAnalysis(BaseModel):
    eligibility: Literal["eligible", "conditional", "ineligible", "unclear"]
    rationale: Rationale
    mandatory_documents: list[DocumentRequirement] = Field(default_factory=list, max_length=40)
    key_deadlines: list[Deadline] = Field(default_factory=list, max_length=30)
    missing_requirements: list[MissingRequirement] = Field(default_factory=list, max_length=40)
    submission_plan: list[PlanStep] = Field(min_length=1, max_length=30)


class AnalysisRecord(BaseModel):
    id: int
    created_at: str
    file_name: str
    page_count: int
    document_count: int = 1
    analysis: TenderAnalysis
