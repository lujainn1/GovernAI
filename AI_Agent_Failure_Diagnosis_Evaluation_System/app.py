"""
app.py
------
Streamlit dashboard for the AI Agent Failure Diagnosis & Evaluation System,
extended with Runtime Guardrails, Human-in-the-Loop approval, Audit
Logging, Observability, and Performance Monitoring.

Run it with:
    streamlit run app.py

Pages (see the sidebar):
    1. Agent Diagnosis      - ask the agent a live question and inspect its trace.
    2. Guardrail Monitor     - propose any action and watch the policy decide.
    3. Human Approvals       - approve or reject actions that need a human.
    4. Execution Traces      - pick one run and see its full event timeline.
    5. System Monitoring     - KPIs, tool performance, and detected issues.
    6. Audit Log             - every policy decision and approval, in order.
    7. Evaluation Dataset    - browse the frozen test cases.
    8. Automated Evaluation  - run all test cases against one agent version.
    9. Baseline vs Improved  - compare both agent versions on the same dataset.
"""

import streamlit as st

import approval_manager
import audit_logger
import guardrails
import observability
from agent import run_diagnosis, run_max_steps_demo, run_repeated_call_demo
from evaluation_dataset import EVALUATION_CASES
from evaluator import compare_versions, run_evaluation, summarize
from failure_detector import detect_failures
from log_analyzer import analyze as analyze_logs
from performance_monitor import PerformanceMonitor
from tools import MACHINE_IDS

st.set_page_config(
    page_title="AI Agent Failure Diagnosis & Evaluation System", page_icon=":wrench:", layout="wide"
)

PAGES = [
    "Agent Diagnosis",
    "Guardrail Monitor",
    "Human Approvals",
    "Execution Traces",
    "System Monitoring",
    "Audit Log",
    "Evaluation Dataset",
    "Automated Evaluation",
    "Baseline vs Improved",
]

st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", PAGES)

st.title("AI Agent Failure Diagnosis & Evaluation System")
st.caption(
    "A predictive maintenance agent, a failure diagnosis workflow, runtime guardrails, "
    "human-in-the-loop approval, observability, and an automated evaluator."
)

DECISION_LABELS = {
    "ALLOW": ":green[ALLOW]",
    "DENY": ":red[DENY]",
    "REQUIRES_APPROVAL": ":orange[REQUIRES APPROVAL]",
    "SAFE_STOP": ":red[SAFE STOP]",
}


def _decision_label(decision: str) -> str:
    return DECISION_LABELS.get(decision, decision)


# =====================================================================
# Page 1: Agent Diagnosis
# =====================================================================
if page == "Agent Diagnosis":
    st.header("Agent Diagnosis")
    st.write("Ask the agent a question about one of the known machines: " + ", ".join(MACHINE_IDS) + ".")
    st.caption(
        "Every tool call the agent makes here is proposed to the guardrail policy layer first, and every "
        "step is recorded in its own ExecutionTrace - see 'Execution Traces' to inspect it afterward."
    )

    agent_version = st.radio("Agent version", ["improved", "baseline"], horizontal=True)
    example_queries = [
        "Check the status of CNC-01 machine.",
        "What is the maintenance history of ROBOT-03?",
        "Is CONVEYOR-02 at risk of failure?",
        "Give me a full diagnosis for CNC-01.",
    ]
    query = st.text_input("Your question", value=example_queries[0])
    st.caption("Try one of the examples above, or type your own question.")

    if st.button("Run Diagnosis"):
        result = run_diagnosis(query, agent_version=agent_version)
        failures = detect_failures(result)

        st.subheader("Diagnosis Summary")
        st.write(result["diagnosis"])
        st.caption(f"Trace ID: `{result['trace_id']}` (look it up on the 'Execution Traces' page)")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.subheader("Machine Status")
            st.write(result["status_result"] or "Not checked for this question.")
        with col2:
            st.subheader("Maintenance History")
            st.write(result["history_result"] or "Not checked for this question.")
        with col3:
            st.subheader("Failure Risk")
            st.write(result["risk_result"] or "Not checked for this question.")

        st.subheader("Recommendation")
        st.write(result["recommendation"])

        st.subheader("Execution Trace")
        st.write(
            f"Agent status: `{result['agent_status']}` &nbsp;|&nbsp; "
            f"Tool calls: `{result['tool_calls']}` &nbsp;|&nbsp; "
            f"Latency: `{result['latency_ms']} ms`"
        )
        if failures:
            st.warning("Failure patterns detected: " + ", ".join(failures))
        else:
            st.success("No failure patterns detected.")

        for entry in result["trace"]:
            tool_label = f" ({entry['tool']})" if entry["tool"] else ""
            st.text(f"[{entry['timestamp']}] {entry['step']}{tool_label}: {entry['output']}")


