"""Tests for the four-way policy determination.

Before this, `PolicyComplianceResult` could only say "violated" or
"satisfied", and the Policy Agent was told to treat anything it could not
confirm as violated - so every submission came back non_compliant with no
satisfied policies. These tests pin the semantics that replaced that:
missing information is `undetermined`, out-of-scope policies are
`not_applicable`, and both are additive so old payloads stay valid.
"""
import json
from types import SimpleNamespace

from app.agents.policy_agent import SYSTEM_PROMPT, PolicyComplianceAgent
from app.models import AIUseCase, ComplianceStatus, PolicyComplianceResult, RiskAssessmentResult, RiskLevel
from app.prompts import build_compliance_context
from app.tools.policy_repository import GET_POLICIES_SCHEMA, get_policies


def _use_case() -> AIUseCase:
    return AIUseCase(
        name="Internal FAQ assistant",
        description="Answers staff questions from the public internal wiki.",
        owner="it-support",
        data_classification="public",
        deployment_context="internal-tool",
        autonomy_level="human-in-the-loop",
    )


def _risk() -> RiskAssessmentResult:
    return RiskAssessmentResult(
        risk_level=RiskLevel.LOW,
        risk_score=15,
        risk_factors=["no personal data"],
        rationale="Read-only, public content.",
    )


class FakeClient:
    def __init__(self, payload):
        message = SimpleNamespace(content=json.dumps(payload), tool_calls=None)
        completion = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: completion)
        )


def _check(monkeypatch, **payload):
    full = {"status": "partially_compliant", "rationale": "r"}
    full.update(payload)
    monkeypatch.setattr("app.agents.base.get_client", lambda: FakeClient(full))
    return PolicyComplianceAgent().check(_use_case(), _risk())


# --- the model can express all four determinations -------------------------


def test_all_four_determinations_survive(monkeypatch):
    result = _check(
        monkeypatch,
        violated_policies=["POL-003"],
        satisfied_policies=["POL-002"],
        undetermined_policies=["PD.11"],
        not_applicable_policies=["XFER-01"],
    )

    assert result.violated_policies == ["POL-003"]
    assert result.satisfied_policies == ["POL-002"]
    assert result.undetermined_policies == ["PD.11"]
    assert result.not_applicable_policies == ["XFER-01"]


def test_partially_compliant_is_reachable(monkeypatch):
    result = _check(
        monkeypatch,
        status="partially_compliant",
        satisfied_policies=["POL-002"],
        undetermined_policies=["PD.11", "DM.2"],
    )

    assert result.status is ComplianceStatus.PARTIALLY_COMPLIANT
    assert result.violated_policies == []


def test_compliant_with_nothing_undetermined_is_reachable(monkeypatch):
    result = _check(monkeypatch, status="compliant", satisfied_policies=["POL-002"])

    assert result.status is ComplianceStatus.COMPLIANT


# --- validation covers the new lists too ----------------------------------


def test_fabricated_ids_are_dropped_from_every_list(monkeypatch):
    result = _check(
        monkeypatch,
        violated_policies=["POL-999", "POL-003"],
        satisfied_policies=["NOPE", "POL-002"],
        undetermined_policies=["INVENTED", "PD.11"],
        not_applicable_policies=["XFER-99", "XFER-01"],
    )

    assert result.violated_policies == ["POL-003"]
    assert result.satisfied_policies == ["POL-002"]
    assert result.undetermined_policies == ["PD.11"]
    assert result.not_applicable_policies == ["XFER-01"]


def test_a_policy_in_two_lists_keeps_the_most_conservative(monkeypatch):
    result = _check(
        monkeypatch,
        violated_policies=["POL-003"],
        satisfied_policies=["POL-003"],
        undetermined_policies=["POL-003"],
        not_applicable_policies=["POL-003"],
    )

    assert result.violated_policies == ["POL-003"]
    assert result.satisfied_policies == []
    assert result.undetermined_policies == []
    assert result.not_applicable_policies == []


def test_satisfied_beats_undetermined_for_a_duplicate(monkeypatch):
    result = _check(
        monkeypatch, satisfied_policies=["POL-002"], undetermined_policies=["POL-002"]
    )

    assert result.satisfied_policies == ["POL-002"]
    assert result.undetermined_policies == []


def test_cross_list_duplicates_are_logged(monkeypatch, caplog):
    with caplog.at_level("WARNING"):
        _check(monkeypatch, violated_policies=["POL-003"], undetermined_policies=["POL-003"])

    assert any("more than one determination" in r.getMessage() for r in caplog.records)


