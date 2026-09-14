from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.document_loader import load_all_sdaia_pdfs


CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
MIN_CHUNK_SIZE = 100


text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],
    length_function=len,
)


def merge_small_chunks(chunks, min_size=MIN_CHUNK_SIZE):
    if len(chunks) <= 1:
        return chunks

    merged = []
    i = 0

    while i < len(chunks):
        current = chunks[i].strip()

        if len(current) < min_size:
            # If possible, attach a small chunk to the next chunk
            if i + 1 < len(chunks):
                next_chunk = chunks[i + 1].strip()
                combined = f"{current}\n{next_chunk}".strip()
                merged.append(combined)
                i += 2
                continue

            # Otherwise attach it to the previous chunk
            if merged:
                merged[-1] = f"{merged[-1]}\n{current}".strip()
                i += 1
                continue

        merged.append(current)
        i += 1

    return merged


def create_chunks():
    documents = load_all_sdaia_pdfs()
    all_chunks = []

    for document in documents:
        text = document["text"]
        metadata = document["metadata"]

        if not text.strip():
            continue

        page_chunks = text_splitter.split_text(text)
        page_chunks = merge_small_chunks(page_chunks)

        for chunk_index, chunk in enumerate(page_chunks, start=1):
            if not chunk.strip():
                continue

            all_chunks.append(
                {
                    "text": chunk.strip(),
                    "metadata": {
                        **metadata,
                        "chunk_index": chunk_index,
                    },
                }
            )

    return all_chunks


if __name__ == "__main__":
    chunks = create_chunks()

    lengths = [len(chunk["text"]) for chunk in chunks]

    print(f"Total chunks: {len(chunks)}")
    print(f"Empty chunks: {sum(length == 0 for length in lengths)}")
    print(f"Under 100: {sum(length < 100 for length in lengths)}")
    print(f"Min length: {min(lengths)}")
    print(f"Average length: {round(sum(lengths) / len(lengths))}")
    print(f"Max length: {max(lengths)}")

    files = sorted(
        set(chunk["metadata"]["file_name"] for chunk in chunks)
    )

    for file_name in files:
        file_chunks = [
            chunk
            for chunk in chunks
            if chunk["metadata"]["file_name"] == file_name
        ]

        print(f"{file_name}: {len(file_chunks)} chunks")