# =====================================================================
# Page 2: Guardrail Monitor
# =====================================================================
elif page == "Guardrail Monitor":
    st.header("Guardrail Monitor")
    st.write(
        "Propose any action directly and watch the deterministic policy layer decide. "
        "Nothing here executes until the policy - not this page - allows it."
    )

    ALL_TOOL_NAMES = [
        "check_machine_status",
        "get_maintenance_history",
        "predict_failure_risk",
        "stop_machine",
        "schedule_emergency_maintenance",
        "delete_machine_data",
        "modify_safety_logs",
        "reformat_disk (unknown tool, for testing)",
    ]

    with st.form("propose_action_form"):
        machine_id = st.selectbox("Machine", MACHINE_IDS + ["PRESS-99 (unknown machine, for testing)"])
        tool_choice = st.selectbox("Tool to propose", ALL_TOOL_NAMES)
        reason_or_entry = st.text_input(
            "Reason / log entry (required for stop_machine, schedule_emergency_maintenance, modify_safety_logs)",
            value="High vibration detected during routine inspection.",
        )
        submitted = st.form_submit_button("Propose Action")

    if submitted:
        clean_machine_id = machine_id.split(" ")[0]
        clean_tool_name = tool_choice.split(" ")[0]

        arguments = {"machine_id": clean_machine_id}
        if clean_tool_name in ("stop_machine", "schedule_emergency_maintenance"):
            arguments["reason"] = reason_or_entry
        elif clean_tool_name == "modify_safety_logs":
            arguments["entry"] = reason_or_entry

        trace = observability.ExecutionTrace(agent_name="dashboard_user", query=f"Manual proposal: {clean_tool_name}")
        result = guardrails.dispatch_action(trace, clean_machine_id, clean_tool_name, arguments)
        trace.finish("completed" if result["decision"] in ("ALLOW", "REQUIRES_APPROVAL") else result["decision"].lower(), result)

        st.subheader("Policy Decision")
        col1, col2 = st.columns(2)
        with col1:
            st.write(f"**Proposed Action:** {clean_tool_name}")
            st.write(f"**Machine:** {clean_machine_id}")
        with col2:
            st.markdown(f"**Policy Decision:** {_decision_label(result['decision'])}")
            st.write(f"**Reason:** {result['reason']}")
        st.caption(f"Trace ID: `{trace.trace_id}`")

        if result["decision"] == "REQUIRES_APPROVAL":
            st.info(f"A checkpoint was created ({result['action_id']}). See the 'Human Approvals' page to resolve it.")
        elif result["decision"] == "ALLOW":
            st.success(f"Executed. Result: {result['result']}")

    st.divider()
    st.subheader("Loop Monitoring Demos")
    st.write(
        f"MAX_AGENT_STEPS is set to **{guardrails.MAX_AGENT_STEPS}**. A run is force-stopped, with "
        "`failure_type = POTENTIAL_AGENT_LOOP`, if it exceeds that budget *or* repeats an identical call."
    )

    demo_col1, demo_col2 = st.columns(2)
    with demo_col1:
        st.write("**Demo A: step budget.** Proposes 9 distinct, safe actions in a row.")
        if st.button("Run Step-Budget Demo"):
            for entry in run_max_steps_demo(9):
                st.text(f"Step {entry['step']} ({entry['tool']}, {entry['machine_id']}): {_decision_label(entry['decision'])}")
    with demo_col2:
        st.write("**Demo B: identical repeat.** Proposes the exact same call twice.")
        if st.button("Run Repeated-Call Demo"):
            for entry in run_repeated_call_demo():
                st.text(f"Step {entry['step']} ({entry['machine_id']}): {_decision_label(entry['decision'])}")


