"""
GovernAI RAG Retrieval Evaluation

This module evaluates the quality of the GovernAI RAG retriever
against a manually defined ground-truth dataset.

It measures:
- Hit@1
- Hit@3
- Hit@5
- Mean Reciprocal Rank (MRR)
- Domain Accuracy@5

The script also:
- Displays a clear terminal benchmark report.
- Shows per-query retrieval success.
- Highlights failed Top-5 queries.
- Saves detailed JSON, CSV, and summary files for later analysis.
"""

import csv
import json
from pathlib import Path

from app.rag.retriever import retrieve_policy_evidence
from app.rag.vector_store import EMBEDDING_MODEL


# Ground-truth evaluation dataset.
GROUND_TRUTH_PATH = Path(
    "data/evaluation/datasets/rag_ground_truth.json"
)

# Directory used to store evaluation artifacts.
RESULTS_DIR = Path(
    "data/evaluation/results"
)

DETAILED_JSON_PATH = RESULTS_DIR / "rag_retrieval_results.json"
CSV_PATH = RESULTS_DIR / "rag_retrieval_results.csv"
SUMMARY_JSON_PATH = RESULTS_DIR / "rag_retrieval_summary.json"


def load_ground_truth():
    """
    Load the manually prepared RAG evaluation dataset.
    """
    with open(
        GROUND_TRUTH_PATH,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def find_source_rank(results, expected_source):
    """
    Find the first rank where the expected source appears.

    Returns:
        int: 1-based rank if found.
        None: if the expected source is not retrieved.
    """
    for rank, result in enumerate(results, start=1):
        if result["file_name"] == expected_source:
            return rank

    return None


def evaluate_query(test_case):
    """
    Evaluate a single ground-truth query against the RAG retriever.
    """
    query = test_case["query"]
    expected_source = test_case["expected_source"]
    expected_domain = test_case["expected_domain"]

    # Retrieve the top five evidence chunks.
    results = retrieve_policy_evidence(
        query=query,
        k=5,
    )

    # Find the rank of the expected source.
    source_rank = find_source_rank(
        results,
        expected_source,
    )

    # Calculate retrieval metrics for this query.
    hit_at_1 = source_rank == 1
    hit_at_3 = (
        source_rank is not None
        and source_rank <= 3
    )
    hit_at_5 = (
        source_rank is not None
        and source_rank <= 5
    )

    reciprocal_rank = (
        1 / source_rank
        if source_rank is not None
        else 0.0
    )

    # Check whether the expected domain appears in Top-5.
    domain_hit_at_5 = any(
        result["domain"] == expected_domain
        for result in results
    )

    # Store the complete evidence for detailed analysis.
    retrieved_results = []

    for rank, result in enumerate(results, start=1):
        retrieved_results.append(
            {
                "rank": rank,
                "source": result["file_name"],
                "title": result["title"],
                "page": result["page"],
                "domain": result["domain"],
                "audience": result["audience"],
                "authority": result["authority"],
                "distance_score": round(
                    result["distance_score"],
                    6,
                ),
                "evidence": result["text"],
            }
        )

    return {
        "id": test_case["id"],
        "query": query,
        "ground_truth": {
            "expected_source": expected_source,
            "expected_domain": expected_domain,
        },
        "evaluation": {
            "source_rank": source_rank,
            "hit_at_1": hit_at_1,
            "hit_at_3": hit_at_3,
            "hit_at_5": hit_at_5,
            "reciprocal_rank": reciprocal_rank,
            "domain_hit_at_5": domain_hit_at_5,
        },
        "retrieved_results": retrieved_results,
    }


def calculate_summary(evaluation_results):
    """
    Calculate aggregate RAG retrieval metrics.
    """
    total_queries = len(evaluation_results)

    hit_1_count = sum(
        result["evaluation"]["hit_at_1"]
        for result in evaluation_results
    )

    hit_3_count = sum(
        result["evaluation"]["hit_at_3"]
        for result in evaluation_results
    )

    hit_5_count = sum(
        result["evaluation"]["hit_at_5"]
        for result in evaluation_results
    )

    domain_count = sum(
        result["evaluation"]["domain_hit_at_5"]
        for result in evaluation_results
    )

    reciprocal_ranks = [
        result["evaluation"]["reciprocal_rank"]
        for result in evaluation_results
    ]

    mrr = sum(reciprocal_ranks) / total_queries

    return {
        "embedding_model": EMBEDDING_MODEL,
        "queries_evaluated": total_queries,
        "hit_at_1": hit_1_count / total_queries,
        "hit_at_3": hit_3_count / total_queries,
        "hit_at_5": hit_5_count / total_queries,
        "mrr": mrr,
        "domain_accuracy_at_5": domain_count / total_queries,
        "hit_at_1_count": hit_1_count,
        "hit_at_3_count": hit_3_count,
        "hit_at_5_count": hit_5_count,
        "domain_count": domain_count,
        "top_1_errors": total_queries - hit_1_count,
        "top_5_misses": total_queries - hit_5_count,
    }


def save_json_results(
    evaluation_results,
    summary,
):
    """
    Save detailed results and summary as JSON files.
    """
    RESULTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        DETAILED_JSON_PATH,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            evaluation_results,
            file,
            indent=2,
            ensure_ascii=False,
        )

    with open(
        SUMMARY_JSON_PATH,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            summary,
            file,
            indent=2,
            ensure_ascii=False,
        )


