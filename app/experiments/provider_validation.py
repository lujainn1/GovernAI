"""Validate the complete Preview system against one chat provider.

Runs realistic submissions through the REAL latest pipeline - the same
`POST /use-cases` the frontend calls - and records what the system did:
risk, policy, decision, review, pipeline/audit stages, observability agent-run
rows, database writes, latency, retries and token usage.

The provider comes from GOVERNAI_LLM_PROVIDER (openai by default), so the GPT
and DeepSeek arms run identical code, inputs, prompts, schemas, tools and
decision semantics - only the chat client differs. Nothing here changes any
production behaviour; it only observes.

Rows written during validation are real. Every use case id created is
recorded, and cleanup.py removes exactly those rows afterwards.

Usage:
    python -m app.experiments.provider_validation <out.json> [--cases N]
"""

import json
import logging
import sys
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import config, db
from app.api import app
from app.auth import get_current_user
from app.llm_client import active_chat_model, active_provider, get_chat_client, get_client
from app.rag.evidence import build_evidence_query, retrieve_evidence
from app.tools.audit_log import get_audit_log
from app.tools.policy_repository import get_policies
from app.tools.risk_rules import score_to_level

CALLS = []
CURRENT = {"case": None}


class RecordingClient:
    """Wraps whichever provider client is active and records every call."""

    def __init__(self, inner):
        outer = self
        self._inner = inner

        class _Completions:
            def create(self, **kwargs):
                started = time.perf_counter()
                completion = outer._inner.chat.completions.create(**kwargs)
                elapsed = time.perf_counter() - started
                choice = completion.choices[0]
                usage = getattr(completion, "usage", None)
                dumped = choice.message.model_dump()
                CALLS.append({
                    "case": CURRENT["case"],
                    "latency_s": round(elapsed, 3),
                    "model": getattr(completion, "model", None),
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", None),
                    "finish_reason": choice.finish_reason,
                    "tool_calls": len(getattr(choice.message, "tool_calls", None) or []),
                    "reasoning_chars": len(dumped.get("reasoning_content") or ""),
                    "content_empty": not (choice.message.content or "").strip(),
                })
                return completion

        self.chat = SimpleNamespace(completions=_Completions())


class RetryCounter(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage()[:200])


CASES = [
 ("benign internal, public data", dict(
    name="PV-1 Internal IT FAQ Assistant",
    description="Answers staff questions about printer setup and room booking from our public internal wiki. Information only, no actions.",
    owner="it-support", data_classification="public", deployment_context="internal-tool",
    autonomy_level="human-in-the-loop",
    documentation="No personal data is processed. Traffic uses TLS and the store is encrypted at rest. Every answer is written to the audit log. Staff can escalate to the service desk, and the tool states answers come from an AI assistant.")),
 ("critical autonomous consequential", dict(
    name="PV-2 Autonomous Loan Decision Engine",
    description="Approves or denies consumer loans and notifies the applicant directly. No human review step.",
    owner="lending", data_classification="confidential", deployment_context="customer-facing",
    autonomy_level="fully-autonomous",
    documentation="Inputs are income, national ID, credit history and employment records. The output is final and emailed automatically without a reason code. No bias or disparate-impact testing has been carried out.")),
 ("cross-border personal data", dict(
    name="PV-3 Regional Customer Analytics",
    description="Analyses customer support transcripts to find recurring complaints, using a processor whose servers are outside the Kingdom.",
    owner="customer-experience", data_classification="confidential", deployment_context="internal-tool",
    autonomy_level="human-on-the-loop",
    documentation="Transcripts contain customer names and phone numbers and are sent to a processor hosting data outside Saudi Arabia. No transfer risk assessment has been completed and no adequacy decision has been confirmed.")),
]


def table_count(table, key):
    try:
        return len(db.select(table, {"select": key}))
    except Exception:
        return None