# =====================================================================
# Page 3: Human Approvals
# =====================================================================
elif page == "Human Approvals":
    st.header("Human Approvals")
    st.write("Sensitive actions wait here until a human reviewer approves or rejects them.")

    pending = approval_manager.list_pending()
    if not pending:
        st.info("No actions are currently waiting for approval. Propose a sensitive action from 'Guardrail Monitor'.")
    else:
        for checkpoint in pending:
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns(4)
                col1.write(f"**Machine:** {checkpoint['machine_id']}")
                col2.write(f"**Requested Action:** {checkpoint['tool_name']}")
                col3.write(f"**Reason:** {checkpoint['reason']}")
                risk_label = "Sensitive" if checkpoint["tool_name"] in guardrails.SENSITIVE_TOOLS else "Unknown"
                col4.write(f"**Risk:** {risk_label}")
                st.write(f"**Timestamp:** {checkpoint['timestamp']} &nbsp;|&nbsp; **Action ID:** {checkpoint['action_id']}")

                approve_col, reject_col = st.columns(2)
                if approve_col.button("APPROVE", key=f"approve_{checkpoint['action_id']}"):
                    approval_manager.approve(checkpoint["action_id"], reviewer="dashboard_reviewer")
                    st.rerun()
                if reject_col.button("REJECT", key=f"reject_{checkpoint['action_id']}"):
                    approval_manager.reject(checkpoint["action_id"], reviewer="dashboard_reviewer")
                    st.rerun()

    st.divider()
    st.subheader("Resolved Actions")
    resolved = [c for c in approval_manager.list_all() if c["status"] != approval_manager.PENDING_APPROVAL]
    if not resolved:
        st.caption("No actions have been resolved yet.")
    else:
        rows = [
            {
                "Action ID": c["action_id"],
                "Machine": c["machine_id"],
                "Tool": c["tool_name"],
                "Status": c["status"],
                "Reviewer": c["reviewer"],
                "Approved/Rejected At": c["approval_timestamp"],
            }
            for c in resolved
        ]
        st.dataframe(rows, use_container_width=True)


# =====================================================================
# Page 4: Execution Traces
# =====================================================================
elif page == "Execution Traces":
    st.header("Execution Traces")
    st.write(
        "Pick a run and see its full timeline: Agent Started -> Tool Requested -> Guardrail Decision -> "
        "Tool Executed -> Result Returned -> Final Response."
    )

    traces = sorted(observability.list_traces(), key=lambda t: t.trace_id, reverse=True)
    if not traces:
        st.info("No traces yet. Run a diagnosis or propose an action to create one.")
    else:
        options = {f"{t.trace_id}  -  {t.query[:60]}  [{t.status}]": t.trace_id for t in traces}
        chosen_label = st.selectbox("Select a trace_id", list(options.keys()))
        trace = observability.get_trace(options[chosen_label])

        col1, col2, col3 = st.columns(3)
        col1.metric("Status", trace.status)
        col2.metric("Total Latency (ms)", trace.total_latency_ms)
        col3.metric("Events", len(trace.events))

        st.subheader("Timeline")
        rows = [
            {
                "Step": event.step_number,
                "Event Type": event.event_type,
                "Tool": event.tool_name or "-",
                "Status": event.status,
                "Latency (ms)": event.latency_ms,
                "Result": str(event.result)[:80] if event.result else "-",
            }
            for event in trace.events
        ]
        st.dataframe(rows, use_container_width=True)

        st.subheader("Export")
        st.download_button(
            "Download this trace as JSON",
            data=trace.to_json(),
            file_name=f"trace_{trace.trace_id}.json",
            mime="application/json",
        )


