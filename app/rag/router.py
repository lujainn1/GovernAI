"""Document-level routing for SDAIA retrieval.

Ported into Preview from the accepted retrieval checkpoint, where soft routing
was the measured difference between Hit@1 77.8% (plain search, this build) and
94.4% on the same frozen 18-query set. Preview's retriever dropped it; this
restores the mechanism unchanged.

Why it helps, mechanically: passage similarity alone cannot tell that a
question about organisational AI adoption belongs to the adoption framework
rather than to the occupational-standards framework, because a list of job
competencies is lexically dense with the same governance vocabulary. Routing
adds a document-level prior computed from each document's own catalogue
metadata, then blends it into the passage distance - so a passage still has to
be similar, but a passage from a topically wrong document has to be *more*
similar to win.

Scope guarantees:
- Only SOURCE_CATALOG metadata is read: title, domain, audience,
  document_type. No evaluation ground truth (expected source, page or domain)
  is used or reachable from here.
- METADATA_DESCRIPTIONS glosses what each metadata *value* means. It describes
  the documents, never the queries, so it cannot encode a benchmark answer.
- Every function is pure, so routing is testable without FAISS or the API.
"""

import math
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from app.rag.document_loader import SOURCE_CATALOG

# Metadata fields the router is allowed to read.
ROUTING_FIELDS = ("title", "domain", "audience", "document_type")

# Plain-language descriptions of metadata values.
METADATA_DESCRIPTIONS = {
    "domain": {
        "ai_ethics": "ethical principles for artificial intelligence",
        "ai_governance": "AI governance, strategy and organizational adoption",
        "media_ai": "artificial intelligence in news and media",
        "generative_ai": "generative artificial intelligence tools and content",
        "deepfake_ethics": "ethics of deepfakes and synthetic media",
        "ai_workforce": "data and AI workforce, occupations and jobs",
        "ai_education": "AI education, academic programs and qualifications",
    },
    "audience": {
        "all": "all stakeholders",
        "organizations": "organizations and entities",
        "media": "media organizations and journalists",
        "government": "government entities and public-sector employees",
        "public": "members of the public and individual users",
        "professionals": "data and AI professionals and employers",
        "education": "universities and academic institutions",
    },
    "document_type": {
        "principles": "principles",
        "framework": "framework",
        "guidelines": "guidelines",
        "index": "national index, indicators and measurement",
    },
}


def build_document_profiles(catalog: Dict[str, dict] = SOURCE_CATALOG) -> Dict[str, str]:
    """One short metadata profile per document: file_name -> profile text."""
    profiles = {}
    for file_name, metadata in catalog.items():
        parts = [f"Title: {metadata['title']}"]
        for field in ROUTING_FIELDS[1:]:
            value = metadata[field]
            description = METADATA_DESCRIPTIONS[field].get(value, value.replace("_", " "))
            parts.append(f"{field.replace('_', ' ').capitalize()}: {description}")
        profiles[file_name] = ". ".join(parts) + "."
    return profiles


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def route_documents(
    query_vector: Sequence[float],
    profile_vectors: Dict[str, Sequence[float]],
) -> List[Tuple[str, float]]:
    """Documents ranked by metadata relevance, scores min-max scaled to [0, 1].

    The scaling is per query, so a route score says "how well this document
    fits this question relative to the others", not an absolute confidence.
    """
    similarities = {
        file_name: cosine_similarity(query_vector, vector)
        for file_name, vector in profile_vectors.items()
    }
    lowest, highest = min(similarities.values()), max(similarities.values())
    spread = highest - lowest
    scores = {
        file_name: ((similarity - lowest) / spread if spread > 0 else 1.0)
        for file_name, similarity in similarities.items()
    }
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def soft_rerank(
    candidates: Sequence[Tuple[dict, float]],
    route_scores: Dict[str, float],
    route_weight: float,
    k: int,
) -> List[Tuple[int, float]]:
    """Lower each candidate's distance by its document's route score.

    `candidates` is [(metadata, distance)]. Returns [(index, adjusted)] for the
    best k, lower adjusted being better. A document the query does not suit
    keeps its raw distance; the best-fitting document gets the full
    `route_weight` discount.
    """
    adjusted = [
        (index, distance - route_weight * route_scores.get(metadata["file_name"], 0.0))
        for index, (metadata, distance) in enumerate(candidates)
    ]
    adjusted.sort(key=lambda item: item[1])
    return adjusted[:k]


def allowed_documents(ranking: Sequence[Tuple[str, float]], top_n: int) -> Set[str]:
    """The file names of the top-n routed documents (for hard routing)."""
    return {file_name for file_name, _ in ranking[:top_n]}
