"""SDAIA evidence retrieval for the governance agents.

Thin adapter between the accepted RAG retriever (app/rag/retriever.py) and
the Policy Compliance Agent: it builds a retrieval query from a submitted
use case, fetches the accepted Top-5 passages, labels them E1..E5, and
resolves the ids the agent cites back to the real retrieved passage.

Two rules shape this module:

- The retrieval configuration is set here explicitly (Top-5) rather than
  relying on the retriever's own default. What runs is the plain similarity
  search app/rag/retriever.py implements: this build has no routing and no
  reranker, so the soft-routing configuration measured in earlier retrieval
  experiments is NOT what serves a submission. Adding it means changing the
  retriever, not this adapter.
- Source, page and passage text are never taken from model output. The
  agent selects evidence ids only; `resolve_citations` looks each id up in
  the retrieved candidates, so a fabricated id yields no citation.

Retrieval is best-effort, like agent memory: if the index or the embedding
API is unavailable the agents run without SDAIA evidence rather than
failing the request.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("governai.rag.evidence")


# How many passages an agent is shown.
EVIDENCE_TOP_K = 5

# Select the Top-k with app/rag/retriever_v2.py (over-fetch, drop duplicate and
# wholly-contained passages, at most one passage per page, then MMR) instead of
# the raw top-k of a similarity search.
#
# Measured before switching, against the same accepted FAISS index:
#   frozen 18-query benchmark - Hit@1 77.8% (unchanged), Hit@3 94.4% -> 100%,
#     Hit@5 100% (unchanged), MRR 0.8657 -> 0.8796, duplicate slots 7.8% -> 0%,
#     distinct documents per query 1.83 -> 2.33. No metric regressed, and no
#     Hit@k difference was statistically significant either way (n=18).
#   50 unseen governance submissions, production query shape - rank-1 passage
#     IDENTICAL in 50/50 cases, so nothing about the top passage changes;
#     duplicate slots 6.8% -> 0% (30% of cases were affected, now none),
#     distinct documents 2.72 -> 3.66, self-assessment-table slots 6.8% -> 2.8%,
#     passages carrying mojibake 4.4% -> 2.0%.
#
# Set to False to fall back to the previous behaviour exactly.
USE_DIVERSITY_SELECTION = True

# Label prefix for the passages shown to an agent. Ids are per request.
EVIDENCE_ID_PREFIX = "E"


def build_evidence_query(use_case, risk=None) -> str:
    """
    Retrieval query built from the submitted use case.

    Only production intake fields are used: name, description, data
    classification, deployment context and autonomy level, plus the risk
    factors when a risk assessment is available. No evaluation or
    ground-truth information is involved.
    """
    parts = [use_case.name, use_case.description]

    for label, value in (
        ("data classification", getattr(use_case, "data_classification", None)),
        ("deployment context", getattr(use_case, "deployment_context", None)),
        ("autonomy level", getattr(use_case, "autonomy_level", None)),
    ):
        if value:
            parts.append(f"{label}: {value}")

    if risk is not None:
        factors = getattr(risk, "risk_factors", None) or []

        if factors:
            parts.append("risk factors: " + ", ".join(str(factor) for factor in factors))

    return "\n".join(part for part in parts if part)


def retrieve_evidence(
    query: str,
    k: int = EVIDENCE_TOP_K,
) -> List[Dict[str, Any]]:
    """
    The accepted Top-k SDAIA passages for a query, in retrieval order, each
    labelled with a stable per-request evidence id.

    Returns [] when retrieval is unavailable; the caller then proceeds
    without SDAIA evidence.
    """
    if not (query or "").strip():
        return []

    try:
        # Imported lazily: importing the retriever loads the FAISS index and
        # constructs the embedding client.
        if USE_DIVERSITY_SELECTION:
            from app.rag.retriever_v2 import retrieve as retrieve_policy_evidence
        else:
            from app.rag.retriever import retrieve_policy_evidence

        results = retrieve_policy_evidence(query, k=k)
    except Exception:  # noqa: BLE001 - retrieval must never fail a request
        logger.warning(
            "SDAIA evidence retrieval unavailable; continuing without evidence",
            exc_info=True,
        )
        return []

    return [
        {
            "evidence_id": f"{EVIDENCE_ID_PREFIX}{rank}",
            "source": result["file_name"],
            "title": result.get("title"),
            "page": result["page"],
            "text": result["text"],
        }
        for rank, result in enumerate(results, start=1)
    ]


def resolve_citations(
    evidence_ids: Optional[Sequence[str]],
    candidates: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Turn the evidence ids an agent cited into citations built from the
    retrieved passages.

    Returns (citations, rejected_ids). Source, page and quote always come
    from `candidates`, never from the agent, so an id that was not
    retrieved produces no citation. Duplicates are collapsed and the
    retrieval order is preserved.
    """
    by_id = {candidate["evidence_id"]: candidate for candidate in candidates}
    citations: List[Dict[str, Any]] = []
    rejected: List[str] = []
    seen = set()

    for evidence_id in evidence_ids or []:
        key = str(evidence_id).strip().upper()

        if key in seen:
            continue

        seen.add(key)
        candidate = by_id.get(key)

        if candidate is None:
            rejected.append(str(evidence_id))
            continue

        citations.append(
            {
                "evidence_id": candidate["evidence_id"],
                "source": candidate["source"],
                "page": candidate["page"],
                "quote": candidate["text"],
            }
        )

    citations.sort(key=lambda citation: list(by_id).index(citation["evidence_id"]))

    if rejected:
        logger.warning(
            "Dropped evidence ids that were not in the retrieved candidates: %s",
            ", ".join(rejected),
        )

    return citations, rejected
