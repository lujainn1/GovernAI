"""Document Processing Agent.

Turns an uploaded PDF or DOCX file into structured, governance-ready
output: detected language (Arabic / English / Mixed), extracted text
(falling back to OCR for scanned PDFs - see app.tools.ocr), page count,
and a best-effort document type.

Unlike the Risk/Policy/Decision agents, this agent does not call an LLM -
extraction, language detection, and type classification are all
deterministic, so it can run without an OPENAI_API_KEY and produce the
exact same output for the same file every time. It does not subclass
BaseAgent for that reason; it is intentionally a plain, dependency-light
pipeline that other agents can call directly.

To keep it decoupled from the Risk/Policy/Decision agents (per the
platform's "don't touch what already works" rule), this module does not
modify AIUseCase or the orchestrator. Instead it exposes
`to_documentation()`, which formats a DocumentProcessingResult into the
free-text blob those agents already consume via AIUseCase.documentation -
the Coordinator/Decision Agent (app.orchestrator.GovernanceOrchestrator)
can wire the two together with a single extra line:

    doc_result = DocumentProcessingAgent().process(filename, content)
    use_case = AIUseCase(..., documentation=DocumentProcessingAgent.to_documentation(doc_result))
    orchestrator.run(use_case)
"""
from pathlib import Path
from typing import Optional

from app.models import DocumentLanguage, DocumentProcessingResult
from app.tools.document_extract import (
    DocumentExtractionError,
    UnsupportedFileTypeError,
    extract_text,
)
from app.tools.document_type import classify_document_type
from app.tools.language_detection import detect_language


class DocumentProcessingError(RuntimeError):
    """Raised when the pipeline fails for a reason other than an
    unsupported file type or a parser-level extraction failure (those
    raise UnsupportedFileTypeError / DocumentExtractionError instead, so
    API callers can keep mapping them to the same 415 / 422 responses)."""


class DocumentProcessingAgent:
    """Accepts a PDF or DOCX upload and returns a DocumentProcessingResult."""

    name = "document_processing_agent"

    # Narrower than app.tools.document_extract.SUPPORTED_EXTENSIONS: this
    # agent is scoped to PDF/DOCX per its spec, even though the underlying
    # extraction tool also understands pptx/xlsx/txt/md for the existing
    # /documents/extract endpoint.
    SUPPORTED_EXTENSIONS = {".pdf", ".docx"}

    def process(self, filename: str, content: bytes) -> DocumentProcessingResult:
        """Extract text + metadata from an uploaded PDF/DOCX file.

        Args:
            filename: original filename (used to pick the parser and to
                populate file_type in the result).
            content: raw file bytes.

        Raises:
            UnsupportedFileTypeError: extension isn't .pdf or .docx.
            DocumentExtractionError: the file is corrupted/unparsable.
            DocumentProcessingError: an unexpected failure elsewhere in the
                pipeline (language detection/classification never raise in
                practice, but this keeps the contract explicit).
        """
        ext = Path(filename).suffix.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise UnsupportedFileTypeError(
                f"Document Processing Agent only accepts PDF and DOCX files, got "
                f"'{ext or filename}'."
            )

        if not content:
            raise DocumentExtractionError(f"{filename} is empty.")

        # Extraction errors are already well-typed (UnsupportedFileTypeError,
        # DocumentExtractionError) - let them propagate unchanged.
        extraction = extract_text(filename, content)

        try:
            language = detect_language(extraction.text)
            document_type = classify_document_type(extraction.text)
        except Exception as exc:  # language/type heuristics are pure regex; guard anyway
            raise DocumentProcessingError(
                f"Failed to analyze extracted text from {filename}: {exc}"
            ) from exc

        warnings = []
        if extraction.word_count == 0:
            warnings.append(
                "No text could be extracted (or recovered via OCR). The file may be a "
                "scanned document with unsupported image quality, or contain no text."
            )
        elif getattr(extraction, "ocr_used", False):
            warnings.append("Text was recovered via OCR from what appears to be a scanned PDF.")

        return DocumentProcessingResult(
            file_name=filename,
            file_type=ext.lstrip("."),
            detected_language=DocumentLanguage(language),
            extracted_text=extraction.text,
            page_count=extraction.pages,
            document_type=document_type,
            word_count=extraction.word_count,
            ocr_used=getattr(extraction, "ocr_used", False),
            warnings=warnings,
        )

    @staticmethod
    def to_documentation(result: DocumentProcessingResult) -> str:
        """Format a DocumentProcessingResult as the free-text blob expected
        by AIUseCase.documentation, so the Risk Assessment and Policy
        Compliance agents can read it exactly like any pasted-in text (via
        app.tools.document_analysis.analyze_document, called on
        use_case.documentation by both agents already)."""
        header = (
            f"[Document: {result.file_name} | type: {result.document_type} | "
            f"language: {result.detected_language.value} | "
            f"pages: {result.page_count if result.page_count is not None else 'n/a'}]"
        )
        return f"{header}\n\n{result.extracted_text}"


def process_document(filename: str, content: bytes, agent: Optional[DocumentProcessingAgent] = None) -> DocumentProcessingResult:
    """Module-level convenience wrapper around DocumentProcessingAgent.process,
    for callers (e.g. the Coordinator) that don't want to manage an agent
    instance themselves."""
    return (agent or DocumentProcessingAgent()).process(filename, content)
