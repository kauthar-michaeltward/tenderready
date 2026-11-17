import pymupdf
from fastapi import HTTPException

from app.document_types import DocumentSegment
from app.ocr import extract_ocr_text


MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PAGES = 250
MAX_EXTRACTED_CHARACTERS = 500_000
MIN_TEXT_FOR_IMAGE_HEAVY_PAGE = 100
IMAGE_COVERAGE_THRESHOLD = 0.05


def extract_pdf_pages(content: bytes) -> list[str]:
    return [segment.text for segment in extract_pdf_segments("Tender.pdf", content)]


def extract_pdf_segments(document_name: str, content: bytes) -> list[DocumentSegment]:
    if not content:
        raise HTTPException(status_code=422, detail="The uploaded PDF is empty.")
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDFs must be 12 MB or smaller.")
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.is_encrypted:
                raise HTTPException(status_code=422, detail="Password-protected PDFs are not supported.")
            if document.page_count > MAX_PAGES:
                raise HTTPException(status_code=413, detail="PDFs must have 250 pages or fewer.")
            segments = []
            for page_number, page in enumerate(document, 1):
                text = page.get_text("text").strip()
                widgets = page.widgets()
                if widgets:
                    form_values = [
                        f"{widget.field_name}: {widget.field_value}"
                        for widget in widgets
                        if widget.field_name and widget.field_value
                    ]
                    if form_values:
                        text = "\n".join([text, *form_values]).strip()
                page_area = page.rect.width * page.rect.height
                image_area = sum(
                    max(0, intersection.width) * max(0, intersection.height)
                    for image in page.get_image_info(xrefs=True)
                    if (intersection := pymupdf.Rect(image["bbox"]) & page.rect)
                )
                image_coverage = min(1.0, image_area / page_area) if page_area else 0
                if image_coverage >= IMAGE_COVERAGE_THRESHOLD and len(text) < MIN_TEXT_FOR_IMAGE_HEAVY_PAGE:
                    text = extract_ocr_text(page)
                    if not text:
                        raise HTTPException(
                            status_code=422,
                            detail=f"OCR could not read {document_name}, page {page_number}.",
                        )
                if text:
                    segments.append(
                        DocumentSegment(
                            document_name,
                            f"Page {page_number}",
                            text,
                            page=page_number,
                        )
                    )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="This file could not be read as a valid PDF.") from exc

    if not segments:
        raise HTTPException(
            status_code=422,
            detail=f"No readable text was found in {document_name}. Scanned pages require OCR.",
        )
    if sum(len(segment.text) for segment in segments) > MAX_EXTRACTED_CHARACTERS:
        raise HTTPException(status_code=413, detail=f"The extracted text in {document_name} is too large.")
    return segments