def main() -> None:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "provider_validation.json"
    limit = int(sys.argv[sys.argv.index("--cases") + 1]) if "--cases" in sys.argv else len(CASES)
    cases = CASES[:limit]
    if "--only" in sys.argv:
        wanted = sys.argv[sys.argv.index("--only") + 1]
        cases = [c for c in CASES if c[1]["name"].startswith(wanted)]

    provider = active_provider()
    model = active_chat_model()
    print(f"=== provider: {provider} | model: {model} ===", flush=True)

    # Record every call, whichever provider is active, without changing routing.
    shared = RecordingClient(get_chat_client(get_client))
    import app.agents.base as base_mod
    base_mod.get_chat_client = lambda *a, **k: shared

    retries = RetryCounter()
    logging.getLogger("governai.agent").addHandler(retries)

    app.dependency_overrides[get_current_user] = lambda: {"email": "provider-validation@local"}
    client = TestClient(app, raise_server_exceptions=False)

    before = {t: table_count(t, k) for t, k in
              (("use_cases", "id"), ("governance_reports", "use_case_id"),
               ("audit_log", "id"), ("agent_runs", "id"), ("pipeline_runs", "use_case_id"),
               ("agent_memory", "use_case_id"))}
    print("db before:", before, flush=True)

    known_ids = {p["id"] for p in get_policies()}
    results, created = [], []

    # --- API surface checks ------------------------------------------------
    api_checks = {
        "health": client.get("/health").status_code,
        "policies": client.get("/policies").status_code,
        "risk_rules": client.get("/risk-rules").status_code,
        "metrics": client.get("/metrics").status_code,
        "metrics_health_report": client.get("/metrics/health-report").status_code,
        "pipeline_runs": client.get("/pipeline-runs").status_code,
        "use_cases_list": client.get("/use-cases").status_code,
        "audit_log": client.get("/audit-log").status_code,
        "unknown_use_case": client.get("/use-cases/does-not-exist").status_code,
    }
    print("api checks:", api_checks, flush=True)

    for label, payload in cases:
        short = payload["name"].split()[0]
        CURRENT["case"] = short
        started = time.perf_counter()
        calls_before = len(CALLS)
        record = {"label": label, "name": payload["name"], "short": short, "problems": []}

        response = client.post("/use-cases", json=payload)
        record["status_code"] = response.status_code
        record["elapsed_s"] = round(time.perf_counter() - started, 2)

        if response.status_code != 200:
            record["error"] = response.text[:400]
            results.append(record)
            print(f"{short}: HTTP {response.status_code} {response.text[:120]}", flush=True)
            continue

        body = response.json()
        use_case_id = body["use_case"]["id"]
        created.append(use_case_id)
        risk, comp, dec = body["risk_assessment"], body["policy_compliance"], body["decision"]
        log = get_audit_log(use_case_id)
        stages = [e["stage"] for e in log]
        reviews = [e["data"].get("verdict") for e in log if e["stage"] == "review"]
        cited = comp["violated_policies"] + comp["satisfied_policies"]
        fabricated = [i for i in cited if i not in known_ids]

        # observability rows for this run
        try:
            agent_runs = db.select("agent_runs", {"use_case_id": f"eq.{use_case_id}", "select": "*"})
        except Exception as exc:  # noqa: BLE001
            agent_runs = []
            record["problems"].append(f"agent_runs query failed: {type(exc).__name__}")

        def flag(msg):
            record["problems"].append(msg)

        if score_to_level(risk["risk_score"]) != risk["risk_level"]:
            flag(f"risk_level {risk['risk_level']} disagrees with score {risk['risk_score']}")
        if comp["violated_policies"] and comp["status"] == "compliant":
            flag("status compliant with violated policies")
        if fabricated:
            flag(f"fabricated policy ids survived: {fabricated}")
        if dec["decision"] == "approve" and comp["status"] == "non_compliant":
            flag("approve while non_compliant")
        for required in ("intake", "risk_assessment", "policy_compliance", "decision", "review"):
            if required not in stages:
                flag(f"audit trail missing {required}")
        if not agent_runs:
            flag("no agent_runs observability rows recorded")

        case_calls = CALLS[calls_before:]
        prompts = [c["prompt_tokens"] for c in case_calls if c["prompt_tokens"]]
        # Independently reproduce the retrieval for this submission so the
        # report can show the full Top-5 and the first retrieved page. This is
        # the same query the Policy Agent built (one extra embedding call).
        try:
            from app.models import AIUseCase as _UC
            retrieved = retrieve_evidence(build_evidence_query(_UC(**payload)))
        except Exception as exc:  # noqa: BLE001
            retrieved = []
            record["problems"].append(f"retrieval probe failed: {type(exc).__name__}")
        cited_ids = comp.get("evidence_ids") or []
        citations = comp.get("evidence") or []
        record.update({
            "rag": {
                "retrieved": len(retrieved),
                "top5": [f"{c['source']} p{c['page']}" for c in retrieved],
                "first_page": (f"{retrieved[0]['source']} p{retrieved[0]['page']}" if retrieved else None),
                "evidence_ids_returned": cited_ids,
                "citations": [{"id": c["evidence_id"], "source": c["source"], "page": c["page"],
                               "quote": c["quote"][:120]} for c in citations],
                "fabricated_citations": [c["evidence_id"] for c in citations
                                         if c["evidence_id"] not in {r["evidence_id"] for r in retrieved}],
            },
            "use_case_id": use_case_id,
            "risk": f"{risk['risk_level']} {risk['risk_score']}",
            "risk_factors": len(risk["risk_factors"]),
            "suggested_new_rules": len(risk.get("suggested_new_rules") or []),
            "compliance": comp["status"],
            "violated": comp["violated_policies"],
            "satisfied": comp["satisfied_policies"],
            "fabricated_ids": fabricated,
            "decision": dec["decision"],
            "conditions": len(dec["conditions"]),
            "report_status": body["status"],
            "review_verdicts": reviews,
            "audit_stages": stages,
            "agent_runs_recorded": len(agent_runs),
            "agent_run_agents": sorted({r.get("agent") for r in agent_runs if r.get("agent")}),
            "llm": {
                "calls": len(case_calls),
                "max_prompt_tokens": max(prompts) if prompts else 0,
                "total_tokens": sum(c["total_tokens"] or 0 for c in case_calls),
                "reasoning_calls": sum(1 for c in case_calls if c["reasoning_chars"]),
                "model": case_calls[0]["model"] if case_calls else None,
            },
        })
        results.append(record)
        print(f"{short}: HTTP 200 {record['risk']} / {comp['status']} -> {dec['decision']} "
              f"| agent_runs {len(agent_runs)} | problems {len(record['problems'])} "
              f"| {record['elapsed_s']}s", flush=True)

    after = {t: table_count(t, k) for t, k in
             (("use_cases", "id"), ("governance_reports", "use_case_id"),
              ("audit_log", "id"), ("agent_runs", "id"), ("pipeline_runs", "use_case_id"),
              ("agent_memory", "use_case_id"))}

    succeeded = [r for r in results if r["status_code"] == 200]
    all_prompts = [c["prompt_tokens"] for c in CALLS if c["prompt_tokens"]]
    summary = {
        "provider": provider,
        "model": model,
        "base_url": str(get_chat_client(get_client).base_url),
        "cases": len(cases),
        "http_200": len(succeeded),
        "problems_total": sum(len(r["problems"]) for r in results),
        "fabricated_ids_total": sum(len(r.get("fabricated_ids", [])) for r in succeeded),
        "repair_retries": sum(1 for m in retries.records if "requesting one correction" in m),
        "warnings": retries.records[:10],
        "api_checks": api_checks,
        "llm_calls": len(CALLS),
        "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in CALLS),
        "completion_tokens": sum(c["completion_tokens"] or 0 for c in CALLS),
        "total_tokens": sum(c["total_tokens"] or 0 for c in CALLS),
        "max_prompt_tokens": max(all_prompts) if all_prompts else 0,
        "calls_with_reasoning": sum(1 for c in CALLS if c["reasoning_chars"]),
        "empty_final_answers": sum(1 for c in CALLS if c["content_empty"] and c["tool_calls"] == 0),
        "model_latency_s": round(sum(c["latency_s"] for c in CALLS), 1),
        "wall_clock_s": round(sum(r["elapsed_s"] for r in results), 1),
        "avg_latency_per_call_s": round(sum(c["latency_s"] for c in CALLS) / max(len(CALLS), 1), 2),
        "db_before": before,
        "db_after": after,
        "db_delta": {k: (after[k] - before[k]) if after[k] is not None and before[k] is not None else None
                     for k in before},
        "created_use_case_ids": created,
        "rag_cases_with_retrieval": sum(1 for r in succeeded if (r.get("rag") or {}).get("retrieved")),
        "rag_cases_that_cited": sum(1 for r in succeeded if (r.get("rag") or {}).get("evidence_ids_returned")),
        "rag_citations_total": sum(len((r.get("rag") or {}).get("citations") or []) for r in succeeded),
        "rag_fabricated_citations": sum(len((r.get("rag") or {}).get("fabricated_citations") or []) for r in succeeded),
    }
    json.dump({"summary": summary, "cases": results, "calls": CALLS},
              open(out_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print()
    print(json.dumps({k: v for k, v in summary.items() if k not in ("warnings",)}, indent=2))
    print(f"\nwritten to {out_path}")


if __name__ == "__main__":
    main()
