from app.agents.decision_agent import deterministic_decision
from app.models import ComplianceStatus, ControlEvaluation, ControlResultStatus, Decision, PolicyComplianceResult, RiskLevel


def _compliance(**evaluations_kwargs) -> PolicyComplianceResult:
    evaluations = [ControlEvaluation(**kwargs) for kwargs in evaluations_kwargs.pop("evaluations", [])]
    violated = [e.control_id for e in evaluations if e.result == ControlResultStatus.FAIL]
    review = [e.control_id for e in evaluations if e.result == ControlResultStatus.REVIEW]
    satisfied = [e.control_id for e in evaluations if e.result == ControlResultStatus.PASS]
    status = (
        ComplianceStatus.COMPLIANT
        if not violated and not review
        else ComplianceStatus.NON_COMPLIANT
        if violated
        else ComplianceStatus.PARTIALLY_COMPLIANT
    )
    return PolicyComplianceResult(
        status=status,
        violated_policies=violated,
        satisfied_policies=satisfied,
        review_policies=review,
        rationale="test",
        control_evaluations=evaluations,
    )


def _control(control_id: str, result: ControlResultStatus, critical: bool = False) -> dict:
    return dict(
        control_id=control_id,
        module="AI_ETHICS",
        principle="Test",
        control="test control",
        result=result,
        critical=critical,
    )


def test_critical_risk_always_blocks():
    compliance = _compliance(evaluations=[_control("PD.1", ControlResultStatus.PASS)])
    assert deterministic_decision(RiskLevel.CRITICAL, compliance) == Decision.BLOCK


def test_high_risk_with_critical_control_failure_blocks():
    compliance = _compliance(evaluations=[_control("BV.9", ControlResultStatus.FAIL, critical=True)])
    assert deterministic_decision(RiskLevel.HIGH, compliance) == Decision.BLOCK


def test_low_risk_with_critical_control_failure_requires_human_review():
    compliance = _compliance(evaluations=[_control("BV.9", ControlResultStatus.FAIL, critical=True)])
    assert deterministic_decision(RiskLevel.LOW, compliance) == Decision.REQUIRE_HUMAN_APPROVAL


def test_high_risk_with_clean_controls_requires_human_review():
    compliance = _compliance(evaluations=[_control("PD.1", ControlResultStatus.PASS)])
    assert deterministic_decision(RiskLevel.HIGH, compliance) == Decision.REQUIRE_HUMAN_APPROVAL


def test_any_failed_or_review_control_requires_human_review():
    compliance = _compliance(evaluations=[_control("PD.9", ControlResultStatus.REVIEW)])
    assert deterministic_decision(RiskLevel.MEDIUM, compliance) == Decision.REQUIRE_HUMAN_APPROVAL


def test_clean_low_or_medium_risk_approves():
    compliance = _compliance(evaluations=[_control("PD.1", ControlResultStatus.PASS)])
    assert deterministic_decision(RiskLevel.LOW, compliance) == Decision.APPROVE
    assert deterministic_decision(RiskLevel.MEDIUM, compliance) == Decision.APPROVE


def test_decision_is_deterministic_same_inputs_same_output():
    compliance = _compliance(evaluations=[_control("BV.9", ControlResultStatus.FAIL, critical=True)])
    results = {deterministic_decision(RiskLevel.HIGH, compliance) for _ in range(5)}
    assert results == {Decision.BLOCK}
