from app.models import (
    ComplianceStatus,
    Decision,
    DecisionResult,
    PolicyComplianceResult,
    ReviewResult,
    ReviewVerdict,
    RiskAssessmentResult,
    RiskLevel,
)
from app.tools.coordination_diagnostics import diagnose_coordination


def _risk(level=RiskLevel.LOW, factors=None) -> RiskAssessmentResult:
    return RiskAssessmentResult(
        risk_level=level, risk_score=10, risk_factors=factors or [], rationale="r"
    )


def _compliance(status=ComplianceStatus.COMPLIANT, violated=None, satisfied=None) -> PolicyComplianceResult:
    return PolicyComplianceResult(
        status=status,
        violated_policies=violated or [],
        satisfied_policies=satisfied or [],
        rationale="r",
    )


def _decision(decision=Decision.APPROVE, conditions=None) -> DecisionResult:
    return DecisionResult(decision=decision, conditions=conditions or [], rationale="r")


def _review(verdict=ReviewVerdict.APPROVED) -> ReviewResult:
    return ReviewResult(verdict=verdict, rationale="r")


def test_clean_run_has_no_issues():
    risk, compliance, decision = _risk(), _compliance(), _decision()

    issues = diagnose_coordination(risk, compliance, decision, [decision], _review(), False)

    assert issues == []


def test_flags_approve_of_a_non_compliant_use_case():
    risk, compliance = _risk(), _compliance(status=ComplianceStatus.NON_COMPLIANT)
    decision = _decision(Decision.APPROVE)

    issues = diagnose_coordination(risk, compliance, decision, [decision], _review(), False)

    assert any(i.code == "CONFLICTING_CONCLUSIONS" for i in issues)


def test_flags_approve_of_a_critical_risk_use_case():
    risk, compliance = _risk(level=RiskLevel.CRITICAL), _compliance()
    decision = _decision(Decision.APPROVE)

    issues = diagnose_coordination(risk, compliance, decision, [decision], _review(), False)

    assert any(i.code == "CONFLICTING_CONCLUSIONS" for i in issues)


def test_flags_block_of_a_low_risk_compliant_use_case():
    risk, compliance = _risk(level=RiskLevel.LOW), _compliance(status=ComplianceStatus.COMPLIANT)
    decision = _decision(Decision.BLOCK)

    issues = diagnose_coordination(risk, compliance, decision, [decision], _review(), False)

    assert any(i.code == "CONFLICTING_CONCLUSIONS" for i in issues)


def test_does_not_flag_a_block_that_has_a_real_reason():
    # High risk (or non-compliant) justifies a block - not a conflict.
    risk, compliance = _risk(level=RiskLevel.HIGH), _compliance(status=ComplianceStatus.COMPLIANT)
    decision = _decision(Decision.BLOCK)

    issues = diagnose_coordination(risk, compliance, decision, [decision], _review(), False)

    assert issues == []


def test_flags_a_policy_marked_both_violated_and_satisfied():
    risk = _risk()
    compliance = _compliance(violated=["POL-001"], satisfied=["POL-001", "POL-002"])
    decision = _decision()

    issues = diagnose_coordination(risk, compliance, decision, [decision], _review(), False)

    inconsistent = [i for i in issues if i.code == "INCONSISTENT_FINDINGS"]
    assert len(inconsistent) == 1
    assert "POL-001" in inconsistent[0].description


def test_flags_duplicate_risk_factors():
    risk = _risk(factors=["weak access controls", "Weak Access Controls"])
    compliance = _compliance()
    decision = _decision()

    issues = diagnose_coordination(risk, compliance, decision, [decision], _review(), False)

    assert any(i.code == "REDUNDANT_WORK" for i in issues)


def test_flags_a_revision_that_repeats_the_previous_decision():
    risk, compliance = _risk(), _compliance()
    first = _decision(Decision.REQUIRE_HUMAN_APPROVAL, conditions=["add human review"])
    repeated = _decision(Decision.REQUIRE_HUMAN_APPROVAL, conditions=["add human review"])

    issues = diagnose_coordination(risk, compliance, repeated, [first, repeated], _review(), False)

    assert any(i.code == "REDUNDANT_WORK" for i in issues)


def test_does_not_flag_a_revision_that_actually_changed():
    risk, compliance = _risk(), _compliance()
    first = _decision(Decision.APPROVE)
    revised = _decision(Decision.REQUIRE_HUMAN_APPROVAL, conditions=["add human review"])

    issues = diagnose_coordination(risk, compliance, revised, [first, revised], _review(), False)

    assert not any(i.code == "REDUNDANT_WORK" for i in issues)


def test_flags_unresolved_review_when_the_revision_budget_ran_out():
    risk, compliance, decision = _risk(), _compliance(), _decision()

    issues = diagnose_coordination(
        risk, compliance, decision, [decision], _review(ReviewVerdict.NEEDS_REVISION), True
    )

    assert any(i.code == "UNRESOLVED_COORDINATION" for i in issues)


def test_does_not_flag_unresolved_review_when_still_within_budget():
    """A NEEDS_REVISION verdict mid-loop is normal - only flag it once the
    revision budget has actually run out."""
    risk, compliance, decision = _risk(), _compliance(), _decision()

    issues = diagnose_coordination(
        risk, compliance, decision, [decision], _review(ReviewVerdict.NEEDS_REVISION), False
    )

    assert not any(i.code == "UNRESOLVED_COORDINATION" for i in issues)
