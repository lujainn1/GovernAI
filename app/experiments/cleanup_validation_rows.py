"""Delete exactly the rows the provider validation created.

Matches on the PV- name prefix, deletes children before parents, one explicit
id per request, and verifies afterwards that every pre-existing row is still
present. Never uses a broad filter.

Preview writes more per run than the older schema did, so all of these are
cleaned: audit_log, agent_runs, agent_memory, pipeline_runs,
governance_reports, use_cases.

Usage:
    python -m app.experiments.cleanup_validation_rows          # dry run
    python -m app.experiments.cleanup_validation_rows --apply
"""

import sys
import time

import httpx

from app import db

PREFIX = "PV-"
CHILD_TABLES = (
    ("audit_log", "id"),
    ("agent_runs", "id"),
    ("agent_memory", "use_case_id"),
    ("pipeline_runs", "use_case_id"),
    ("governance_reports", "use_case_id"),
)
TRANSIENT = (httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout)


def retry(call, tries=8):
    for attempt in range(tries):
        try:
            return call()
        except TRANSIENT:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def counts():
    out = {}
    for table, key in (("use_cases", "id"), ("governance_reports", "use_case_id"),
                       ("audit_log", "id"), ("agent_runs", "id"),
                       ("pipeline_runs", "use_case_id"), ("agent_memory", "use_case_id")):
        try:
            out[table] = len(retry(lambda: db.select(table, {"select": key})))
        except Exception:  # noqa: BLE001
            out[table] = None
    return out


def delete(url):
    def once():
        with httpx.Client(timeout=30) as client:
            client.delete(url, headers=db._headers()).raise_for_status()
    retry(once)


def main() -> None:
    apply = "--apply" in sys.argv
    rows = retry(lambda: db.select("use_cases", {"select": "id,name"}))
    mine = [r for r in rows if r["name"].startswith(PREFIX)]
    keep = {r["id"] for r in rows} - {r["id"] for r in mine}

    print("before:", counts())
    print(f"validation rows to delete: {len(mine)}")
    for row in sorted(mine, key=lambda r: r["name"]):
        print(f"  {row['id']}  {row['name'][:46]}")
    print(f"pre-existing use cases to preserve: {len(keep)}")

    if not apply:
        print("DRY RUN - nothing deleted")
        return

    ids = [r["id"] for r in mine]
    deleted = {}
    for table, key in CHILD_TABLES:
        n = 0
        for use_case_id in ids:
            try:
                children = retry(lambda: db.select(
                    table, {"use_case_id": f"eq.{use_case_id}", "select": key}))
            except Exception:  # noqa: BLE001 - table may not exist
                continue
            for child in children:
                delete(f"{db._url(table)}?{key}=eq.{child[key]}")
                n += 1
        deleted[table] = n
    for use_case_id in ids:
        delete(f"{db._url('use_cases')}?id=eq.{use_case_id}")
    deleted["use_cases"] = len(ids)

    print("deleted:", deleted)
    print("after :", counts())
    remaining = {r["id"] for r in retry(lambda: db.select("use_cases", {"select": "id"}))}
    print("every pre-existing use case still present:", keep <= remaining, f"({len(keep)} checked)")
    left = [r["name"] for r in retry(lambda: db.select("use_cases", {"select": "id,name"}))
            if r["name"].startswith(PREFIX)]
    print("validation rows left:", left or "none")


if __name__ == "__main__":
    main()
