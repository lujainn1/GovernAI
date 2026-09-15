from pathlib import Path
from pypdf import PdfReader


SDAIA_DIR = Path("data/knowledge/sdaia")


# Metadata catalog for the official SDAIA documents
SOURCE_CATALOG = {
    "ai-principles.pdf": {
        "title": "AI Ethics Principles",
        "domain": "ai_ethics",
        "audience": "all",
        "document_type": "principles",
        "year": 2025,
    },
    "AIAdoptionFramework.pdf": {
        "title": "AI Adoption Framework",
        "domain": "ai_governance",
        "audience": "organizations",
        "document_type": "framework",
        "year": 2025,
    },
    "AIPrinciplesMedia.pdf": {
        "title": "AI Principles in Media",
        "domain": "media_ai",
        "audience": "media",
        "document_type": "principles",
        "year": 2026,
    },
    "GenAIGuidelinesForGovernmentENCompressed.pdf": {
        "title": "Generative AI Guidelines for Government",
        "domain": "generative_ai",
        "audience": "government",
        "document_type": "guidelines",
        "year": 2025,
    },
    "GenerativeAIPublicEN.pdf": {
        "title": "Generative AI Guidelines for Public",
        "domain": "generative_ai",
        "audience": "public",
        "document_type": "guidelines",
        "year": 2025,
    },
    "GuidelinesOnDeepfakeEthics.pdf": {
        "title": "Guidelines on Deepfake Ethics",
        "domain": "deepfake_ethics",
        "audience": "all",
        "document_type": "guidelines",
        "year": 2025,
    },
    "NAII.pdf": {
        "title": "National AI Index",
        "domain": "ai_governance",
        "audience": "organizations",
        "document_type": "index",
        "year": 2025,
    },
    "NationalOccupationalStandardFramework.pdf": {
        "title": "National Occupational Standard Framework for Data & Artificial Intelligence",
        "domain": "ai_workforce",
        "audience": "professionals",
        "document_type": "framework",
        "year": 2025,
    },
    "SaudiAcademicFrameworkAIQualifications.pdf": {
        "title": "Saudi Academic Framework for AI Qualifications (Education Intelligence)",
        "domain": "ai_education",
        "audience": "education",
        "document_type": "framework",
        "year": 2025,
    },
}


def load_all_sdaia_pdfs():
    documents = []

    for pdf_path in sorted(SDAIA_DIR.glob("*.pdf")):
        reader = PdfReader(pdf_path)
        source_metadata = SOURCE_CATALOG.get(pdf_path.name, {})

        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""

            documents.append(
                {
                    "text": text.strip(),
                    "metadata": {
                        "source": pdf_path.name,
                        "file_name": pdf_path.name,
                        "title": source_metadata.get("title", pdf_path.stem),
                        "authority": "SDAIA",
                        "domain": source_metadata.get("domain", "unknown"),
                        "audience": source_metadata.get("audience", "all"),
                        "document_type": source_metadata.get(
                            "document_type", "unknown"
                        ),
                        "year": source_metadata.get("year"),
                        "page": page_number,
                        "total_pages": len(reader.pages),
                        "access_level": "public",
                    },
                }
            )

    return documents


if __name__ == "__main__":
    docs = load_all_sdaia_pdfs()

    print(f"Loaded pages: {len(docs)}")
    print(f"Loaded files: {len(set(d['metadata']['file_name'] for d in docs))}")

    for file_name in sorted(set(d["metadata"]["file_name"] for d in docs)):
        pages = [d for d in docs if d["metadata"]["file_name"] == file_name]
        metadata = pages[0]["metadata"]

        print(
            f"{metadata['title']} | "
            f"{metadata['domain']} | "
            f"{metadata['audience']} | "
            f"{len(pages)} pages"
        )