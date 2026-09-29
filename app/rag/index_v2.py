"""Build the improved SDAIA index into its OWN directory.

The accepted index (data/vectorstore/sdaia_faiss, built by
app/rag/vector_store.py) is never touched: this writes to
data/vectorstore/sdaia_faiss_v2 so both can be retrieved from and compared.

Pipeline, in order:

    load pages -> repair extraction damage -> chunk -> drop redundant chunks
                -> embed -> save

Only the first and third steps are new. Chunking and the embedding model are
unchanged, so a difference between the two indexes is attributable to the
cleaning and de-duplication rather than to a different representation.

    python -m app.rag.index_v2
"""

from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv
from langchain_community.vectorstores import FAISS

from app.rag.chunker import merge_small_chunks, text_splitter
from app.rag.dedupe import deduplicate
from app.rag.document_loader import load_all_sdaia_pdfs
from app.rag.text_repair import clean_documents
from app.rag.vector_store import EMBEDDING_MODEL, get_embeddings

load_dotenv()

VECTOR_STORE_V2_DIR = Path("data/vectorstore/sdaia_faiss_v2")


def build_chunks_v2() -> Tuple[List[dict], Dict[str, object]]:
    """Cleaned, chunked and de-duplicated chunks, with the stats behind them."""
    pages = load_all_sdaia_pdfs()
    cleaned_pages, repair_stats = clean_documents(pages)

    chunks: List[dict] = []
    for page in cleaned_pages:
        text = page["text"]
        if not text.strip():
            continue

        page_chunks = merge_small_chunks(text_splitter.split_text(text))
        for chunk_index, chunk in enumerate(page_chunks, start=1):
            if not chunk.strip():
                continue
            chunks.append(
                {
                    "text": chunk.strip(),
                    "metadata": {**page["metadata"], "chunk_index": chunk_index},
                }
            )

    kept, dedupe_stats = deduplicate(chunks)
    return kept, {"repair": repair_stats, "dedupe": dedupe_stats}


def build_vector_store_v2() -> FAISS:
    chunks, stats = build_chunks_v2()

    store = FAISS.from_texts(
        texts=[chunk["text"] for chunk in chunks],
        embedding=get_embeddings(),
        metadatas=[chunk["metadata"] for chunk in chunks],
    )

    VECTOR_STORE_V2_DIR.parent.mkdir(parents=True, exist_ok=True)
    store.save_local(str(VECTOR_STORE_V2_DIR))

    repair = stats["repair"]
    dedupe = stats["dedupe"]
    print(f"embedding model         : {EMBEDDING_MODEL}")
    print(f"pages loaded            : {repair['pages']}")
    print(f"ligature tokens repaired: {repair['ligatures_fixed']}")
    print(f"answer cells removed    : {repair['answer_cells_removed']}")
    print(f"repeated lines removed  : {repair['repeated_lines_removed']}")
    print(f"unresolved damaged toks : {dict(repair['unresolved_tokens'])}")
    print(f"chunks before dedupe    : {dedupe['input_chunks']}")
    print(f"  exact duplicates      : -{dedupe['exact_duplicates_removed']}")
    print(f"  wholly contained      : -{dedupe['contained_chunks_removed']}")
    print(f"chunks indexed          : {dedupe['kept_chunks']}")
    print(f"removed by file         : {dedupe['removed_by_file']}")
    print(f"saved to                : {VECTOR_STORE_V2_DIR}")
    return store


if __name__ == "__main__":
    build_vector_store_v2()
