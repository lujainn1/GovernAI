"""Tests for the Document Processing Agent.

Uses generated DOCX fixtures (via python-docx, already a dependency) for
the required "one Arabic document, one English document" cases, since
that avoids pulling in a PDF-authoring library just for tests. PDF-path
behavior (page count, corrupted-file handling, OCR fallback wiring) is
covered separately using pypdf, which the project already depends on.

Arabic sample text is built from Unicode codepoints rather than literal
Arabic characters so this test file stays plain ASCII source.
"""
from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfWriter

from app.agents.document_agent import DocumentProcessingAgent
from app.models import AIUseCase, DocumentLanguage
from app.prompts import build_use_case_brief
from app.tools.document_extract import DocumentExtractionError, UnsupportedFileTypeError
from app.tools.document_type import classify_document_type
from app.tools.language_detection import detect_language


def _arabic(*codepoints: int) -> str:
    return "".join(chr(cp) for cp in codepoints)


# "hatha huwa nass siyasat al-khususiya al-khass bina" (this is our privacy policy text)
ARABIC_PRIVACY_POLICY_TEXT = _arabic(
    0x0647, 0x0630, 0x0647, 0x0020,  # "hathihi "
    0x0633, 0x064A, 0x0627, 0x0633, 0x0629, 0x0020,  # "siyasat "
    0x0627, 0x0644, 0x062E, 0x0635, 0x0648, 0x0635, 0x064A, 0x0629, 0x0020,  # "al-khususiya "
    0x0644, 0x0644, 0x0634, 0x0631, 0x0643, 0x0629,  # "lil-sharika" (of the company)
)

ENGLISH_PRIVACY_POLICY_TEXT = (
    "This Privacy Policy describes how our company collects, uses, and "
    "protects personal data submitted by customers."
)


def _make_docx(paragraphs) -> bytes:
    doc = Document()
    for para in paragraphs:
        doc.add_paragraph(para)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# =========================================================
# Language detection (unit)
# =========================================================


def test_detect_language_english():
    assert detect_language(ENGLISH_PRIVACY_POLICY_TEXT) == "english"


def test_detect_language_arabic():
    assert detect_language(ARABIC_PRIVACY_POLICY_TEXT) == "arabic"


def test_detect_language_mixed():
    mixed = ENGLISH_PRIVACY_POLICY_TEXT + " " + ARABIC_PRIVACY_POLICY_TEXT
    assert detect_language(mixed) == "mixed"


def test_detect_language_unknown_for_no_letters():
    assert detect_language("12345 !!! ---") == "unknown"


# =========================================================
# Document type classification (unit)
# =========================================================


def test_classify_document_type_privacy_policy():
    assert classify_document_type(ENGLISH_PRIVACY_POLICY_TEXT) == "privacy_policy"


def test_classify_document_type_unknown_for_generic_text():
    assert classify_document_type("The quick brown fox jumps over the lazy dog.") == "unknown"


# =========================================================
# DocumentProcessingAgent.process - the required Arabic + English cases
# =========================================================


def test_process_english_docx():
    content = _make_docx([ENGLISH_PRIVACY_POLICY_TEXT, "Contact privacy@example.com for questions."])
    result = DocumentProcessingAgent().process("privacy_policy_en.docx", content)

    assert result.file_name == "privacy_policy_en.docx"
    assert result.file_type == "docx"
    assert result.detected_language == DocumentLanguage.ENGLISH
    assert "Privacy Policy" in result.extracted_text
    assert result.document_type == "privacy_policy"
    assert result.word_count > 0
    assert result.ocr_used is False
    assert result.warnings == []


def test_process_arabic_docx():
    content = _make_docx([ARABIC_PRIVACY_POLICY_TEXT])
    result = DocumentProcessingAgent().process("privacy_policy_ar.docx", content)

    assert result.file_type == "docx"
    assert result.detected_language == DocumentLanguage.ARABIC
    assert result.document_type == "privacy_policy"
    assert ARABIC_PRIVACY_POLICY_TEXT in result.extracted_text
    assert result.word_count > 0


# =========================================================
# PDF path: page count + OCR-fallback wiring (no OCR binaries required)
# =========================================================


def test_process_pdf_with_no_text_layer_reports_zero_words_and_warns():
    # A PDF with a blank page has no extractable text and, in this test
    # environment, no OCR stack installed either - so extraction should
    # degrade gracefully (empty text + a warning) rather than raising.
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)

    result = DocumentProcessingAgent().process("scanned.pdf", buf.getvalue())

    assert result.file_type == "pdf"
    assert result.page_count == 1
    assert result.word_count == 0
    assert result.ocr_used is False
    assert result.warnings  # explains why extracted_text is empty


# =========================================================
# Error handling: unsupported / corrupted files
# =========================================================


def test_process_rejects_unsupported_extension():
    with pytest.raises(UnsupportedFileTypeError):
        DocumentProcessingAgent().process("notes.txt", b"plain text content")


def test_process_rejects_corrupted_pdf():
    with pytest.raises(DocumentExtractionError):
        DocumentProcessingAgent().process("corrupted.pdf", b"this is not a real pdf file")


def test_process_rejects_empty_file():
    with pytest.raises(DocumentExtractionError):
        DocumentProcessingAgent().process("empty.docx", b"")


# =========================================================
# Making extracted text available to the Risk/Policy agents
# =========================================================


def test_to_documentation_feeds_into_use_case_brief():
    content = _make_docx([ENGLISH_PRIVACY_POLICY_TEXT])
    result = DocumentProcessingAgent().process("privacy_policy_en.docx", content)

    documentation = DocumentProcessingAgent.to_documentation(result)
    assert result.extracted_text in documentation
    assert result.document_type in documentation

    # This is exactly the field the Risk Assessment and Policy Compliance
    # agents read (via app.tools.document_analysis.analyze_document on
    # use_case.documentation) - confirm it flows through untouched.
    use_case = AIUseCase(
        name="Customer Chatbot",
        description="A support chatbot",
        owner="test-owner",
        documentation=documentation,
    )
    brief = build_use_case_brief(use_case)
    assert result.extracted_text in brief