# --- backward compatibility -----------------------------------------------


def test_a_payload_without_the_new_lists_is_still_valid(monkeypatch):
    result = _check(monkeypatch, status="non_compliant", violated_policies=["POL-003"])

    assert result.undetermined_policies == []
    assert result.not_applicable_policies == []


def test_the_model_can_be_constructed_the_old_way():
    legacy = PolicyComplianceResult(
        status=ComplianceStatus.NON_COMPLIANT,
        violated_policies=["POL-001"],
        satisfied_policies=[],
        rationale="r",
    )

    assert legacy.undetermined_policies == []
    assert legacy.not_applicable_policies == []


# --- the instructions that produce these determinations -------------------


def test_the_prompt_forbids_treating_missing_information_as_a_violation():
    assert "A missing fact is not a violation" in SYSTEM_PROMPT
    assert "undetermined, not violated" in SYSTEM_PROMPT
    # and does not swing the other way
    assert "silence is never evidence of compliance" in SYSTEM_PROMPT


def test_the_prompt_requires_an_applicability_decision_first():
    assert "applies_when" in SYSTEM_PROMPT
    assert "not_applicable_policies" in SYSTEM_PROMPT
    assert "Do not assume a fact you were not told" in SYSTEM_PROMPT


def test_the_prompt_defines_every_status():
    for status in ("non_compliant", "partially_compliant", "compliant"):
        assert status in SYSTEM_PROMPT


def test_the_category_filter_documents_the_categories_that_exist():
    """The old description advertised six categories, five of which matched
    one or two policies each, so filtering was useless and the agent could
    only fetch all 140. Keep the documented vocabulary honest."""
    described = GET_POLICIES_SCHEMA["function"]["parameters"]["properties"]["category"][
        "description"
    ]
    categories = {policy["category"] for policy in get_policies()}

    assert categories, "no categories in the repository"
    for category in categories:
        assert category in described, f"{category} is not documented for the agent"


def test_undetermined_policies_reach_the_decision_and_review_agents():
    context = build_compliance_context(
        PolicyComplianceResult(
            status=ComplianceStatus.PARTIALLY_COMPLIANT,
            satisfied_policies=["POL-002"],
            undetermined_policies=["PD.11"],
            not_applicable_policies=["XFER-01"],
            rationale="r",
        )
    )

    assert "PD.11" in context
    assert "information gaps, not violations" in context
    assert "XFER-01" in context


# --- status must agree with the findings behind it -------------------------
# A live run returned non_compliant while listing no violated policy at all.
# Same deterministic auto-correction pattern as risk_agent._reconcile_with_bands.


def test_a_violation_forces_non_compliant(monkeypatch):
    result = _check(monkeypatch, status="compliant", violated_policies=["POL-003"])

    assert result.status is ComplianceStatus.NON_COMPLIANT
    assert "auto-adjusted" in result.rationale


def test_outstanding_gaps_force_partially_compliant(monkeypatch):
    result = _check(
        monkeypatch, status="non_compliant", undetermined_policies=["PD.11", "DM.2"]
    )

    assert result.status is ComplianceStatus.PARTIALLY_COMPLIANT
    assert "0 violated" in result.rationale


def test_all_satisfied_forces_compliant(monkeypatch):
    result = _check(monkeypatch, status="non_compliant", satisfied_policies=["POL-002"])

    assert result.status is ComplianceStatus.COMPLIANT


def test_a_consistent_status_is_left_alone(monkeypatch):
    result = _check(
        monkeypatch,
        status="non_compliant",
        violated_policies=["POL-003"],
        undetermined_policies=["PD.11"],
    )

    assert result.status is ComplianceStatus.NON_COMPLIANT
    assert result.rationale == "r"


def test_nothing_assessed_is_not_reconciled(monkeypatch):
    """With no findings at all there is nothing to reconcile against, so the
    model's own status stands rather than being talked up or down."""
    result = _check(monkeypatch, status="non_compliant")

    assert result.status is ComplianceStatus.NON_COMPLIANT
    assert result.rationale == "r"


def test_reconciliation_cannot_invent_a_violation(monkeypatch):
    result = _check(monkeypatch, status="compliant", undetermined_policies=["PD.11"])

    assert result.violated_policies == []
    assert result.status is ComplianceStatus.PARTIALLY_COMPLIANT
