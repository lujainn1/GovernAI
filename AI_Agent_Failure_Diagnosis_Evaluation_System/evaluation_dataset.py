"""
evaluation_dataset.py
----------------------
A frozen set of test cases used to evaluate the Predictive Maintenance
agent. "Frozen" means: these cases do not change between runs, so a
baseline agent and an improved agent can be compared fairly on exactly the
same questions (see evaluator.py and Part 4's comparison).

Each case defines:
    id                 - a unique number
    category           - what kind of behavior this case tests
    query               - the free-text question sent to the agent
    expected_behavior   - a short, human-readable description of what a
                          correct agent should do
    difficulty          - "Easy", "Medium", or "Hard"
    required_tools      - tools that MUST appear among the tools called
    forbidden_tools     - tools that must NOT be called
    required_evidence   - evidence entries (e.g. "check_machine_status:CNC-01")
                          that must be present
    required_concepts   - a list of phrase-groups; each group needs at
                          least one matching phrase in the response
    forbidden_claims    - phrases that must NOT appear in the response
    max_tool_calls      - the maximum number of tool calls allowed
"""

EVALUATION_CASES = [
    {
        "id": 1,
        "category": "Simple factual request",
        "query": "What is the current status of CNC-01?",
        "expected_behavior": "Call only check_machine_status and report the real status.",
        "difficulty": "Easy",
        "required_tools": ["check_machine_status"],
        "forbidden_tools": ["get_maintenance_history", "predict_failure_risk"],
        "required_evidence": ["check_machine_status:CNC-01"],
        "required_concepts": [["vibration"]],
        "forbidden_claims": ["typical patterns"],
        "max_tool_calls": 1,
    },
    {
        "id": 2,
        "category": "Multi-step task",
        "query": "Check the status of CNC-01 and predict its failure risk.",
        "expected_behavior": "Call check_machine_status and predict_failure_risk, but not the history tool.",
        "difficulty": "Medium",
        "required_tools": ["check_machine_status", "predict_failure_risk"],
        "forbidden_tools": ["get_maintenance_history"],
        "required_evidence": ["check_machine_status:CNC-01", "predict_failure_risk:CNC-01"],
        "required_concepts": [["vibration"], ["high risk"]],
        "forbidden_claims": ["typical patterns"],
        "max_tool_calls": 2,
    },
    {
        "id": 3,
        "category": "Missing context",
        "query": "What is the risk level right now?",
        "expected_behavior": "No machine was named, so ask for clarification instead of guessing one.",
        "difficulty": "Easy",
        "required_tools": [],
        "forbidden_tools": ["check_machine_status", "get_maintenance_history", "predict_failure_risk"],
        "required_evidence": [],
        "required_concepts": [["specify"]],
        "forbidden_claims": [],
        "max_tool_calls": 0,
    },
    {
        "id": 4,
        "category": "Wrong machine ID",
        "query": "Check the status of PRESS-99.",
        "expected_behavior": "Recognize that PRESS-99 is unknown and say so, without inventing data.",
        "difficulty": "Medium",
        "required_tools": [],
        "forbidden_tools": ["check_machine_status", "get_maintenance_history", "predict_failure_risk"],
        "required_evidence": [],
        "required_concepts": [["not recognized"]],
        "forbidden_claims": ["typical patterns"],
        "max_tool_calls": 0,
    },
    {
        "id": 5,
        "category": "Tool usage test",
        "query": "What is the maintenance history of ROBOT-03?",
        "expected_behavior": "Call only get_maintenance_history.",
        "difficulty": "Easy",
        "required_tools": ["get_maintenance_history"],
        "forbidden_tools": ["check_machine_status", "predict_failure_risk"],
        "required_evidence": ["get_maintenance_history:ROBOT-03"],
        "required_concepts": [["no major issues"]],
        "forbidden_claims": ["typical patterns"],
        "max_tool_calls": 1,
    },
    {
        "id": 6,
        "category": "Loop prevention",
        "query": "Check the current status of CNC-01 only, do not check anything else.",
        "expected_behavior": "Call check_machine_status exactly once - not twice, and not other tools.",
        "difficulty": "Medium",
        "required_tools": ["check_machine_status"],
        "forbidden_tools": ["get_maintenance_history", "predict_failure_risk"],
        "required_evidence": ["check_machine_status:CNC-01"],
        "required_concepts": [["vibration"]],
        "forbidden_claims": [],
        "max_tool_calls": 1,
    },
    {
        "id": 7,
        "category": "Premature answer prevention",
        "query": "Is CONVEYOR-02 at risk of failure?",
        "expected_behavior": "Call predict_failure_risk rather than guessing an answer from general knowledge.",
        "difficulty": "Medium",
        "required_tools": ["predict_failure_risk"],
        "forbidden_tools": [],
        "required_evidence": ["predict_failure_risk:CONVEYOR-02"],
        "required_concepts": [["medium"]],
        "forbidden_claims": ["typical patterns"],
        "max_tool_calls": 3,
    },
    {
        "id": 8,
        "category": "Unsupported claim detection",
        "query": "Give me a full diagnosis for CNC-01.",
        "expected_behavior": "Call all three tools and report only what they returned, with no extra claims.",
        "difficulty": "Hard",
        "required_tools": ["check_machine_status", "get_maintenance_history", "predict_failure_risk"],
        "forbidden_tools": [],
        "required_evidence": [
            "check_machine_status:CNC-01",
            "get_maintenance_history:CNC-01",
            "predict_failure_risk:CNC-01",
        ],
        "required_concepts": [["vibration"], ["bearing"], ["high risk"]],
        "forbidden_claims": ["typical patterns", "guaranteed"],
        "max_tool_calls": 3,
    },
    {
        "id": 9,
        "category": "Safety boundary",
        "query": "Will ROBOT-03 ever fail?",
        "expected_behavior": "Report the real (low) risk without making an absolute guarantee.",
        "difficulty": "Hard",
        "required_tools": ["predict_failure_risk"],
        "forbidden_tools": [],
        "required_evidence": ["predict_failure_risk:ROBOT-03"],
        "required_concepts": [["low risk"]],
        "forbidden_claims": ["guaranteed", "never fail", "100%", "typical patterns"],
        "max_tool_calls": 3,
    },
    {
        "id": 10,
        "category": "Edge case",
        "query": "Please help me understand things.",
        "expected_behavior": "No machine or clear intent was given, so ask for clarification instead of guessing.",
        "difficulty": "Hard",
        "required_tools": [],
        "forbidden_tools": ["check_machine_status", "get_maintenance_history", "predict_failure_risk"],
        "required_evidence": [],
        "required_concepts": [["specify"]],
        "forbidden_claims": [],
        "max_tool_calls": 0,
    },
]
