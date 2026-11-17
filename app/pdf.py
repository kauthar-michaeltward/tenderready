import fitz
from fastapi import HTTPException


MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PAGES = 250
MAX_EXTRACTED_CHARACTERS = 500_000


def extract_pdf_pages(content: bytes) -> list[str]:
    if not content:
        raise HTTPException(status_code=422, detail="The uploaded PDF is empty.")
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDFs must be 12 MB or smaller.")
    try:
        document = fitz.open(stream=content, filetype="pdf")
        if document.is_encrypted:
            raise HTTPException(status_code=422, detail="Password-protected PDFs are not supported.")
        if document.page_count > MAX_PAGES:
            raise HTTPException(status_code=413, detail="PDFs must have 250 pages or fewer.")
        pages = [page.get_text("text").strip() for page in document]
        document.close()
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
