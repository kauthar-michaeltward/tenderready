import csv
import io
import re
from html.parser import HTMLParser
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import openpyxl
import pymupdf
import xlrd
from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from fastapi import HTTPException
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.document_types import DocumentSegment
from app.ocr import extract_ocr_text
from app.pdf import extract_pdf_segments


MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_BUNDLE_BYTES = 40 * 1024 * 1024
MAX_BUNDLE_FILES = 20
MAX_EXTRACTED_CHARACTERS = 500_000
MAX_DOCUMENT_SEGMENTS = 20_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 80 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_SPREADSHEET_ROWS = 20_000
MAX_SPREADSHEET_COLUMNS = 256
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".xls", ".pptx", ".txt", ".csv", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp",
}


def safe_filename(filename: str) -> str:
    leaf = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._ -]", "_", leaf).strip(" .")[:160] or "document"


def extract_document(filename: str, content: bytes) -> list[DocumentSegment]:
    name = safe_filename(filename)
    extension = Path(name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported document type {extension or '(no extension)'}. Supported types: {supported}.",
        )
    if not content:
        raise HTTPException(status_code=422, detail=f"{name} is empty.")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"Each uploaded file must be 12 MB or smaller: {name}.")

    if extension == ".pdf":
        return extract_pdf_segments(name, content)
    if extension in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        return _extract_image(name, extension, content)
    if extension == ".docx":
        return _extract_docx(name, content)
    if extension in {".xlsx", ".xls"}:
        return _extract_spreadsheet(name, extension, content)
    if extension == ".pptx":
        return _extract_presentation(name, content)
    if extension == ".csv":
        return _extract_csv(name, content)
    if extension in {".html", ".htm"}:
        return _extract_html(name, content)
    return _extract_text(name, content)


def extract_bundle(documents: list[tuple[str, bytes]]) -> list[DocumentSegment]:
    if not documents:
        raise HTTPException(status_code=422, detail="Upload at least one tender document.")
    if len(documents) > MAX_BUNDLE_FILES:
        raise HTTPException(status_code=413, detail="Upload no more than 20 files at a time.")
    if sum(len(content) for _, content in documents) > MAX_BUNDLE_BYTES:
        raise HTTPException(status_code=413, detail="The combined upload must be 40 MB or smaller.")

    segments: list[DocumentSegment] = []
    seen_names: set[str] = set()
    for filename, content in documents:
        name = safe_filename(filename)
        if name in seen_names:
            raise HTTPException(status_code=422, detail=f"Remove duplicate filenames before uploading: {name}.")
        seen_names.add(name)
        document_segments = extract_document(name, content)
        if len(segments) + len(document_segments) > MAX_DOCUMENT_SEGMENTS:
            raise HTTPException(status_code=413, detail="The upload contains too many separate text sections.")
        segments.extend(document_segments)

    total_characters = sum(len(segment.text) for segment in segments)
    if total_characters > MAX_EXTRACTED_CHARACTERS:
        raise HTTPException(status_code=413, detail="The extracted document text is too large to analyze.")
    if not any(segment.text for segment in segments):
        raise HTTPException(status_code=422, detail="No readable text was found in the uploaded documents.")
    return segments


def _validate_office_archive(content: bytes, filename: str) -> None:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
    except BadZipFile as exc:
        raise HTTPException(status_code=422, detail=f"{filename} is not a valid Office document.") from exc
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise HTTPException(status_code=413, detail=f"{filename} contains too many internal entries.")
    uncompressed_size = sum(entry.file_size for entry in entries)
    if uncompressed_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise HTTPException(status_code=413, detail=f"{filename} expands beyond the supported document size.")
    if any(entry.file_size > max(1_000_000, entry.compress_size * 200) for entry in entries):
        raise HTTPException(status_code=413, detail=f"{filename} has a suspicious compression ratio.")


def _extract_pdf(name: str, content: bytes) -> list[DocumentSegment]:
    return extract_pdf_segments(name, content)


