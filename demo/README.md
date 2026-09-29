# Demo assets

Everything needed to run the GovernAI live demo: the use cases to submit, the
documentation files to upload, and what each one is supposed to prove.

```
demo/
  seed/   8 use cases for pre-seeding history via the CLI (documentation inline)
  docs/   documentation files to upload in the UI during the live acts
```

The submit form has no free-text documentation field — in the UI,
`documentation` comes only from uploaded files (`.pdf .docx .pptx .xlsx .md
.txt`, max 25 MB). That is why the live acts upload from `docs/` while the
seed payloads carry their documentation as a string.

## Before the audience

```powershell
pytest                                     # mocked; no API key needed
python main.py                             # backend on :8000
curl http://localhost:8000/health          # -> {"status":"ok"}
cd frontend; npm run dev                   # :5173, then open it once to warm the bundles
```

Pre-seed history so Overview and Monitoring have something to show (the CLI
runs the orchestrator directly — no bearer token needed):

```powershell
New-Item -ItemType Directory -Force demo\seed-output | Out-Null
Get-ChildItem demo\seed\*.json | ForEach-Object {
  python -m app.cli submit $_.FullName |
    Out-File -Encoding utf8 "demo\seed-output\$($_.BaseName).result.json"
}
python -m app.cli list
```

Then record a human rejection on the seeded **Automated Loan Decision Agent**
(`03`) so agent memory holds a human verdict for Act 6:

```powershell
python -m app.cli approve <id> --approver you@example.com --reject --notes "no appeal process, no bias testing"
```

Expected spread from the 8 seeds: `01`/`07` approve, `02` pending human
approval (human-in-the-loop always ends at the human step), `03`/`04`/`06`
block or require human approval, `05`/`08` require human approval.

## Live acts

| # | Use case | Autonomy | Upload | Proves | Expected |
|---|---|---|---|---|---|
| 1 | — | — | — | Overview + System health card | populated by the seeds |
| 2 | Internal Meeting Notes Summarizer | human-on-the-loop | `docs/summarizer-model-card.md` | it does not over-block | low–medium · **approve** · completed |
| 3 | Automated Loan Decision Agent | fully-autonomous | `docs/loan-agent-design-notes.md` | the flagship block | high–critical · POL-003, POL-006 · block |
| 4 | Autonomous Database Maintenance Agent | fully-autonomous | `docs/db-maintenance-design-notes.md` | prompt injection ignored | high–critical · POL-007 · block |
| 5 | Clinical Notes Diagnosis Assistant | **human-in-the-loop** | `docs/clinical-assistant-dpia.md` | step-by-step approval + Review Agent | POL-004 · pending human approval |
| 6 | Instant Consumer Credit Decisioning Bot | fully-autonomous | none | memory recalls Act 3 as precedent | `memory_retrieval` in the audit log |
| 7 | Arabic Voice Assistant for Outbound Collections | human-on-the-loop | none | a policy you add mid-demo is enforced | POL-200, POL-005 violated |
| 8 | — | — | — | Monitoring, audit log, global search | HEALTHY + per-agent cost/latency |

Field values for each act are in the plan:
`~/.claude/plans/give-me-use-cases-shimmering-sunset.md`.

Autonomy level decides the flow: `human-in-the-loop` runs one agent per click
(approve/reject each step, resumable from **Pipeline runs**); anything else
runs the whole pipeline in one request, ~30–90 s. A human-in-the-loop case can
never end in a plain `approve` — it is escalated to `require_human_approval`,
so Act 2 deliberately uses `human-on-the-loop`.

Before Act 7, add the policy the case is meant to trip: **Policies → Add
governance policy** → `POL-200`, Voice Cloning Consent, `transparency`,
minimum risk `medium`.

## Verification

```powershell
python -m app.cli show <use_case_id>       # risk / policy / decision / conditions
python -m app.cli audit-log <use_case_id>  # every stage, append-only
```

Clean full run: `intake` → (`memory_retrieval`) → `risk_assessment` →
`policy_compliance` → `decision` → `review` → (`decision_revision` →
`review`)* → (`review_escalation` | `human_in_the_loop_escalation`) →
(`coordination_diagnosis`) → `report_finalized` → (`human_approval`).
Step-by-step runs add a `step_approval` per verdict.
