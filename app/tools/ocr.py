"""Optional OCR fallback for scanned/image-only PDFs.

pytesseract and pdf2image are optional dependencies (pdf2image also needs
the poppler system binary, and pytesseract needs the tesseract-ocr system
binary). Importing this module never fails; ocr_pdf() simply reports that
OCR wasn't available if any of that isn't installed, rather than raising -
a scanned PDF with no OCR available should degrade to "little/no text
extracted", not a hard error for the caller.
"""
from typing import Tuple

DEFAULT_OCR_LANGUAGES = "ara+eng"


def is_ocr_available() -> bool:
    """Whether the optional OCR stack (pytesseract + pdf2image, and their
    underlying tesseract/poppler binaries) is usable in this environment."""
    try:
        import pytesseract
        from pdf2image import convert_from_bytes  # noqa: F401

        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


def ocr_pdf(content: bytes, languages: str = DEFAULT_OCR_LANGUAGES) -> Tuple[str, bool]:
    """Best-effort OCR over every page of a PDF.

    Args:
        content: raw PDF bytes.
        languages: tesseract language codes to run, e.g. "ara+eng" for
            combined Arabic + English recognition.

    Returns:
        (text, ocr_used) - ocr_used is False (with text="") whenever the
        OCR stack isn't installed/available or OCR itself fails, so callers
        can fall back to whatever (possibly empty) text they already had.
    """
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError:
        return "", False

    try:
        images = convert_from_bytes(content)
        pages_text = [pytesseract.image_to_string(image, lang=languages) for image in images]
        text = "\n\n".join(pages_text).strip()
        return text, True
    except Exception:
        # Missing poppler/tesseract binaries, a corrupt PDF for the rasterizer,
        # unsupported language pack, etc. - treat as "OCR unavailable" rather
        # than raising, since the caller already has a non-OCR result to fall
        # back on.
        return "", False