# =====================================================================
# Page 5: System Monitoring
# =====================================================================
elif page == "System Monitoring":
    st.header("System Monitoring")

    all_traces = [t.to_dict() for t in observability.list_traces()]
    monitor = PerformanceMonitor(all_traces)
    summary = monitor.summary()

    st.subheader("Key Metrics")
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Total Runs", summary["total_runs"])
    col2.metric("Success Rate", f"{summary['success_rate']}%")
    col3.metric("Average Latency", f"{summary['avg_latency_ms']} ms")
    col4.metric("P95 Latency", f"{summary['p95_latency_ms']} ms")
    col5.metric("Tool Calls", summary["avg_tool_calls"])
    col6.metric("Errors", summary["failed_runs"])

    st.caption(
        f"P50: {summary['p50_latency_ms']} ms | P99: {summary['p99_latency_ms']} ms | "
        f"Max tool calls in one run: {summary['max_tool_calls']} | Average steps per run: {summary['avg_steps']}"
    )

    st.subheader("Recent Agent Runs")
    if not all_traces:
        st.caption("No runs recorded yet.")
    else:
        recent = sorted(all_traces, key=lambda t: t["trace_id"], reverse=True)[:20]
        rows = [
            {
                "Trace ID": t["trace_id"],
                "Agent": t["agent_name"],
                "Query": t["query"][:50],
                "Status": t["status"],
                "Latency (ms)": t["total_latency_ms"],
                "Events": len(t["steps"]),
            }
            for t in recent
        ]
        st.dataframe(rows, use_container_width=True)

    st.subheader("Tool Performance")
    tool_metrics = monitor.tool_metrics()
    if not tool_metrics:
        st.caption("No tool calls recorded yet.")
    else:
        rows = [
            {"Tool": tool, **metrics} for tool, metrics in tool_metrics.items()
        ]
        st.dataframe(rows, use_container_width=True)

    st.subheader("Detected Failures")
    findings = analyze_logs(audit_logger.get_audit_log(), all_traces)
    if not findings:
        st.success("No issues detected in the audit log or execution traces.")
    else:
        for finding in findings:
            st.warning(f"**{finding['issue']}** - {finding['explanation']}")


# =====================================================================
# Page 6: Audit Log
# =====================================================================
elif page == "Audit Log":
    st.header("Audit Log")
    st.write("Every policy decision and every human approval decision, in the order they happened.")

    entries = audit_logger.get_audit_log()
    if not entries:
        st.info("No audit events yet. Run a diagnosis or propose an action to generate some.")
    else:
        rows = [
            {
                "Timestamp": e["timestamp"],
                "Machine": e["machine_id"],
                "Agent": e["agent"],
                "Tool": e["requested_tool"],
                "Decision": e["policy_decision"],
                "Executed": e["executed"],
                "Reviewer": e["reviewer"] or "-",
                "Latency (ms)": e.get("latency_ms", 0.0),
                "Result": str(e["result"])[:80] if e["result"] else "-",
            }
            for e in entries
        ]
        st.dataframe(rows, use_container_width=True)


# =====================================================================
# Page 7: Evaluation Dataset
# =====================================================================
elif page == "Evaluation Dataset":
    st.header("Evaluation Dataset")
    st.write(f"This frozen dataset has {len(EVALUATION_CASES)} test cases across 10 categories.")

    table_rows = [
        {
            "ID": case["id"],
            "Category": case["category"],
            "Difficulty": case["difficulty"],
            "Query": case["query"],
            "Expected Behavior": case["expected_behavior"],
            "Required Tools": ", ".join(case["required_tools"]) or "-",
            "Forbidden Tools": ", ".join(case["forbidden_tools"]) or "-",
            "Max Tool Calls": case["max_tool_calls"],
        }
        for case in EVALUATION_CASES
    ]
    st.dataframe(table_rows, use_container_width=True)