def save_csv_results(evaluation_results):
    """
    Save a compact query-level CSV report.
    """
    RESULTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        CSV_PATH,
        "w",
        newline="",
        encoding="utf-8",
    ) as file:

        fieldnames = [
            "id",
            "query",
            "expected_source",
            "expected_domain",
            "source_rank",
            "hit_at_1",
            "hit_at_3",
            "hit_at_5",
            "reciprocal_rank",
            "domain_hit_at_5",
            "top_1_source",
            "top_1_page",
            "top_1_domain",
            "top_1_distance",
        ]

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for result in evaluation_results:
            top_1 = result["retrieved_results"][0]

            writer.writerow(
                {
                    "id": result["id"],
                    "query": result["query"],
                    "expected_source":
                        result["ground_truth"]["expected_source"],
                    "expected_domain":
                        result["ground_truth"]["expected_domain"],
                    "source_rank":
                        result["evaluation"]["source_rank"],
                    "hit_at_1":
                        result["evaluation"]["hit_at_1"],
                    "hit_at_3":
                        result["evaluation"]["hit_at_3"],
                    "hit_at_5":
                        result["evaluation"]["hit_at_5"],
                    "reciprocal_rank":
                        round(
                            result["evaluation"]["reciprocal_rank"],
                            4,
                        ),
                    "domain_hit_at_5":
                        result["evaluation"]["domain_hit_at_5"],
                    "top_1_source":
                        top_1["source"],
                    "top_1_page":
                        top_1["page"],
                    "top_1_domain":
                        top_1["domain"],
                    "top_1_distance":
                        top_1["distance_score"],
                }
            )


def status(value):
    """
    Convert a Boolean evaluation result into a readable label.
    """
    return "PASS" if value else "MISS"


def short_source(source, max_length=34):
    """
    Shorten long source names for cleaner terminal tables.
    """
    if len(source) <= max_length:
        return source

    return source[: max_length - 3] + "..."


