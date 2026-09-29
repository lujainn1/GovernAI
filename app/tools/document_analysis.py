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
    # Identity/demographic fields common in Saudi use cases (PDPL sensitive data
    # and typical bias proxies).
    "national id", "iqama", "nationality", "gender", "date of birth", "personal photo",
    "salary", "voiceprint", "voice print", "geolocation", "patient",
]

# Arabic equivalents. Matched against Arabic-normalized text (alef/yaa/taa
# marbuta variants and diacritics folded) so spelling variants still hit.
SENSITIVE_KEYWORDS_AR = [
    "الصحية", "السجل الصحي", "الحالة الصحية", "الطبي", "الطبية", "تشخيص", "المريض",
    "بيانات المرضى", "بيومتري", "بصمة", "التعرف على الوجه", "بصمة الصوت", "الجيني",
    "الجينية", "الجينات", "رقم الهوية", "الهوية الوطنية", "الاقامة", "رقم الاقامة",
    "جواز السفر", "الجنسية", "الجنس", "تاريخ الميلاد", "العمر", "الصورة الشخصية",
    "الاطفال", "القاصرين", "الديانة", "المذهب", "العرق", "الاصل العرقي",
    "السجل الجنائي", "السوابق", "الحساب البنكي", "الراتب", "السجل الائتماني",
    "الجدارة الائتمانية", "الموقع الجغرافي", "الانتماء السياسي",
]

# Saudi national ID (starts with 1) / Iqama (starts with 2): 10 digits.
SAUDI_ID_RE = re.compile(r"(?<!\d)[12]\d{9}(?!\d)")

_AR_DIACRITICS = re.compile(r"[\u064B-\u0652\u0640]")


def _normalize_arabic(text: str) -> str:
    text = _AR_DIACRITICS.sub("", text)
    return (
        text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        .replace("ى", "ي").replace("ة", "ه")
    )


def analyze_document(text: str) -> Dict[str, Any]:
    """Scan free-text documentation for PII patterns and sensitive topics.

    Args:
        text: the document text to analyze (e.g. use-case documentation).
    """
    text = text or ""
    lower = text.lower()
    normalized_ar = _normalize_arabic(text)
    found_keywords: List[str] = sorted(
        {kw for kw in SENSITIVE_KEYWORDS if kw in lower}
        | {kw for kw in SENSITIVE_KEYWORDS_AR if _normalize_arabic(kw) in normalized_ar}
    )

    return {
        "word_count": len(text.split()),
        "emails_found": len(EMAIL_RE.findall(text)),
        "ssn_like_patterns_found": len(SSN_RE.findall(text)),
        "credit_card_like_patterns_found": len(CREDIT_CARD_RE.findall(text)),
        "phone_like_patterns_found": len(PHONE_RE.findall(text)),
        "saudi_id_like_patterns_found": len(SAUDI_ID_RE.findall(text)),
        "sensitive_keywords_found": found_keywords,
        "contains_pii_indicators": bool(
            EMAIL_RE.search(text) or SSN_RE.search(text) or CREDIT_CARD_RE.search(text)
            or SAUDI_ID_RE.search(text)
        ),
    }


ANALYZE_DOCUMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "analyze_document",
        "description": (
            "Run deterministic pattern analysis over a block of free text "
            "(e.g. use-case documentation) to detect PII indicators (emails, "
            "SSNs, Saudi national ID / Iqama numbers, credit-card-like numbers, "
            "phone numbers) and sensitive-topic keywords in English and Arabic "
            "(health, biometric, children, nationality, gender, etc.)."
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
