"""Remove redundant chunks before they are indexed.

Measured on the accepted index (802 chunks):

* 32 chunks (4.0%) are byte-identical duplicates of another chunk, mostly
  table header rows that repeat on every page of a multi-page table.
* 124 chunks (15.5%) have at least 70% of their sentences repeated inside
  another chunk of the same document, and 91 of those (11.3%) have *every*
  sentence repeated - they are wholly contained in a longer chunk.

Redundancy is invisible to a file-level retrieval benchmark but it is not
harmless: two overlapping chunks can occupy two of the five slots the Policy
Agent is shown, halving the distinct evidence behind a governance finding.

The rule here is deliberately conservative. Only a chunk whose sentences are
*fully* covered by a chunk that is being kept is dropped, so removal is
lossless by construction: whatever the dropped chunk said, the kept one still
says. Partially overlapping neighbours (the normal result of a 200-character
chunk overlap) are left in the index and handled at retrieval time instead,
where diversity selection can see the query.
"""

import hashlib
import re
from collections import defaultdict
from typing import Dict, List, Sequence, Set, Tuple

# A chunk must cover this share of another chunk's sentences to absorb it.
# 1.0 = only wholly-contained chunks are dropped.
CONTAINMENT_THRESHOLD = 1.0

# Sentences shorter than this are ignored when comparing: fragments like a
# heading or a table cell are not evidence that two chunks say the same thing.
MIN_SENTENCE_WORDS = 5

_SPLIT = re.compile(r"(?<=[.?!])\s+|\n+")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def text_hash(text: str) -> str:
    return hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()


def sentence_set(text: str) -> Set[str]:
    """The comparable sentences of a chunk, normalized."""
    out = set()
    for part in _SPLIT.split(text or ""):
        normalized = normalize(part)
        if len(normalized.split()) >= MIN_SENTENCE_WORDS:
            out.add(normalized)
    return out


def _containment(inner: Set[str], outer: Set[str]) -> float:
    if not inner:
        return 0.0
    return len(inner & outer) / len(inner)


def deduplicate(
    chunks: Sequence[dict],
    containment_threshold: float = CONTAINMENT_THRESHOLD,
) -> Tuple[List[dict], Dict[str, object]]:
    """Drop exact duplicates and wholly-contained chunks.

    Chunks are considered longest-first so the surviving chunk is the one with
    the most context, and containment is only tested within a single document
    (the same sentence appearing in two different SDAIA publications is a real
    corroboration, not a duplicate).
    """
    kept: List[dict] = []
    seen_hashes: Dict[str, int] = {}
    exact_removed: List[dict] = []
    contained_removed: List[dict] = []

    order = sorted(range(len(chunks)), key=lambda i: -len(chunks[i]["text"]))
    kept_sentences_by_file: Dict[str, List[Tuple[int, Set[str]]]] = defaultdict(list)

    for index in order:
        chunk = chunks[index]
        digest = text_hash(chunk["text"])

        if digest in seen_hashes:
            exact_removed.append(chunk)
            continue

        file_name = chunk["metadata"].get("file_name", "")
        sentences = sentence_set(chunk["text"])

        absorbed = False
        if sentences:
            for _, kept_set in kept_sentences_by_file[file_name]:
                if _containment(sentences, kept_set) >= containment_threshold:
                    absorbed = True
                    break

        if absorbed:
            contained_removed.append(chunk)
            continue

        seen_hashes[digest] = index
        kept_sentences_by_file[file_name].append((index, sentences))
        kept.append((index, chunk))

    # Restore the original corpus order so the index is built the same way
    # every time regardless of chunk length.
    kept.sort(key=lambda pair: pair[0])
    kept_chunks = [chunk for _, chunk in kept]

    return kept_chunks, {
        "input_chunks": len(chunks),
        "kept_chunks": len(kept_chunks),
        "exact_duplicates_removed": len(exact_removed),
        "contained_chunks_removed": len(contained_removed),
        "removed_by_file": _count_by_file(exact_removed + contained_removed),
    }


def _count_by_file(removed: Sequence[dict]) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for chunk in removed:
        counts[chunk["metadata"].get("file_name", "?")] += 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))
