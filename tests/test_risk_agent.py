import json
from types import SimpleNamespace

from app.agents.risk_agent import RiskAssessmentAgent, _reconcile_with_bands, _use_case_corpus
from app.models import AIUseCase, RiskAssessmentResult, RiskLevel, SuggestedRiskRule
from app.tools.risk_rules import get_baseline_risk_score

# `fake_supabase` (tests/conftest.py, autouse) seeds the risk_rules table
# from data/risk_rules.yaml before every test, so get_baseline_risk_score
# below is exercised against the same rules the platform ships with.


def _use_case(**overrides) -> AIUseCase:
    defaults = dict(
        name="Automated Loan Denial Assistant",
        description="Fully autonomous agent approving or denying loans with no human review.",
        owner="lending-team",
    )
    defaults.update(overrides)
    return AIUseCase(**defaults)


# --- get_baseline_risk_score ------------------------------------------------


def test_get_baseline_risk_score_matches_expected_rules():
    text = (
        "This agent is fully autonomous, has no human review, and can "
        "delete records. It also handles health data."
    )
    result = get_baseline_risk_score(text)
    matched_ids = {m["id"] for m in result["matched_rules"]}

    assert "RISK-002" in matched_ids  # autonomy keywords
    assert "RISK-001" in matched_ids  # sensitive data keywords
    assert "RISK-006" in matched_ids  # system access keywords
    # Several rules legitimately overlap on this text (e.g. RISK-001 and R-02
    # both key off "health"), so the raw weight sum exceeds 100 - suggested_score
    # is the capped version of that sum.
    raw_total = sum(m["weight"] for m in result["matched_rules"])
    assert result["suggested_score"] == min(raw_total, 100)
    assert result["suggested_level"] in {"low", "medium", "high", "critical"}


def test_get_baseline_risk_score_handles_empty_text():
    result = get_baseline_risk_score("")
    assert result["matched_rules"] == []
    assert result["category_breakdown"] == []
    assert result["suggested_score"] == 0
    assert result["suggested_level"] == "low"
    assert result["rules_requiring_judgment"]  # static, independent of the text


def test_get_baseline_risk_score_always_surfaces_rules_requiring_judgment():
    # RISK-008 has no trigger_keywords, so it can never be scored - but it must
    # still reach the agent rather than being silently skipped.
    result = get_baseline_risk_score("Internal meeting notes summarizer.")
    by_id = {r["id"]: r for r in result["rules_requiring_judgment"]}
    assert "RISK-008" in by_id
    assert by_id["RISK-008"]["condition"]
    assert "RISK-008" not in {m["id"] for m in result["matched_rules"]}


def test_get_baseline_risk_score_category_breakdown_merges_alias_categories():
    # "health" fires RISK-001 (data_sensitivity, 25) and R-02 (data, 20): the
    # same underlying fact under two labels, merged into one bucket.
    result = get_baseline_risk_score("This agent is fully autonomous and processes health data.")
    breakdown = {b["category"]: b["weight"] for b in result["category_breakdown"]}

    assert breakdown["data_sensitivity"] == 45
    assert "data" not in breakdown
    assert breakdown["autonomy"] == 25
    weights = [b["weight"] for b in result["category_breakdown"]]
    assert weights == sorted(weights, reverse=True)
    # The breakdown is diagnostic only: it always accounts for the raw total.
    assert sum(weights) == sum(m["weight"] for m in result["matched_rules"])


def test_get_baseline_risk_score_caps_at_100():
    # Stack enough high-weight keywords to exceed 100 before capping.
    text = (
        "fully autonomous, no human review, hiring decision, lending, "
        "delete records, write access, admin access, customer-facing, "
        "millions of users, health data, biometric, ssn"
    )
    result = get_baseline_risk_score(text)
    assert result["suggested_score"] <= 100


# --- use case corpus / band reconciliation ----------------------------------


def test_use_case_corpus_includes_all_free_text_fields():
    use_case = _use_case(documentation="Uses biometric data for verification.")
    corpus = _use_case_corpus(use_case)
    assert "Automated Loan Denial Assistant" in corpus
    assert "biometric data" in corpus


def test_reconcile_with_bands_leaves_consistent_result_untouched():
    result = RiskAssessmentResult(
        risk_level=RiskLevel.HIGH, risk_score=60, risk_factors=["x"], rationale="because"
    )
    reconciled = _reconcile_with_bands(result)
    assert reconciled is result


