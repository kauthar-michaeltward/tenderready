import pymupdf
from fastapi import HTTPException


MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PAGES = 250
MAX_EXTRACTED_CHARACTERS = 500_000
MIN_TEXT_FOR_IMAGE_HEAVY_PAGE = 100
IMAGE_COVERAGE_THRESHOLD = 0.5


def extract_pdf_pages(content: bytes) -> list[str]:
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
            pages = []
            for page in document:
                text = page.get_text("text").strip()
                page_area = page.rect.width * page.rect.height
                image_area = sum(
                    max(0, intersection.width) * max(0, intersection.height)
                    for image in page.get_image_info(xrefs=True)
                    if (intersection := pymupdf.Rect(image["bbox"]) & page.rect)
                )
                image_coverage = min(1.0, image_area / page_area) if page_area else 0
                if (
                    len(text) < MIN_TEXT_FOR_IMAGE_HEAVY_PAGE
                    and image_coverage >= IMAGE_COVERAGE_THRESHOLD
                ):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "This PDF contains a scanned or image-based page with too little "
                            "selectable text. OCR is required before analysis."
                        ),
                    )
                pages.append(text)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="This file could not be read as a valid PDF.") from exc

    if not any(pages):
        raise HTTPException(
            status_code=422,
            detail="No selectable text was found. Scanned PDFs need OCR before analysis.",
        )
    if sum(map(len, pages)) > MAX_EXTRACTED_CHARACTERS:
        raise HTTPException(status_code=413, detail="The extracted PDF text is too large to analyze.")
    return pages
