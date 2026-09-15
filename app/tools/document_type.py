"""Tool: deterministic document-type classification.

Guesses what kind of governance document a piece of text is (privacy
policy, DPIA, contract, model card, ...) via bilingual (English + Arabic)
keyword matching - the same lightweight, reproducible approach used
elsewhere in the platform (see app.tools.document_analysis,
app.tools.risk_rules) rather than an extra model call.

Arabic keywords are written using chr()/codepoint-built strings instead of
literal Arabic text so this module stays plain ASCII source.
"""
from typing import Dict, List

_AR = chr  # alias to keep the keyword tables below readable


def _arabic(*codepoints: int) -> str:
    return "".join(chr(cp) for cp in codepoints)


# Keyword -> document_type. Checked as a case-insensitive substring match
# against the extracted text. English keywords are plain literals; Arabic
# keywords are built from codepoints to avoid non-ASCII source.
_ENGLISH_KEYWORDS: Dict[str, List[str]] = {
    "privacy_policy": ["privacy policy", "privacy notice"],
    "data_protection_impact_assessment": [
        "data protection impact assessment",
        "dpia",
    ],
    "risk_assessment": ["risk assessment report", "risk assessment"],
    "contract_or_agreement": [
        "data processing agreement",
        "service agreement",
        "terms and conditions",
        "contract",
        "agreement",
    ],
    "model_card": ["model card"],
    "audit_report": ["audit report", "audit findings"],
    "terms_of_service": ["terms of service"],
    "policy_document": ["policy", "governance policy", "compliance policy"],
}

# Arabic keyword phrases, spelled out via Unicode codepoints:
#   privacy policy       -> siyasat al-khususiya
#   impact assessment    -> taqyim al-athar
#   agreement/contract   -> ittifaqiya / aqd
#   report                -> taqrir
_ARABIC_KEYWORDS: Dict[str, List[str]] = {
    "privacy_policy": [
        # "siyasat al-khususiya" (privacy policy)
        _arabic(
            0x0633, 0x064A, 0x0627, 0x0633, 0x0629, 0x0020,
            0x0627, 0x0644, 0x062E, 0x0635, 0x0648, 0x0635, 0x064A, 0x0629,
        )
    ],
    "data_protection_impact_assessment": [
        # "taqyim al-athar" (impact assessment)
        _arabic(
            0x062A, 0x0642, 0x064A, 0x064A, 0x0645, 0x0020,
            0x0627, 0x0644, 0x0623, 0x062B, 0x0631,
        )
    ],
    "contract_or_agreement": [
        _arabic(0x0627, 0x062A, 0x0641, 0x0627, 0x0642, 0x064A, 0x0629),  # "ittifaqiya" (agreement)
        _arabic(0x0639, 0x0642, 0x062F),  # "aqd" (contract)
    ],
    "audit_report": [
        # "taqrir tadqiq" (audit report)
        _arabic(0x062A, 0x0642, 0x0631, 0x064A, 0x0631, 0x0020,
                0x062A, 0x062F, 0x0642, 0x064A, 0x0642),
    ],
    "risk_assessment": [
        # "taqyim al-makhatir" (risk assessment)
        _arabic(
            0x062A, 0x0642, 0x064A, 0x064A, 0x0645, 0x0020,
            0x0627, 0x0644, 0x0645, 0x062E, 0x0627, 0x0637, 0x0631,
        )
    ],
    "policy_document": [
        _arabic(0x0633, 0x064A, 0x0627, 0x0633, 0x0629),  # "siyasa" (policy)
    ],
}

UNKNOWN_DOCUMENT_TYPE = "unknown"


def classify_document_type(text: str) -> str:
    """Best-effort guess at the document's type from bilingual keywords.

    Args:
        text: the extracted document text to classify.

    Returns one of the keys in _ENGLISH_KEYWORDS / _ARABIC_KEYWORDS (e.g.
    "privacy_policy", "risk_assessment", "contract_or_agreement") or
    UNKNOWN_DOCUMENT_TYPE if nothing matches. Checked in a fixed, most-
    specific-first order so e.g. a DPIA isn't mis-classified as a generic
    "policy_document" just because it also contains the word "policy".
    """
    lowered = (text or "").lower()
    check_order = [
        "data_protection_impact_assessment",
        "privacy_policy",
        "risk_assessment",
        "model_card",
        "audit_report",
        "terms_of_service",
        "contract_or_agreement",
        "policy_document",
    ]

    for doc_type in check_order:
        for keyword in _ENGLISH_KEYWORDS.get(doc_type, []):
            if keyword in lowered:
                return doc_type
        for keyword in _ARABIC_KEYWORDS.get(doc_type, []):
            # Arabic has no case-folding concern; match against the original text.
            if keyword in (text or ""):
                return doc_type

    return UNKNOWN_DOCUMENT_TYPE