def _extract_image(name: str, extension: str, content: bytes) -> list[DocumentSegment]:
    try:
        with pymupdf.open(stream=content, filetype=extension.lstrip(".")) as document:
            if document.page_count > 250:
                raise HTTPException(status_code=413, detail=f"{name} contains too many image pages.")
            segments = []
            for page_number, page in enumerate(document, 1):
                text = extract_ocr_text(page)
                if not text:
                    raise HTTPException(status_code=422, detail=f"OCR could not read {name}, page {page_number}.")
                _append_segment(
                    segments, DocumentSegment(name, f"Page {page_number}", text, page=page_number)
                )
            return segments
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{name} could not be read as an image.") from exc


def _extract_docx(name: str, content: bytes) -> list[DocumentSegment]:
    _validate_office_archive(content, name)
    try:
        document = Document(io.BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{name} is not a readable Word document.") from exc

    segments = []
    paragraph_number = 0
    table_number = 0
    for block in document.element.body.iterchildren():
        if isinstance(block, CT_P):
            paragraph_number += 1
            text = Paragraph(block, document).text.strip()
            if text:
                _append_segment(
                    segments,
                    DocumentSegment(name, f"Paragraph {paragraph_number}", text)
                )
        elif isinstance(block, CT_Tbl):
            table_number += 1
            table = Table(block, document)
            for row_number, row in enumerate(table.rows, 1):
                text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if text:
                    _append_segment(
                        segments,
                        DocumentSegment(name, f"Table {table_number}, row {row_number}", text)
                    )

    seen_images = set()
    image_number = 0
    for relation in document.part.rels.values():
        if relation.is_external:
            continue
        image_part = relation.target_part
        if not image_part.content_type.startswith("image/") or image_part.blob in seen_images:
            continue
        seen_images.add(image_part.blob)
        image_number += 1
        text = _ocr_embedded_image(image_part.blob, image_part.content_type.rsplit("/", 1)[-1])
        if text:
            _append_segment(
                segments, DocumentSegment(name, f"Embedded image {image_number}", text)
            )

    for section_number, section in enumerate(document.sections, 1):
        for label, part in (("header", section.header), ("footer", section.footer)):
            text = " | ".join(paragraph.text.strip() for paragraph in part.paragraphs if paragraph.text.strip())
            if text:
                _append_segment(
                    segments,
                    DocumentSegment(name, f"Section {section_number} {label}", text)
                )
    if not segments:
        raise HTTPException(status_code=422, detail=f"No text or table content was found in {name}.")
    return segments


def _extract_spreadsheet(name: str, extension: str, content: bytes) -> list[DocumentSegment]:
    if extension == ".xls":
        return _extract_xls(name, content)
    _validate_office_archive(content, name)
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=False)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{name} is not a readable Excel workbook.") from exc
    segments = []
    try:
        for sheet in workbook.worksheets:
            if sheet.max_row > MAX_SPREADSHEET_ROWS or sheet.max_column > MAX_SPREADSHEET_COLUMNS:
                raise HTTPException(
                    status_code=413,
                    detail=f"{name}, sheet {sheet.title}, exceeds the supported spreadsheet dimensions.",
                )
            for row_number, row in enumerate(
                sheet.iter_rows(max_row=sheet.max_row, max_col=sheet.max_column),
                1,
            ):
                values = [
                    f"{cell.coordinate}: {cell.value}"
                    for cell in row
                    if cell.value is not None and str(cell.value).strip()
                ]
                if values:
                    _append_segment(
                        segments,
                        DocumentSegment(name, f"Sheet {sheet.title}, row {row_number}", " | ".join(values))
                    )
    finally:
        workbook.close()
    if not segments:
        raise HTTPException(status_code=422, detail=f"No cell values were found in {name}.")
    return segments


def _extract_xls(name: str, content: bytes) -> list[DocumentSegment]:
    try:
        workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{name} is not a readable legacy Excel workbook.") from exc
    segments = []
    for sheet in workbook.sheets():
        if sheet.nrows > MAX_SPREADSHEET_ROWS or sheet.ncols > MAX_SPREADSHEET_COLUMNS:
            workbook.release_resources()
            raise HTTPException(
                status_code=413,
                detail=f"{name}, sheet {sheet.name}, exceeds the supported spreadsheet dimensions.",
            )
        for row_number in range(sheet.nrows):
            values = [
                f"Column {column + 1}: {sheet.cell_value(row_number, column)}"
                for column in range(sheet.ncols)
                if str(sheet.cell_value(row_number, column)).strip()
            ]
            if values:
                _append_segment(
                    segments,
                    DocumentSegment(name, f"Sheet {sheet.name}, row {row_number + 1}", " | ".join(values))
                )
    workbook.release_resources()
    if not segments:
        raise HTTPException(status_code=422, detail=f"No cell values were found in {name}.")
    return segments


