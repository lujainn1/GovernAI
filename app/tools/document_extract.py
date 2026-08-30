"""Text extraction for uploaded governance documents.

Backs the `POST /documents/extract` endpoint used by the Submit form's file
dropzone. Extracted text is handed back to the frontend, which folds it into
the use case's `documentation` field before submission - the agents then see
it through the existing `analyze_document` tool, same as pasted-in text.

This is plain text extraction (no OCR): scanned/image-only PDFs or slides
will yield little or no text, which is surfaced as a low word count rather
than an error.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import openpyxl
from docx import Document
from pptx import Presentation
from pypdf import PdfReader


class UnsupportedFileTypeError(ValueError):
    pass


class DocumentExtractionError(RuntimeError):
    pass


@dataclass
class ExtractionResult:
    text: str
    word_count: int
    pages: Optional[int] = None


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt"}
MAX_FILE_SIZE = 25 * 1024 * 1024


def _extract_pdf(content: bytes) -> ExtractionResult:
    from io import BytesIO

    reader = PdfReader(BytesIO(content))
    pages_text = [page.extract_text() or "" for page in reader.pages]
    text = "\n\n".join(pages_text).strip()
    return ExtractionResult(text=text, word_count=len(text.split()), pages=len(reader.pages))


def _extract_docx(content: bytes) -> ExtractionResult:
    from io import BytesIO

    doc = Document(BytesIO(content))
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return ExtractionResult(text=text, word_count=len(text.split()))


def _extract_pptx(content: bytes) -> ExtractionResult:
    from io import BytesIO

    prs = Presentation(BytesIO(content))
    chunks = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                chunks.append(shape.text_frame.text.strip())
    text = "\n".join(chunks)
    return ExtractionResult(text=text, word_count=len(text.split()), pages=len(prs.slides))


def _extract_xlsx(content: bytes) -> ExtractionResult:
    from io import BytesIO

    wb = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=True)
    chunks = []
    for sheet in wb.worksheets:
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                chunks.append(" | ".join(cells))
    text = "\n".join(chunks)
    return ExtractionResult(text=text, word_count=len(text.split()))


def _extract_plain_text(content: bytes) -> ExtractionResult:
    text = content.decode("utf-8", errors="replace").strip()
    return ExtractionResult(text=text, word_count=len(text.split()))


def extract_text(filename: str, content: bytes) -> ExtractionResult:
    """Extract plain text from an uploaded document.

    Args:
        filename: original filename, used only to determine the parser.
        content: raw file bytes.

    Raises:
        UnsupportedFileTypeError: extension isn't one of SUPPORTED_EXTENSIONS.
        DocumentExtractionError: the file couldn't be parsed (corrupt/malformed).
    """
    ext = Path(filename).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{ext or filename}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    try:
        if ext == ".pdf":
            return _extract_pdf(content)
        if ext == ".docx":
            return _extract_docx(content)
        if ext == ".pptx":
            return _extract_pptx(content)
        if ext == ".xlsx":
            return _extract_xlsx(content)
        return _extract_plain_text(content)
    except (UnsupportedFileTypeError, DocumentExtractionError):
        raise
    except Exception as exc:  # parser libraries raise their own varied exceptions
        raise DocumentExtractionError(f"Could not parse {filename}: {exc}") from exc
