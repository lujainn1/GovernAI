"""
GovernAI RAG Retriever

This module loads the OpenAI embedding model and the local FAISS
vector store, then retrieves the most relevant SDAIA evidence chunks.

Each result includes its source metadata and FAISS distance score.

Important:
- Lower FAISS distance means higher semantic similarity.
- The OpenAI embedding model and FAISS index are loaded only once.
"""

from langchain_community.vectorstores import FAISS

from app.rag.vector_store import (
    VECTOR_STORE_DIR,
    get_embeddings,
)


# Load the OpenAI embedding model once.
embeddings = get_embeddings()


# Load the existing FAISS index once.
vector_store = FAISS.load_local(
    str(VECTOR_STORE_DIR),
    embeddings,
    allow_dangerous_deserialization=True,
)


def retrieve_policy_evidence(query: str, k: int = 5):
    """
    Retrieve the top-k most relevant SDAIA evidence chunks.

    The returned FAISS score is a distance score:
    lower values indicate stronger semantic similarity.
    """
    results_with_scores = vector_store.similarity_search_with_score(
        query,
        k=k,
    )

    evidence = []

    for doc, score in results_with_scores:
        evidence.append(
            {
                "text": doc.page_content,
                "title": doc.metadata.get("title"),
                "file_name": doc.metadata.get("file_name"),
                "page": doc.metadata.get("page"),
                "domain": doc.metadata.get("domain"),
                "audience": doc.metadata.get("audience"),
                "authority": doc.metadata.get("authority"),
                "distance_score": float(score),
            }
        )

    return evidence


# Run a simple retrieval test when executed directly.
if __name__ == "__main__":
    query = "deepfake consent disclosure and ethical use"

    results = retrieve_policy_evidence(
        query,
        k=5,
    )

    print(f"\nQuery: {query}\n")

    for rank, result in enumerate(results, start=1):
        print(f"Rank: {rank}")
        print(f"Title: {result['title']}")
        print(f"Page: {result['page']}")
        print(f"Domain: {result['domain']}")
        print(f"Distance Score: {result['distance_score']:.4f}")
        print(f"Evidence: {result['text'][:400]}")
        print("-" * 70)