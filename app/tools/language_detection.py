"""Tool: deterministic Arabic/English language detection.

Classifies free text as Arabic, English, or Mixed by counting Arabic-script
vs. Latin-script letters (ignoring digits, punctuation, and whitespace) -
the same cheap-heuristic style as app.tools.document_analysis, so the
Document Processing Agent doesn't need a model call or an extra ML
dependency just to tell which language a document is in.

Character ranges are expressed as plain integer codepoints (rather than
Arabic literals or \\u escapes in a regex) so this file stays plain ASCII
and unambiguous regardless of the editor/terminal's encoding.
"""
from typing import Dict

# Unicode blocks that contain Arabic-script letters: Arabic, Arabic
# Supplement, Arabic Extended-A, and the Arabic Presentation Forms A/B
# blocks (covers standard Arabic plus common ligatures found in real-world
# PDFs/DOCX exports).
_ARABIC_CODEPOINT_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)

# A language is only called "mixed" once the minority script clears this
# share of all letters - a handful of stray Latin acronyms in an Arabic
# document (or vice versa) shouldn't flip the verdict.
_MIXED_THRESHOLD = 0.15


def _is_arabic_letter(ch: str) -> bool:
    cp = ord(ch)
    return any(start <= cp <= end for start, end in _ARABIC_CODEPOINT_RANGES)


def _is_latin_letter(ch: str) -> bool:
    return ("A" <= ch <= "Z") or ("a" <= ch <= "z")


def language_breakdown(text: str) -> Dict[str, int]:
    """Return raw Arabic/Latin letter counts, useful for debugging/logging."""
    arabic = 0
    latin = 0
    for ch in text or "":
        if _is_arabic_letter(ch):
            arabic += 1
        elif _is_latin_letter(ch):
            latin += 1
    return {"arabic_letters": arabic, "latin_letters": latin}


def detect_language(text: str) -> str:
    """Classify text as "arabic", "english", "mixed", or "unknown".

    Args:
        text: the document text to classify.

    Returns "unknown" when the text has no Arabic or Latin letters at all
    (e.g. empty text, or a document that is pure numbers/symbols).
    """
    counts = language_breakdown(text)
    arabic_count = counts["arabic_letters"]
    latin_count = counts["latin_letters"]
    total = arabic_count + latin_count

    if total == 0:
        return "unknown"

    arabic_ratio = arabic_count / total
    latin_ratio = latin_count / total

    if arabic_ratio >= (1 - _MIXED_THRESHOLD):
        return "arabic"
    if latin_ratio >= (1 - _MIXED_THRESHOLD):
        return "english"
    return "mixed"
