# GovernAI

A Multi-Agent AI Governance Platform. Organizations adopting AI systems and
AI agents often lack a centralized, automated way to assess risk, check
policy compliance, and govern AI use cases — leading to security, privacy,
compliance, and operational exposure. GovernAI gives AI governance, risk,
compliance, and security teams (and the developers building AI systems) a
repeatable pipeline for reviewing an AI use case before and after it ships.

Three agents, each backed by an LLM via the [OpenAI API](https://platform.openai.com),
collaborate on every submission:

- **Risk Assessment Agent** — evaluates the use case and assigns a risk
  level (`low` / `medium` / `high` / `critical`) with a numeric score and
  named risk factors.
- **Policy Compliance Agent** — checks the use case against the
  organizational policy repository and reports satisfied/violated policies.
- **Decision Agent** — combines both findings and recommends `approve`,
  `require_human_approval`, or `block`.

The platform is built so additional specialized agents (e.g. a Bias/Fairness
Agent, a Vendor Risk Agent) can be added later without changing the
orchestration model.

## Workflow

```
Input → Risk Assessment → Policy Check → Decision → Human Approval (if needed) → Audit Log
```

Every stage writes an entry to an append-only audit log
(`data/audit_log.jsonl`), and the resulting report is persisted to
`data/reports/<use_case_id>.json`. If the Decision Agent says
`require_human_approval`, the use case sits in `pending_human_approval`
status until a human calls the approve/reject action, which is itself
logged.

## How the agents use tools

Agents don't just free-associate — they call real tools (via OpenAI-style
function calling through the OpenAI API) backed by this repo's data:

| Tool | Backing data | Used by |
|---|---|---|
| `get_risk_rules`, `get_scoring_bands` | `data/risk_rules.yaml` | Risk Assessment Agent |
| `get_policies`, `search_policies` | `data/policies.yaml` | Policy Compliance Agent, Decision Agent |
| `analyze_document` | regex/keyword heuristics over submitted documentation | Risk Assessment Agent, Policy Compliance Agent |
| `log_event` / audit log | `data/audit_log.jsonl` | orchestrator (every stage) |

## Project layout

```
app/
  agents/
    base.py            # shared tool-calling loop against OpenAI
    risk_agent.py       # Risk Assessment Agent
    policy_agent.py      # Policy Compliance Agent
    decision_agent.py    # Decision Agent
  tools/
    policy_repository.py # get_policies / search_policies
    risk_rules.py         # get_risk_rules / get_scoring_bands
    document_analysis.py  # analyze_document
    audit_log.py           # log_event / get_audit_log
  orchestrator.py       # runs the full workflow, human-approval step
  models.py              # pydantic models (AIUseCase, GovernanceReport, ...)
  api.py                  # FastAPI app
  cli.py                   # command-line interface
data/
  policies.yaml          # organizational AI governance policies
  risk_rules.yaml         # risk-scoring rules
  reports/                # one JSON governance report per use case (generated)
  audit_log.jsonl          # append-only audit trail (generated)
examples/
  sample_use_case.json    # example high-risk AI use case for a demo run
frontend/                 # React + Vite UI (submit, list, view, approve reports)
tests/                    # pytest suite (LLM calls are mocked, no API key needed)
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set OPENAI_API_KEY (get one at https://platform.openai.com/api-keys)
# OPENAI_MODEL can be any OpenAI model, e.g. gpt-4o-mini, gpt-4o, gpt-4.1-mini
```

To also run the web UI:

```bash
cd frontend
npm install
```

## Usage

### CLI

```bash
# Submit the bundled example (a fully-autonomous loan-denial agent — expect
# a high-risk, non-compliant, block/require-human-approval outcome)
python -m app.cli submit examples/sample_use_case.json

# List all reports
python -m app.cli list

# Show one report
python -m app.cli show <use_case_id>

# Record a human decision on a use case pending approval
python -m app.cli approve <use_case_id> --approver "you@example.com" --approve --notes "added human review step"

# Inspect the audit trail
python -m app.cli audit-log <use_case_id>
```

### API

```bash
python main.py
# -> http://localhost:8000
```

| Method | Path | Description |
|---|---|---|
| POST | `/use-cases` | Submit a new AI use case; runs the full agent pipeline |
| GET | `/use-cases` | List all governance reports |
| GET | `/use-cases/{id}` | Fetch one report |
| POST | `/use-cases/{id}/approve` | Record a human approve/reject decision |
| GET | `/audit-log?use_case_id=...` | Read the audit trail |
| GET | `/policies` | List the policy repository |
| GET | `/risk-rules` | List the risk-scoring rules |

Interactive API docs are available at `/docs` once the server is running.

### Web UI

With the API running (`python main.py`), start the frontend separately:

```bash
cd frontend
npm run dev
# -> http://localhost:5173
```

Submit a use case, browse reports, and record human approve/reject decisions
from the browser instead of the CLI or raw API calls. See
[frontend/README.md](frontend/README.md) for details.

## Tests

```bash
pytest
```

The test suite mocks the OpenAI client, so it runs without a real API
key or network access.