def print_report(
    evaluation_results,
    summary,
):
    """
    Display a clean and readable terminal evaluation report.
    """
    width = 78

    print()
    print("=" * width)
    print(
        "GovernAI RAG Evaluation Report".center(width)
    )
    print("=" * width)

    print()
    print(f"Embedding Model : {summary['embedding_model']}")
    print(f"Test Queries    : {summary['queries_evaluated']}")

    print()
    print("RETRIEVAL PERFORMANCE")
    print("-" * width)

    print(
        f"{'Metric':<24}"
        f"{'Score':<16}"
        f"{'Correct':<16}"
    )

    print("-" * width)

    print(
        f"{'Hit@1':<24}"
        f"{summary['hit_at_1'] * 100:>6.2f}%"
        f"{'':<9}"
        f"{summary['hit_at_1_count']} / "
        f"{summary['queries_evaluated']}"
    )

    print(
        f"{'Hit@3':<24}"
        f"{summary['hit_at_3'] * 100:>6.2f}%"
        f"{'':<9}"
        f"{summary['hit_at_3_count']} / "
        f"{summary['queries_evaluated']}"
    )

    print(
        f"{'Hit@5':<24}"
        f"{summary['hit_at_5'] * 100:>6.2f}%"
        f"{'':<9}"
        f"{summary['hit_at_5_count']} / "
        f"{summary['queries_evaluated']}"
    )

    print(
        f"{'MRR':<24}"
        f"{summary['mrr']:>7.4f}"
    )

    print(
        f"{'Domain Accuracy@5':<24}"
        f"{summary['domain_accuracy_at_5'] * 100:>6.2f}%"
        f"{'':<9}"
        f"{summary['domain_count']} / "
        f"{summary['queries_evaluated']}"
    )

    print("-" * width)

    print(
        f"Top-1 Errors : {summary['top_1_errors']}"
    )
    print(
        f"Top-5 Misses : {summary['top_5_misses']}"
    )

    print()
    print("QUERY RESULTS")
    print("-" * width)

    print(
        f"{'ID':<7}"
        f"{'Top-1':<10}"
        f"{'Top-3':<10}"
        f"{'Top-5':<10}"
        f"{'Expected Source'}"
    )

    print("-" * width)

    for result in evaluation_results:
        evaluation = result["evaluation"]

        print(
            f"{result['id']:<7}"
            f"{status(evaluation['hit_at_1']):<10}"
            f"{status(evaluation['hit_at_3']):<10}"
            f"{status(evaluation['hit_at_5']):<10}"
            f"{short_source(result['ground_truth']['expected_source'])}"
        )

    print("-" * width)

    # Display only serious failures where the correct source
    # was not found anywhere in the Top-5 results.
    top_5_misses = [
        result
        for result in evaluation_results
        if not result["evaluation"]["hit_at_5"]
    ]

    if top_5_misses:
        print()
        print("FAILED TOP-5 QUERIES")
        print("-" * width)

        for result in top_5_misses:
            top_result = result["retrieved_results"][0]

            print()
            print(f"{result['id']} | {result['query']}")
            print(
                "Expected : "
                f"{result['ground_truth']['expected_source']}"
            )
            print(
                "Retrieved: "
                f"{top_result['source']}"
            )
            print(
                "Page     : "
                f"{top_result['page']}"
            )
            print(
                "Distance : "
                f"{top_result['distance_score']:.5f}"
            )

            print("-" * width)

    print()
    print("EVALUATION FILES")
    print("-" * width)
    print(f"Detailed JSON : {DETAILED_JSON_PATH}")
    print(f"Query CSV     : {CSV_PATH}")
    print(f"Summary JSON  : {SUMMARY_JSON_PATH}")

    print("=" * width)
    print()


def main():
    """
    Run the complete GovernAI RAG retrieval evaluation.
    """
    ground_truth = load_ground_truth()

    evaluation_results = []

    for test_case in ground_truth:
        result = evaluate_query(test_case)
        evaluation_results.append(result)

    summary = calculate_summary(
        evaluation_results
    )

    save_json_results(
        evaluation_results,
        summary,
    )

    save_csv_results(
        evaluation_results
    )

    print_report(
        evaluation_results,
        summary,
    )


if __name__ == "__main__":
    main()