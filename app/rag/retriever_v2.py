"""Diversity-aware SDAIA retrieval.

app/rag/retriever.py returns the raw top-k of a similarity search. Measured on
the frozen 18-query benchmark, that put two chunks from the *same page* into
the top 5 on 7 of 18 queries (38.9%), and 7 of the 90 retrieved slots (7.8%)
were redundant. The Policy Agent only ever sees five passages, so a wasted
slot is a third of the distinct evidence behind a governance finding.

This module keeps the same embedding model and the same index format and
changes only how candidates are selected:

1. Over-fetch, then select. Nothing can be re-ranked that was never fetched.
2. Drop near-duplicate candidates (exact text, or one whose sentences are
   already covered by a selected passage).
3. Cap how many passages may come from one page of one document.
4. Penalise structurally low-information chunks - flattened table rows,
   heading runs, bare label columns - by how they are *shaped*, never by
   which document they came from.
5. Choose the final set with Maximal Marginal Relevance so the passages are
   relevant *and* cover different ground.

Every parameter is a keyword argument with a documented default so the
configuration can be measured rather than asserted. `index_dir` is a parameter
too: the same algorithm can run against the accepted index or a rebuilt one,
which is what makes an A/B attributable.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app import config
from app.rag.dedupe import sentence_set, text_hash

# --- defaults ---------------------------------------------------------------
# How many candidates to consider per requested passage.
FETCH_MULTIPLIER = 6
# At most this many passages from a single (document, page).
PER_PAGE_CAP = 1
# MMR trade-off: 1.0 = pure relevance, 0.0 = pure diversity.
MMR_LAMBDA = 0.7
# A selected passage absorbs a candidate covering this share of its sentences.
DUPLICATE_CONTAINMENT = 0.8
# Multiplied into a candidate's relevance when it looks structurally
# low-information. 1.0 disables the penalty.
#
# MEASURED AND DISABLED. At 0.85 this cost 2 of 18 frozen-benchmark queries
# their rank-1 passage (Hit@1 77.8% -> 66.7%) for no gain: on Q013 the correct
# NAII passage on "Adoption Measurement Levels" scores 0.19 on the shape
# heuristic while a less relevant passage from another document scores 0.11, so
# the penalty demoted the better answer. The heuristic cannot tell mildly
# formatted substance from table scaffolding reliably enough to move ranks.
# The per-page cap and de-duplication already remove most table fragments
# (low-info slots 13.3% -> 11.1% with the penalty off), so nothing is lost by
# leaving it at 1.0. `low_information_score` is kept as a diagnostic.
LOW_INFO_WEIGHT = 1.0

_SHORT_LINE_WORDS = 4
_TERMINATOR = re.compile(r"[.!?]\s*$")


@lru_cache(maxsize=4)
def _load(index_dir: str):
    """Load an index once per directory. Imported lazily by callers."""
    from langchain_community.vectorstores import FAISS

    from app.rag.vector_store import get_embeddings

    embeddings = get_embeddings()
    store = FAISS.load_local(
        index_dir, embeddings, allow_dangerous_deserialization=True
    )
    position_to_id = store.index_to_docstore_id
    return store, embeddings, position_to_id


def low_information_score(text: str) -> float:
    """How table-/heading-like a chunk is, in [0, 1]. Higher = less prose.

    Judged only on shape, so it cannot encode a preference for a particular
    source. The two signals are combined deliberately: a document whose real
    content is short declarative lines (the occupational standards framework,
    221 chunks of one-line competencies) has a high short-line ratio but its
    lines *end in full stops*, so it is not penalised. A flattened table row
    or a run of column labels has short lines with no terminators at all.
    """
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]
    if not lines:
        return 1.0

    short = sum(1 for line in lines if len(line.split()) <= _SHORT_LINE_WORDS)
    terminated = sum(1 for line in lines if _TERMINATOR.search(line))

    short_ratio = short / len(lines)
    unterminated_ratio = 1.0 - (terminated / len(lines))

    # Both must be high for a chunk to look like table scaffolding.
    return round(short_ratio * unterminated_ratio, 4)


def _mmr(
    query_vector: np.ndarray,
    candidate_vectors: np.ndarray,
    relevance: np.ndarray,
    k: int,
    lambda_mult: float,
) -> List[int]:
    """Maximal Marginal Relevance over unit-norm embeddings.

    OpenAI embeddings are unit-normalised, so a dot product is the cosine
    similarity and no renormalisation is needed.
    """
    selected: List[int] = []
    remaining = list(range(len(relevance)))

    while remaining and len(selected) < k:
        if not selected:
            best = max(remaining, key=lambda i: relevance[i])
        else:
            chosen = candidate_vectors[selected]

            def score(i: int) -> float:
                redundancy = float(np.max(chosen @ candidate_vectors[i]))
                return lambda_mult * relevance[i] - (1.0 - lambda_mult) * redundancy

            best = max(remaining, key=score)
        selected.append(best)
        remaining.remove(best)

    return selected


def retrieve(
    query: str,
    k: int = 5,
    *,
    # The ACCEPTED index. The cleaned rebuild in app/rag/index_v2.py was
    # measured and rejected: stripping table answer-cells shortened those
    # chunks, which made them denser and MORE competitive, so evidence quality
    # got worse on every diagnostic (duplicate slots 7.8% -> 17.8%, low-info
    # slots 13.3% -> 36.7%, distinct documents 1.83 -> 1.33) and one query lost
    # its source from the top 5 altogether.
    index_dir: str = str(config.VECTOR_STORE_DIR),
    fetch_multiplier: int = FETCH_MULTIPLIER,
    per_page_cap: int = PER_PAGE_CAP,
    mmr_lambda: float = MMR_LAMBDA,
    duplicate_containment: float = DUPLICATE_CONTAINMENT,
    low_info_weight: float = LOW_INFO_WEIGHT,
) -> List[Dict[str, Any]]:
    """Top-k SDAIA passages, de-duplicated and diversified.

    Returns the same shape as app.rag.retriever.retrieve_policy_evidence, plus
    `low_info_score` and `selection_rank`, so a caller (and an evaluation) can
    see why a passage was chosen.
    """
    if not (query or "").strip():
        return []

    store, embeddings, position_to_id = _load(str(Path(index_dir)))

    query_vector = np.asarray(embeddings.embed_query(query), dtype="float32")
    fetch = max(k, k * max(1, fetch_multiplier))
    distances, positions = store.index.search(
        query_vector.reshape(1, -1).copy(), fetch
    )

    candidates: List[Dict[str, Any]] = []
    vectors: List[np.ndarray] = []

    for distance, position in zip(distances[0], positions[0]):
        if position < 0:
            continue
        document = store.docstore.search(position_to_id[int(position)])
        if document is None or isinstance(document, str):
            continue
        candidates.append(
            {
                "text": document.page_content,
                "metadata": document.metadata,
                "distance_score": float(distance),
            }
        )
        vectors.append(store.index.reconstruct(int(position)))

    if not candidates:
        return []

    # --- structural relevance ------------------------------------------------
    # L2 on unit vectors: cosine = 1 - d^2 / 2.
    relevance = np.array(
        [1.0 - (c["distance_score"] / 2.0) for c in candidates], dtype="float64"
    )
    low_info = np.array(
        [low_information_score(c["text"]) for c in candidates], dtype="float64"
    )
    # Interpolate the weight by how table-like the chunk is, so a borderline
    # chunk is nudged rather than demoted.
    relevance = relevance * (1.0 - low_info * (1.0 - low_info_weight))

    # --- duplicate and per-page filtering ------------------------------------
    order = np.argsort(-relevance)
    keep: List[int] = []
    seen_hashes: set = set()
    page_counts: Dict[Tuple[str, Any], int] = {}
    kept_sentences: List[set] = []

    for i in order:
        candidate = candidates[i]
        digest = text_hash(candidate["text"])
        if digest in seen_hashes:
            continue

        page_key = (
            candidate["metadata"].get("file_name"),
            candidate["metadata"].get("page"),
        )
        if page_counts.get(page_key, 0) >= per_page_cap:
            continue

        sentences = sentence_set(candidate["text"])
        if sentences and any(
            len(sentences & already) / len(sentences) >= duplicate_containment
            for already in kept_sentences
        ):
            continue

        seen_hashes.add(digest)
        page_counts[page_key] = page_counts.get(page_key, 0) + 1
        kept_sentences.append(sentences)
        keep.append(int(i))

    if not keep:
        keep = [int(i) for i in order[:k]]

    # --- diversity selection -------------------------------------------------
    kept_vectors = np.vstack([vectors[i] for i in keep])
    chosen = _mmr(
        query_vector,
        kept_vectors,
        relevance[keep],
        k=k,
        lambda_mult=mmr_lambda,
    )

    results: List[Dict[str, Any]] = []
    for rank, local in enumerate(chosen, start=1):
        candidate = candidates[keep[local]]
        metadata = candidate["metadata"]
        results.append(
            {
                "text": candidate["text"],
                "title": metadata.get("title"),
                "file_name": metadata.get("file_name"),
                "page": metadata.get("page"),
                "domain": metadata.get("domain"),
                "audience": metadata.get("audience"),
                "authority": metadata.get("authority"),
                "distance_score": candidate["distance_score"],
                "low_info_score": low_information_score(candidate["text"]),
                "selection_rank": rank,
            }
        )
    return results
