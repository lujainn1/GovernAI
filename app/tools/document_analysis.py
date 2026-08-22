"""Tool: lightweight, deterministic document analysis.

Runs cheap regex/keyword heuristics over free-text documentation supplied
with a use case (e.g. a design doc excerpt or DPIA) so the LLM agents get a
factual, reproducible signal about PII and sensitive-topic mentions instead
of relying purely on their own reading.
"""
import re
from typing import Any, Dict, List

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")
PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")

SENSITIVE_KEYWORDS = [
    "health", "medical", "diagnosis", "biometric", "fingerprint", "facial recognition",
    "genetic", "ssn", "social security", "passport", "credit score", "bank account",
    "children", "minor", "race", "ethnicity", "religion", "sexual orientation",
    "criminal record", "immigration status", "union membership", "political affiliation",
]


def analyze_document(text: str) -> Dict[str, Any]:
    """Scan free-text documentation for PII patterns and sensitive topics.

    Args:
        text: the document text to analyze (e.g. use-case documentation).
    """
    text = text or ""
    lower = text.lower()
    found_keywords: List[str] = sorted({kw for kw in SENSITIVE_KEYWORDS if kw in lower})

    return {
        "word_count": len(text.split()),
        "emails_found": len(EMAIL_RE.findall(text)),
        "ssn_like_patterns_found": len(SSN_RE.findall(text)),
        "credit_card_like_patterns_found": len(CREDIT_CARD_RE.findall(text)),
        "phone_like_patterns_found": len(PHONE_RE.findall(text)),
        "sensitive_keywords_found": found_keywords,
        "contains_pii_indicators": bool(
            EMAIL_RE.search(text) or SSN_RE.search(text) or CREDIT_CARD_RE.search(text)
        ),
    }


ANALYZE_DOCUMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "analyze_document",
        "description": (
            "Run deterministic pattern analysis over a block of free text "
            "(e.g. use-case documentation) to detect PII indicators (emails, "
            "SSNs, credit-card-like numbers, phone numbers) and sensitive-topic "
            "keywords (health, biometric, children, etc.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The document text to analyze."},
            },
            "required": ["text"],
        },
    },
}
