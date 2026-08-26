from app.models import AIUseCase
from app.tools.audit_log import get_audit_log, log_event
from app.tools.control_router import route_controls
from app.tools.document_analysis import analyze_document
from app.tools.policy_repository import get_policies, search_policies
from app.tools.risk_engine import compute_overall_risk, detect_risks
from app.tools.risk_rules import get_risk_rules, get_scoring_bands


def _use_case(**overrides) -> AIUseCase:
    defaults = dict(name="Test Use Case", description="desc", owner="team-x")
    defaults.update(overrides)
    return AIUseCase(**defaults)


# --- control library / router -----------------------------------------------


def test_get_policies_returns_full_library_by_default():
    policies = get_policies()
    assert len(policies) >= 131
    assert any(p["id"] == "PD.1" for p in policies)
    assert any(p["id"] == "PDPL-09" for p in policies)
    assert any(p["id"] == "XFER-04" for p in policies)
    assert any(p["id"] == "GEN-07" for p in policies)


def test_get_policies_filters_by_module():
    pdpl_only = get_policies(module="PDPL")
    assert pdpl_only
    assert all(p["module"] == "PDPL" for p in pdpl_only)


def test_get_policies_filters_critical_only():
    critical = get_policies(critical_only=True)
    all_policies = get_policies()
    assert critical
    assert all(p["critical"] for p in critical)
    assert len(critical) < len(all_policies)


def test_search_policies_matches_keyword():
    results = search_policies("bias")
    assert any(p["id"] == "BV.9" for p in results)


def test_route_controls_base_only_for_minimal_use_case():
    use_case = _use_case()
    routed = route_controls(use_case)
    modules = {c["module"] for c in routed}
    # Base AI-Ethics (pre-deployment phases) always applies; nothing else
    # should be routed in without personal_data/generative_ai/etc.
    assert modules == {"AI_ETHICS"}
    assert all(c["phase"] != "deploy_monitor" for c in routed)


def test_route_controls_adds_pdpl_when_personal_data():
    use_case = _use_case(personal_data=True)
    routed = route_controls(use_case)
    ids = {c["id"] for c in routed}
    assert "PDPL-09" in ids
    assert "XFER-04" not in ids  # no data_outside_ksa yet


def test_route_controls_adds_transfer_only_with_both_conditions():
    use_case = _use_case(personal_data=True, data_outside_ksa=True)
    routed = route_controls(use_case)
    ids = {c["id"] for c in routed}
    assert "XFER-04" in ids
    assert "PDPL-09" in ids


def test_route_controls_adds_genai_and_deploy_monitor():
    use_case = _use_case(generative_ai=True, deployment_status="production")
    routed = route_controls(use_case)
    ids = {c["id"] for c in routed}
    assert "GEN-07" in ids
    assert "DM.1" in ids  # deploy_monitor now applies


# --- risk library / deterministic engine ------------------------------------


def test_get_risk_rules_and_bands():
    rules = get_risk_rules()
    assert len(rules) >= 35
    assert any(r["id"] == "R-09" for r in rules)
    bands = get_scoring_bands()
    levels = {b["level"] for b in bands}
    assert levels == {"low", "medium", "high", "critical"}


def test_detect_risks_empty_for_minimal_use_case():
    use_case = _use_case()
    assert detect_risks(use_case) == []
    level, score = compute_overall_risk([])
    assert level.value == "low"
    assert score == 0


def test_detect_risks_field_trigger_beats_keyword_only():
    field_triggered = _use_case(personal_data=True)
    [risk] = [r for r in detect_risks(field_triggered) if r.risk_id == "R-01"]
    assert risk.likelihood == 4  # structured field match

    keyword_triggered = _use_case(description="this system processes personal data about customers")
    [risk2] = [r for r in detect_risks(keyword_triggered) if r.risk_id == "R-01"]
    assert risk2.likelihood == 3  # keyword-only match


def test_detect_risks_is_deterministic_and_reproducible():
    use_case = _use_case(personal_data=True, sensitive_data=True, high_impact_decision=True)
    first = detect_risks(use_case)
    second = detect_risks(use_case)
    assert [r.risk_id for r in first] == [r.risk_id for r in second]
    assert any(r.risk_id == "R-02" for r in first)  # sensitive-data misuse
    assert any(r.risk_id == "R-09" for r in first)  # bias/discrimination


def test_compute_overall_risk_multiple_risks_outrank_single_risk():
    single = [r for r in detect_risks(_use_case(personal_data=True)) if r.risk_id == "R-01"]
    combined = detect_risks(_use_case(personal_data=True, sensitive_data=True, high_impact_decision=True))

    _, single_score = compute_overall_risk(single)
    _, combined_score = compute_overall_risk(combined)
    assert combined_score >= single_score


def test_analyze_document_detects_pii_and_keywords():
    text = "Contact jane@example.com. SSN 123-45-6789. This involves health and biometric data."
    result = analyze_document(text)
    assert result["emails_found"] == 1
    assert result["ssn_like_patterns_found"] == 1
    assert result["contains_pii_indicators"] is True
    assert "health" in result["sensitive_keywords_found"]
    assert "biometric" in result["sensitive_keywords_found"]


def test_analyze_document_handles_empty_text():
    result = analyze_document("")
    assert result["word_count"] == 0
    assert result["contains_pii_indicators"] is False


def test_audit_log_round_trip():
    entry = log_event("uc-1", "intake", "system", {"foo": "bar"})
    assert entry.use_case_id == "uc-1"

    log_event("uc-2", "intake", "system", {})

    all_entries = get_audit_log()
    assert len(all_entries) == 2

    uc1_entries = get_audit_log("uc-1")
    assert len(uc1_entries) == 1
    assert uc1_entries[0]["data"]["foo"] == "bar"
