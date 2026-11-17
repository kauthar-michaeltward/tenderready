from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentSegment:
    document: str
    location: str
    text: str
    page: int | None = None
