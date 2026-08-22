from app.tools.audit_log import get_audit_log, log_event
from app.tools.document_analysis import analyze_document
from app.tools.policy_repository import get_policies, search_policies
from app.tools.risk_rules import get_risk_rules, get_scoring_bands


def test_get_policies_returns_all_by_default():
    policies = get_policies()
    assert len(policies) >= 8
    assert any(p["id"] == "POL-003" for p in policies)


def test_get_policies_filters_by_category():
    policies = get_policies(category="human_oversight")
    assert policies
    assert all(p["category"] == "human_oversight" for p in policies)


def test_get_policies_filters_by_min_risk_level():
    low_only = get_policies(min_risk_level="low")
    assert all(p.get("min_risk_level", "low") == "low" for p in low_only)
    all_policies = get_policies()
    assert len(low_only) < len(all_policies)


def test_search_policies_matches_keyword():
    results = search_policies("encryption")
    assert any(p["id"] == "POL-002" for p in results)


def test_get_risk_rules_and_bands():
    rules = get_risk_rules()
    assert any(r["id"] == "RISK-002" for r in rules)
    bands = get_scoring_bands()
    levels = {b["level"] for b in bands}
    assert levels == {"low", "medium", "high", "critical"}


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
