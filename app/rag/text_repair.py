"""Repair PDF-extraction damage in SDAIA source text before it is indexed.

Two defects were measured in the accepted index (802 chunks) and both reach
the Policy Agent as quoted evidence:

1. Lost ligatures. pypdf emits U+FFFD where the PDF's font mapping for a
   ligature glyph is missing, so `define` arrives as `de<FFFD>ne`. 7 distinct
   damaged tokens, 32 occurrences, all in ai-principles.pdf.

2. Column bleed in flattened tables. The self-assessment checklist on
   ai-principles.pdf pp.41-46 is a multi-column table. Extraction interleaves
   the answer column and the principle-name column into the question text, so
   a chunk reads `"Yes Fairness Did you use any sensitive data?"`. The
   leading `Yes` is a cell from a different column and is not an answer to
   anything in the passage - quoting it as evidence is actively misleading.

Nothing here is specific to a document, a query or a benchmark. The ligature
repair is driven by the corpus's own vocabulary: a candidate is accepted only
if it produces a token that actually occurs elsewhere in the corpus, so a
damaged token the corpus cannot vouch for is left alone rather than guessed.
"""

import re
from collections import Counter
from typing import Dict, Iterable, List, Sequence, Tuple

REPLACEMENT = "�"

# The ligatures a PDF font subset commonly fails to map, most frequent first.
# Order only breaks ties; a candidate still has to be a real corpus word.
LIGATURES: Tuple[str, ...] = ("fi", "fl", "ff", "ft", "ffi", "ffl", "fj")

_WORD = re.compile(r"[A-Za-z]{2,}")
# A line that starts with a bare table answer cell.
_ANSWER_CELL = re.compile(r"^(?:yes|no|n/?a)\b[\s:.-]*", re.IGNORECASE)


def build_vocabulary(texts: Iterable[str]) -> Counter:
    """Lower-cased word frequencies over the undamaged parts of the corpus.

    Tokens containing the replacement character are skipped, so damage can
    never vote for its own repair.
    """
    vocab: Counter = Counter()
    for text in texts:
        for token in _WORD.findall(text):
            if REPLACEMENT in token:
                continue
            vocab[token.lower()] += 1
    return vocab


def repair_ligatures(text: str, vocabulary: Counter) -> Tuple[str, int, List[str]]:
    """Replace U+FFFD with the ligature that yields a known corpus word.

    Returns (repaired text, number of tokens repaired, tokens left alone).
    A token is only changed when exactly one candidate is a corpus word, or
    when several are and the most frequent is taken; when none is, the token
    is returned untouched and reported, because inventing a word inside a
    quoted regulation is worse than leaving visible damage.
    """
    if REPLACEMENT not in text:
        return text, 0, []

    repaired = 0
    unresolved: List[str] = []

    def fix(match: re.Match) -> str:
        nonlocal repaired
        token = match.group(0)
        best, best_count = None, 0
        for ligature in LIGATURES:
            candidate = token.replace(REPLACEMENT, ligature)
            count = vocabulary.get(candidate.lower(), 0)
            if count > best_count:
                best, best_count = candidate, count
        if best is None:
            unresolved.append(token)
            return token
        repaired += 1
        return best

    # Only touch the damaged token itself, not the surrounding text.
    out = re.sub(r"[A-Za-z]*" + REPLACEMENT + r"[A-Za-z]*", fix, text)
    return out, repaired, unresolved


def strip_table_column_bleed(text: str) -> Tuple[str, int]:
    """Drop answer-column cells that extraction glued onto question rows.

    Only a line that *begins* with a bare `Yes` / `No` / `N/A` cell and then
    continues with other content is touched, and only that leading cell is
    removed. A line that is genuinely about a yes/no answer keeps its words;
    a line that is only the cell is dropped, since a lone `Yes` carries no
    meaning once separated from its table.
    """
    if not text:
        return text, 0

    removed = 0
    lines: List[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        match = _ANSWER_CELL.match(stripped)
        if match:
            remainder = stripped[match.end():].strip()
            removed += 1
            if not remainder:
                continue  # the line was nothing but the cell
            lines.append(remainder)
        else:
            lines.append(line)
    return "\n".join(lines), removed


def collapse_repeated_lines(text: str) -> Tuple[str, int]:
    """Collapse runs of the same line repeated back to back.

    The checklist table also bleeds its *category* column in, which arrives as
    `Fairness / Fairness / Fairness / Humanity / Humanity`. Consecutive
    identical lines carry no information the first occurrence does not, and
    the repetition inflates a chunk's similarity to any query using the same
    governance vocabulary - which is how these fragments out-rank real policy
    prose. Only exact, adjacent repeats are collapsed, so a document that
    legitimately repeats a phrase in different places keeps every occurrence.
    """
    if not text:
        return text, 0

    collapsed: List[str] = []
    removed = 0
    for line in text.split("\n"):
        key = line.strip().lower()
        if collapsed and key and key == collapsed[-1].strip().lower():
            removed += 1
            continue
        collapsed.append(line)
    return "\n".join(collapsed), removed


def clean_text(text: str, vocabulary: Counter) -> Tuple[str, Dict[str, object]]:
    """All repairs, in order, with a record of what changed."""
    repaired, ligatures_fixed, unresolved = repair_ligatures(text, vocabulary)
    stripped, cells_removed = strip_table_column_bleed(repaired)
    cleaned, repeats_removed = collapse_repeated_lines(stripped)
    return cleaned, {
        "ligatures_fixed": ligatures_fixed,
        "unresolved": unresolved,
        "answer_cells_removed": cells_removed,
        "repeated_lines_removed": repeats_removed,
    }


def clean_documents(
    documents: Sequence[dict],
) -> Tuple[List[dict], Dict[str, object]]:
    """Clean a list of {"text", "metadata"} pages.

    The vocabulary is built from the whole corpus first, so a word damaged on
    one page can be repaired from its undamaged occurrences on another.
    """
    vocabulary = build_vocabulary(doc["text"] for doc in documents)

    cleaned: List[dict] = []
    stats = {
        "pages": len(documents),
        "pages_with_ligature_damage": 0,
        "ligatures_fixed": 0,
        "answer_cells_removed": 0,
        "repeated_lines_removed": 0,
        "unresolved_tokens": Counter(),
    }

    for doc in documents:
        text, record = clean_text(doc["text"], vocabulary)
        if record["ligatures_fixed"] or record["unresolved"]:
            stats["pages_with_ligature_damage"] += 1
        stats["ligatures_fixed"] += record["ligatures_fixed"]
        stats["answer_cells_removed"] += record["answer_cells_removed"]
        stats["repeated_lines_removed"] += record["repeated_lines_removed"]
        stats["unresolved_tokens"].update(record["unresolved"])
        cleaned.append({"text": text, "metadata": doc["metadata"]})

    return cleaned, stats
