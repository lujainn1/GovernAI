# AI Agent Failure Diagnosis & Evaluation System

A predictive maintenance agent for a smart manufacturing factory, combined
with automated evaluation, runtime guardrails, human-in-the-loop approval,
and a full observability/performance-monitoring layer.

Everything runs offline: no API key, no external service. Every "tool" is
a simulated lookup or a recorded side effect over three sample machines.

## Sample machines

| Machine | Status | Maintenance History | Failure Risk |
|---|---|---|---|
| `CNC-01` | High vibration, normal temperature, 85% capacity | Bearing replaced twice this year | High |
| `ROBOT-03` | Normal operation, no anomalies | No major issues | Low |
| `CONVEYOR-02` | Elevated motor temperature, possible bearing issue | Previous motor overheating | Medium |

## Project structure

| File | Role |
|---|---|
| `tools.py` | All 7 tools: 3 read-only (status/history/risk), 2 sensitive (`stop_machine`, `schedule_emergency_maintenance`), 2 forbidden (`delete_machine_data`, `modify_safety_logs`). `SIDE_EFFECTS` records every real execution. |
| `guardrails.py` | The policy brain: `evaluate_policy()` (pure ALLOW/DENY/REQUIRES_APPROVAL decision) and `dispatch_action()` (the single execution gateway, with built-in loop monitoring). |
| `approval_manager.py` | The human-in-the-loop checkpoint store: create, approve (executes once), reject (never executes). |
| `observability.py` | `ExecutionTrace`: one object per run, logging every observable event with a unique `trace_id`. Persists to `data/traces.json`. |
| `performance_monitor.py` | `PerformanceMonitor`: run-level KPIs (success/error rate, latency percentiles, tool calls, steps) and per-tool metrics, computed from traces. |
| `log_analyzer.py` | Scans the audit log and traces for 8 categories of recurring problems and explains each one in plain English. |
| `audit_logger.py` | One append-only, disk-persisted list every policy and approval decision is written to (`data/audit_log.json`). |
| `agent.py` | The two agent versions (`improved`, `baseline`), each proposing actions through `guardrails.dispatch_action()` - never executing anything directly. |
| `failure_detector.py` | The original 6 Part-1 failure patterns (Wrong Tool Selection, Repeated Tool Loop, Premature Answer, Missing Information, Invalid Machine ID, Unsupported Claim). |
| `evaluation_dataset.py`, `evaluator.py` | The 10 frozen test cases and the scorer, now also reporting error rate, P95 latency, guardrail violations, and loop events per agent version. |
| `tests/test_guardrails.py` | The 10 requested guardrail tests + 2 loop-monitoring tests. |
| `tests/test_evaluator.py` | Sanity tests for the dataset and evaluator, including "improved must not score worse than baseline." |
| `tests/test_logging.py` | Tests for `ExecutionTrace`, `PerformanceMonitor`, and `log_analyzer`. |
| `data/traces.json`, `data/audit_log.json` | Persisted logs, rewritten as the app runs. Start empty; delete and recreate as `[]` to reset. |
| `app.py` | The 9-page Streamlit dashboard. |

## Why the baseline agent fails almost every test - and now gets caught looping

The baseline agent has exactly three general, realistic bugs, applied
consistently to every question - never tuned per test case:

1. It always calls **every** tool, in the wrong order, with one duplicate
   call. The new guardrail loop monitor now catches that duplicate call
   itself: baseline's run is force-stopped (`SAFE_STOP`,
   `failure_type=POTENTIAL_AGENT_LOOP`) partway through, before it can even
   reach its old "unsupported claim" sentence.
2. If it cannot find a machine ID in the question, it silently guesses one
   instead of asking for clarification (**Missing Information**).
3. It always appends one sentence to its final answer that no tool ever
   produced (**Unsupported Claim**) - on the rare path where it isn't
   stopped first.

Because the evaluation dataset checks for exactly these behaviors, the
baseline agent fails every case (0/10), while the improved agent - which
calls only the tools it needs, asks for clarification when information is
missing, never adds ungrounded claims, and never repeats a call - passes
every case (10/10). Every one of these numbers is a measured result of
running the actual code, not a scripted outcome.

## Only observable behavior is evaluated or logged

Every check in `evaluator.py`, `failure_detector.py`, and every event in
`observability.py` looks only at things you could see from the outside:
which tools were called, what they returned, what evidence was gathered,
the agent's status, its latency, and its final response. Nothing here
inspects, logs, or scores private reasoning.

## A note on percentiles

With only 10 evaluation cases, latency percentiles (P95/P99) are
illustrative rather than statistically robust - a single expensive case
(e.g. a full 3-tool diagnosis) can dominate a small sample's P95. Treat
percentiles from `System Monitoring` and `Baseline vs Improved` as useful
signals, not precise statistics, until far more runs have accumulated.

## Running the project

```bash
python -m venv .venv
.venv\Scripts\activate        # on Windows
pip install -r requirements.txt

python tests/test_guardrails.py
python tests/test_evaluator.py
python tests/test_logging.py

streamlit run app.py
```

Then open the local URL Streamlit prints (usually `http://localhost:8501`).