def _extract_presentation(name: str, content: bytes) -> list[DocumentSegment]:
    _validate_office_archive(content, name)
    try:
        presentation = Presentation(io.BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{name} is not a readable PowerPoint presentation.") from exc
    segments = []
    for slide_number, slide in enumerate(presentation.slides, 1):
        parts = []
        image_number = 0
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text.strip():
                parts.append(shape.text.strip())
            if shape.has_table:
                parts.extend(
                    " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                    for row in shape.table.rows
                )
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                image_number += 1
                image_text = _ocr_embedded_image(shape.image.blob, shape.image.ext)
                if image_text:
                    parts.append(f"Image {image_number}: {image_text}")
        text = "\n".join(part for part in parts if part)
        if text:
            _append_segment(segments, DocumentSegment(name, f"Slide {slide_number}", text))
    if not segments:
        raise HTTPException(status_code=422, detail=f"No text was found in {name}.")
    return segments


def _extract_csv(name: str, content: bytes) -> list[DocumentSegment]:
    text = _decode_text(name, content)
    segments = []
    try:
        for row_number, row in enumerate(csv.reader(io.StringIO(text)), 1):
            if row_number > MAX_SPREADSHEET_ROWS:
                raise HTTPException(status_code=413, detail=f"{name} contains too many CSV rows.")
            values = [value.strip() for value in row if value.strip()]
            if values:
                _append_segment(segments, DocumentSegment(name, f"Row {row_number}", " | ".join(values)))
    except csv.Error as exc:
        raise HTTPException(status_code=422, detail=f"{name} is not a readable CSV file.") from exc
    if not segments:
        raise HTTPException(status_code=422, detail=f"No rows were found in {name}.")
    return segments


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag, _attrs):
        if tag.lower() in {"script", "style"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data):
        if not self.hidden_depth and data.strip():
            self.parts.append(data.strip())


def _extract_html(name: str, content: bytes) -> list[DocumentSegment]:
    text = _decode_text(name, content)
    parser = _VisibleTextParser()
    parser.feed(text)
    visible = "\n".join(parser.parts)
    if not visible:
        raise HTTPException(status_code=422, detail=f"No readable text was found in {name}.")
    return _extract_text(name, visible.encode())


def _extract_text(name: str, content: bytes) -> list[DocumentSegment]:
    text = _decode_text(name, content)
    lines = text.splitlines()
    segments = []
    chunk, start, size = [], 1, 0
    for line_number, line in enumerate(lines, 1):
        if size + len(line) > 4000 and chunk:
            _append_segment(
                segments, DocumentSegment(name, f"Lines {start}-{line_number - 1}", "\n".join(chunk))
            )
            chunk, start, size = [], line_number, 0
        chunk.append(line)
        size += len(line) + 1
    if chunk and any(line.strip() for line in chunk):
        _append_segment(segments, DocumentSegment(name, f"Lines {start}-{len(lines)}", "\n".join(chunk)))
    if not segments:
        raise HTTPException(status_code=422, detail=f"No readable text was found in {name}.")
    return segments


def _decode_text(name: str, content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=422, detail=f"{name} is not valid UTF-8 or UTF-16 text.")


def _append_segment(segments: list[DocumentSegment], segment: DocumentSegment) -> None:
    if len(segments) >= MAX_DOCUMENT_SEGMENTS:
        raise HTTPException(status_code=413, detail="The document contains too many separate text sections.")
    segments.append(segment)


def _ocr_embedded_image(content: bytes, extension: str) -> str:
    try:
        with pymupdf.open(stream=content, filetype=extension.lower()) as image:
            if image.page_count != 1:
                return ""
            return extract_ocr_text(image[0])
    except HTTPException:
        raise
    except Exception:
        return ""
