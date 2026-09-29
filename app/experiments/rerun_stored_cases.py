"""Re-run the use cases already stored in the database through the current
pipeline, and compare each result with the report that was stored for it.

The inputs are taken verbatim from `use_cases` - same name, description,
documentation and classification - so the only difference between "before" and
"after" is the code. New rows are created (the real POST /use-cases path);
every id is recorded and removed afterwards by cleanup.

Usage:
    python -m app.experiments.rerun_stored_cases <out.json> [--limit N]
"""

import json
import logging
import sys
import time
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from app import db
from app.api import app
from app.auth import get_current_user
from app.llm_client import active_chat_model, active_provider, get_chat_client, get_client
from app.tools.audit_log import get_audit_log
from app.tools.policy_repository import policy_ids
from app.tools.risk_rules import score_to_level

TRANSIENT = (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)
CALLS = []
CURRENT = {"case": None}


def retry(call, tries=6):
    for attempt in range(tries):
        try:
            return call()
        except TRANSIENT:
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


class Recording:
    def __init__(self, inner):
        outer = self
        self._inner = inner

        class _C:
            def create(self, **kwargs):
                started = time.perf_counter()
                completion = outer._inner.chat.completions.create(**kwargs)
                usage = getattr(completion, "usage", None)
                CALLS.append({
                    "case": CURRENT["case"],
                    "latency_s": round(time.perf_counter() - started, 3),
                    "total_tokens": getattr(usage, "total_tokens", None),
                })
                return completion

        self.chat = SimpleNamespace(completions=_C())


