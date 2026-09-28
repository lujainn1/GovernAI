"""Agent observability and monitoring.

    BaseAgent.last_trace -> recorder (agent_runs table, linked to the audit log)
                         -> metrics (RED-style aggregates) -> health (failure
                            classes, latency anomalies, HEALTHY/DEGRADED/CRITICAL)

Structured logging and the per-request id live in `context` and
`logging_config`. Nothing here ever stores or logs prompts or submitted
documentation. Only names, timings, counts, truncated previews of tool
arguments/outputs, and truncated error text are kept (an unparseable model
reply can leave a short fragment of it in the error text).
"""
