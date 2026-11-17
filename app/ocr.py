import os

import pymupdf
from fastapi import HTTPException


def extract_ocr_text(page: pymupdf.Page) -> str:
    language = os.getenv("TENDERREADY_OCR_LANGUAGE", "eng")
    try:
        text_page = page.get_textpage_ocr(language=language, dpi=300, full=True)
        return page.get_text("text", textpage=text_page).strip()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Local OCR is unavailable. Install Tesseract OCR and ensure it is on PATH, "
                "then restart TenderReady."
            ),
        ) from exc