class WarnCounter(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append((CURRENT["case"], record.getMessage()[:120]))


INPUT_FIELDS = ("name", "description", "owner", "data_classification",
                "deployment_context", "autonomy_level", "documentation")


def main() -> None:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "rerun.json"
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    shared = Recording(get_chat_client(get_client))
    import app.agents.base as base_mod
    base_mod.get_chat_client = lambda *a, **k: shared

    warns = WarnCounter()
    for name in ("governai.agent", "governai.agents.policy"):
        logging.getLogger(name).addHandler(warns)

    app.dependency_overrides[get_current_user] = lambda: {"email": "rerun@local"}
    client = TestClient(app, raise_server_exceptions=False)

    # "before": the stored inputs and the reports they produced
    use_cases = {r["id"]: r for r in retry(lambda: db.select("use_cases", {"select": "*"}))}
    stored = retry(lambda: db.select("governance_reports", {"select": "*", "order": "created_at.asc"}))
    baseline = [(use_cases[r["use_case_id"]], r) for r in stored if r["use_case_id"] in use_cases]
    if limit:
        baseline = baseline[:limit]
    print(f"re-running {len(baseline)} stored cases with provider={active_provider()} "
          f"model={active_chat_model()}", flush=True)

    known = policy_ids()
    results, created = [], []

    for original, before in baseline:
        payload = {f: original.get(f) for f in INPUT_FIELDS if original.get(f) is not None}
        CURRENT["case"] = payload["name"][:28]
        started = time.perf_counter()
        calls_before = len(CALLS)
        record = {"name": payload["name"], "problems": [],
                  "before": {
                      "risk": f"{before['risk_level']} {before['risk_score']}",
                      "compliance": before["compliance_status"],
                      "violated": len(before.get("violated_policies") or []),
                      "satisfied": len(before.get("satisfied_policies") or []),
                      "decision": before["decision"],
                      "status": before["status"],
                  }}

        response = client.post("/use-cases", json=payload)
        record["status_code"] = response.status_code
        record["elapsed_s"] = round(time.perf_counter() - started, 1)

        if response.status_code != 200:
            record["error"] = response.text[:250]
            results.append(record)
            print(f"  {payload['name'][:34]:34} HTTP {response.status_code}", flush=True)
            continue

        body = response.json()
        created.append(body["use_case"]["id"])
        risk, comp, dec = body["risk_assessment"], body["policy_compliance"], body["decision"]
        log = retry(lambda: get_audit_log(body["use_case"]["id"]))
        cited = (comp["violated_policies"] + comp["satisfied_policies"]
                 + comp.get("undetermined_policies", []) + comp.get("not_applicable_policies", []))
        fabricated = [i for i in cited if i not in known]

        if score_to_level(risk["risk_score"]) != risk["risk_level"]:
            record["problems"].append(f"risk_level/{risk['risk_score']} mismatch")
        if comp["violated_policies"] and comp["status"] != "non_compliant":
            record["problems"].append(f"status {comp['status']} with violations")
        if not comp["violated_policies"] and comp["status"] == "non_compliant":
            record["problems"].append("non_compliant with zero violated policies")
        if fabricated:
            record["problems"].append(f"fabricated ids: {fabricated}")
        seen = set()
        for i in cited:
            if i in seen:
                record["problems"].append(f"{i} in two determination lists")
            seen.add(i)
        if dec["decision"] == "approve" and comp["status"] == "non_compliant":
            record["problems"].append("approve while non_compliant")

        case_calls = CALLS[calls_before:]
        record["after"] = {
            "risk": f"{risk['risk_level']} {risk['risk_score']}",
            "compliance": comp["status"],
            "violated": len(comp["violated_policies"]),
            "satisfied": len(comp["satisfied_policies"]),
            "undetermined": len(comp.get("undetermined_policies", [])),
            "not_applicable": len(comp.get("not_applicable_policies", [])),
            "decision": dec["decision"],
            "status": body["status"],
            "conditions": len(dec["conditions"]),
            "reviews": [e["data"].get("verdict") for e in log if e["stage"] == "review"],
            "use_case_id": body["use_case"]["id"],
            "llm_calls": len(case_calls),
            "tokens": sum(c["total_tokens"] or 0 for c in case_calls),
        }
        b, a = record["before"], record["after"]
        print(f"  {payload['name'][:34]:34} {b['compliance']:16}->{a['compliance']:20} "
              f"viol {b['violated']:>3}->{a['violated']:<3} undet {a['undetermined']:<3} "
              f"{b['decision']:22}->{a['decision']:22} {record['elapsed_s']}s", flush=True)
        results.append(record)

    ok = [r for r in results if r["status_code"] == 200]
    dist_before = {}
    dist_after = {}
    for r in ok:
        dist_before[r["before"]["compliance"]] = dist_before.get(r["before"]["compliance"], 0) + 1
        dist_after[r["after"]["compliance"]] = dist_after.get(r["after"]["compliance"], 0) + 1

    summary = {
        "provider": active_provider(),
        "model": active_chat_model(),
        "cases": len(results),
        "http_200": len(ok),
        "failures": [{"name": r["name"], "code": r["status_code"], "error": r.get("error")}
                     for r in results if r["status_code"] != 200],
        "compliance_distribution_before": dist_before,
        "compliance_distribution_after": dist_after,
        "violated_total_before": sum(r["before"]["violated"] for r in ok),
        "violated_total_after": sum(r["after"]["violated"] for r in ok),
        "violated_max_before": max((r["before"]["violated"] for r in ok), default=0),
        "violated_max_after": max((r["after"]["violated"] for r in ok), default=0),
        "undetermined_total_after": sum(r["after"]["undetermined"] for r in ok),
        "not_applicable_total_after": sum(r["after"]["not_applicable"] for r in ok),
        "partially_compliant_before": dist_before.get("partially_compliant", 0),
        "partially_compliant_after": dist_after.get("partially_compliant", 0),
        "decision_changes": [{"name": r["name"], "from": r["before"]["decision"], "to": r["after"]["decision"]}
                             for r in ok if r["before"]["decision"] != r["after"]["decision"]],
        "reviews_approved_after": sum(1 for r in ok if "approved" in (r["after"]["reviews"] or [])),
        "reviews_total_after": sum(len(r["after"]["reviews"] or []) for r in ok),
        "invariant_problems": sum(len(r["problems"]) for r in results),
        "problem_detail": [{"name": r["name"], "problems": r["problems"]} for r in results if r["problems"]],
        "output_repairs": [m for m in warns.messages if "output_repair" in m[1]],
        "llm_calls": len(CALLS),
        "tokens": sum(c["total_tokens"] or 0 for c in CALLS),
        "model_latency_s": round(sum(c["latency_s"] for c in CALLS), 1),
        "wall_clock_s": round(sum(r["elapsed_s"] for r in results), 1),
        "avg_case_s_after": round(sum(r["elapsed_s"] for r in ok) / max(len(ok), 1), 1),
        "created_use_case_ids": created,
    }
    json.dump({"summary": summary, "cases": results}, open(out_path, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    print()
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("created_use_case_ids", "problem_detail", "output_repairs")},
                     indent=2))
    print(f"\nwritten to {out_path}")


if __name__ == "__main__":
    main()