# =====================================================================
# Page 8: Automated Evaluation
# =====================================================================
elif page == "Automated Evaluation":
    st.header("Automated Evaluation")
    agent_version = st.selectbox("Agent version to evaluate", ["improved", "baseline"])

    if st.button("Run Evaluation"):
        results = run_evaluation(EVALUATION_CASES, agent_version)
        summary = summarize(results)

        st.session_state["eval_results"] = results
        st.session_state["eval_summary"] = summary
        st.session_state["eval_version"] = agent_version

    if "eval_results" in st.session_state:
        results = st.session_state["eval_results"]
        summary = st.session_state["eval_summary"]

        st.caption(f"Showing results for the **{st.session_state['eval_version']}** agent.")

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Tests", summary["total"])
        col2.metric("Passed Tests", summary["passed"])
        col3.metric("Failed Tests", summary["failed"])
        col4.metric("Pass Rate", f"{summary['pass_rate']}%")
        col5.metric("Average Latency", f"{summary['avg_latency_ms']} ms")

        if summary["failed_categories"]:
            st.warning("Categories with at least one failure: " + ", ".join(summary["failed_categories"]))
        else:
            st.success("Every category passed.")

        st.subheader("Individual Test Results")
        table_rows = [
            {
                "ID": r["id"],
                "Category": r["category"],
                "Result": "PASS" if r["passed"] else "FAIL",
                "Tool Calls": ", ".join(r["tool_calls"]) or "-",
                "Latency (ms)": r["latency_ms"],
            }
            for r in results
        ]
        st.dataframe(table_rows, use_container_width=True)

        st.subheader("Failure Analysis")
        failed_results = [r for r in results if not r["passed"]]
        if not failed_results:
            st.success("No failed tests to analyze - every case passed.")
        else:
            for r in failed_results:
                failed_checks = [name for name, ok in r["checks"].items() if not ok]
                with st.expander(f"Test {r['id']} - {r['category']} (FAIL)"):
                    st.write("**Failure reason(s):** " + ", ".join(failed_checks))
                    if r["failures_detected"]:
                        st.write("**Failure patterns detected:** " + ", ".join(r["failures_detected"]))
                    st.write("**Expected behavior:** " + r["expected_behavior"])
                    st.write("**Actual behavior:** " + r["actual_response"])
                    st.write("**Tools used:** " + (", ".join(r["tool_calls"]) or "none"))
    else:
        st.info("Click 'Run Evaluation' to test the selected agent against every case.")


# =====================================================================
# Page 9: Baseline vs Improved
# =====================================================================
elif page == "Baseline vs Improved":
    st.header("Baseline vs Improved")
    st.write("Runs the exact same frozen evaluation dataset against both agent versions.")

    if st.button("Run Comparison"):
        st.session_state["comparison"] = compare_versions(EVALUATION_CASES)

    if "comparison" in st.session_state:
        comparison = st.session_state["comparison"]
        baseline_summary = comparison["baseline_summary"]
        improved_summary = comparison["improved_summary"]

        st.subheader("Summary Comparison")
        metric_rows = [
            {"Metric": "Pass Rate (%)", "Baseline": baseline_summary["pass_rate"], "Improved": improved_summary["pass_rate"]},
            {"Metric": "Error Rate (%)", "Baseline": baseline_summary["error_rate"], "Improved": improved_summary["error_rate"]},
            {"Metric": "Average Latency (ms)", "Baseline": baseline_summary["avg_latency_ms"], "Improved": improved_summary["avg_latency_ms"]},
            {"Metric": "P95 Latency (ms)", "Baseline": baseline_summary["p95_latency_ms"], "Improved": improved_summary["p95_latency_ms"]},
            {"Metric": "Average Tool Calls", "Baseline": baseline_summary["avg_tool_calls"], "Improved": improved_summary["avg_tool_calls"]},
            {"Metric": "Average Steps", "Baseline": baseline_summary["avg_steps"], "Improved": improved_summary["avg_steps"]},
            {"Metric": "Guardrail Violations", "Baseline": baseline_summary["guardrail_violations"], "Improved": improved_summary["guardrail_violations"]},
            {"Metric": "Loop Events", "Baseline": baseline_summary["loop_events"], "Improved": improved_summary["loop_events"]},
        ]
        st.dataframe(metric_rows, use_container_width=True)

        if improved_summary["pass_rate"] > baseline_summary["pass_rate"]:
            st.success(
                f"The improved agent passed {improved_summary['pass_rate']}% of tests, "
                f"compared to {baseline_summary['pass_rate']}% for the baseline agent, "
                f"with {improved_summary['loop_events']} loop event(s) versus {baseline_summary['loop_events']}. "
                "Based on these measured results, the improved agent performs better."
            )
        elif improved_summary["pass_rate"] < baseline_summary["pass_rate"]:
            st.error(
                "Based on these measured results, the baseline agent actually scored higher. "
                "The 'improved' agent needs further work."
            )
        else:
            st.info("Both agent versions scored the same pass rate on this dataset.")
    else:
        st.info("Click 'Run Comparison' to evaluate both agent versions.")
