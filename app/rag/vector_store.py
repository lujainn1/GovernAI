"""
GovernAI RAG Vector Store

This module builds a FAISS vector store from SDAIA document chunks
using OpenAI's text-embedding-3-small embedding model.

The embeddings are created through the OpenAI API, while the FAISS
index is stored locally inside the GovernAI project.
"""

from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS

from app.rag.chunker import create_chunks


# Load environment variables from the project's .env file.
load_dotenv()


# Directory where the FAISS vector store will be saved locally.
VECTOR_STORE_DIR = Path("data/vectorstore/sdaia_faiss")


# OpenAI embedding model used for semantic retrieval.
EMBEDDING_MODEL = "text-embedding-3-small"


def get_embeddings():
    """
    Create the OpenAI embedding model.

    The model converts SDAIA document chunks and future user queries
    into numerical vectors for semantic similarity search.
    """
    return OpenAIEmbeddings(
        model=EMBEDDING_MODEL,
    )


def build_vector_store():
    """
    Build the FAISS vector store for the GovernAI RAG pipeline.

    Steps:
    1. Load the SDAIA document chunks.
    2. Create embeddings using OpenAI text-embedding-3-small.
    3. Store vectors, original text, and metadata in FAISS.
    4. Save the FAISS index locally for future retrieval.
    """
    chunks = create_chunks()

    # Separate text from metadata.
    texts = [chunk["text"] for chunk in chunks]
    metadatas = [chunk["metadata"] for chunk in chunks]

    # Initialize the OpenAI embedding model.
    embeddings = get_embeddings()

    # Create embeddings and build the FAISS index.
    vector_store = FAISS.from_texts(
        texts=texts,
        embedding=embeddings,
        metadatas=metadatas,
    )

    # Create the local vector store directory if needed.
    VECTOR_STORE_DIR.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Save the FAISS index locally.
    vector_store.save_local(
        str(VECTOR_STORE_DIR)
    )

    # Print basic indexing information.
    print(f"Embedding model: {EMBEDDING_MODEL}")
    print(f"Indexed chunks: {len(texts)}")
    print(f"Saved to: {VECTOR_STORE_DIR}")

    return vector_store


# Build the vector store when this file is executed directly.
if __name__ == "__main__":
    build_vector_store()