def test_reconcile_with_bands_corrects_mismatched_level():
    result = RiskAssessmentResult(
        risk_level=RiskLevel.LOW, risk_score=90, risk_factors=["x"], rationale="because"
    )
    reconciled = _reconcile_with_bands(result)
    assert reconciled.risk_level == RiskLevel.CRITICAL
    assert "auto-adjusted" in reconciled.rationale
    assert reconciled.rationale.startswith("because")


# --- full assess() flow against a fake OpenAI client ------------------------


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments))
    )


def _completion(content="", tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """Same fake OpenAI-compatible client pattern used in test_base_agent.py."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

        class _Completions:
            def create(inner_self, **kwargs):
                self.calls.append(kwargs)
                return self._responses.pop(0)

        self.chat = SimpleNamespace(completions=_Completions())


def test_assess_injects_deterministic_signals_and_reconciles_result(monkeypatch):
    # The model deliberately returns a level ("medium") inconsistent with its
    # own score (90) to prove the reconciliation step runs end-to-end.
    final = json.dumps(
        {
            "risk_level": "medium",
            "risk_score": 90,
            "risk_factors": ["full autonomy", "health data", "no human oversight"],
            "rationale": "high autonomy, sensitive data, no human oversight",
        }
    )
    fake_client = FakeClient([_completion(content=final)])
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    use_case = _use_case(
        documentation="Processes health records and can delete them without review.",
        autonomy_level="fully-autonomous",
    )
    agent = RiskAssessmentAgent()
    result = agent.assess(use_case)

    assert isinstance(result, RiskAssessmentResult)
    assert result.risk_level == RiskLevel.CRITICAL  # corrected from the model's "medium"
    assert "auto-adjusted" in result.rationale

    sent_messages = fake_client.calls[0]["messages"]
    user_message = sent_messages[1]["content"]
    assert "Deterministic Pre-Analysis" in user_message
    assert "Baseline keyword/weight risk score" in user_message
    assert "PII indicators present" in user_message
    assert "Score contribution by category" in user_message
    assert "Rules requiring contextual judgment" in user_message
    assert "RISK-008" in user_message


def test_assess_still_supports_the_model_calling_tools(monkeypatch):
    final = json.dumps(
        {
            "risk_level": "low",
            "risk_score": 5,
            "risk_factors": ["minimal risk"],
            "rationale": "internal tool, no sensitive data, human reviewed",
        }
    )
    responses = [
        _completion(tool_calls=[_tool_call("call_1", "get_scoring_bands", {})]),
        _completion(content=final),
    ]
    fake_client = FakeClient(responses)
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    use_case = _use_case(
        name="Internal Meeting Notes Summarizer",
        description="Summarizes internal meeting notes for the team that requested them.",
    )
    agent = RiskAssessmentAgent()
    result = agent.assess(use_case)

    assert result.risk_level == RiskLevel.LOW
    assert len(fake_client.calls) == 2
    tool_messages = [m for m in fake_client.calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_messages  # the model's get_scoring_bands call was answered


def test_assess_passes_through_suggested_new_rules_without_affecting_score(monkeypatch):
    final = json.dumps(
        {
            "risk_level": "low",
            "risk_score": 10,
            "risk_factors": ["minimal risk"],
            "rationale": "internal tool, no sensitive data",
            "suggested_new_rules": [
                {
                    "title": "Synthetic voice cloning without consent",
                    "category": "biometric_synthesis",
                    "condition": "Generates a synthetic voice or likeness of a real person "
                    "without documented consent",
                    "suggested_weight": 20,
                    "rationale": "No existing rule covers consent for synthetic likeness.",
                }
            ],
        }
    )
    fake_client = FakeClient([_completion(content=final)])
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    result = RiskAssessmentAgent().assess(_use_case())

    assert result.risk_score == 10
    assert result.risk_level == RiskLevel.LOW
    (suggestion,) = result.suggested_new_rules
    assert isinstance(suggestion, SuggestedRiskRule)
    assert suggestion.suggested_weight == 20


def test_assess_still_validates_when_model_omits_suggested_new_rules(monkeypatch):
    final = json.dumps(
        {
            "risk_level": "low",
            "risk_score": 5,
            "risk_factors": ["minimal risk"],
            "rationale": "nothing new to suggest",
        }
    )
    fake_client = FakeClient([_completion(content=final)])
    monkeypatch.setattr("app.agents.base.get_client", lambda: fake_client)

    result = RiskAssessmentAgent().assess(_use_case())

    assert result.suggested_new_rules == []
