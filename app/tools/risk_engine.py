"""Deterministic risk detection and scoring engine.

Implements Part A of the SDAIA-derived governance library: detects which
named risks (data/risk_rules.yaml) apply to a submitted use case, scores
each with likelihood x impact, and rolls that up into one overall risk
level/score. This runs in plain Python precisely so the LLM cannot invent
the number - see the library's guidance: "Do not let the LLM invent the
risk level alone: use deterministic scoring rules for likelihood/impact
and use the LLM mainly to extract facts, explain findings."

Likelihood is derived from how a risk was triggered rather than hand-tuned
per risk: a matched structured field (e.g. personal_data=true) is treated
as more certain than a free-text keyword match, and a risk matched both
ways is more certain still.
"""
from typing import List, Tuple

from app.models import AIUseCase, DetectedRisk, RiskLevel
from app.tools.risk_rules import _load_risk_rules

_LIKELIHOOD_KEYWORD_ONLY = 3
_LIKELIHOOD_FIELD_ONLY = 4
_LIKELIHOOD_BOTH = 5


def _use_case_text(use_case: AIUseCase) -> str:
    parts = [
        use_case.description,
        use_case.documentation or "",
        use_case.deployment_context or "",
        use_case.autonomy_level or "",
        use_case.data_classification or "",
    ]
    return " ".join(parts).lower()


def detect_risks(use_case: AIUseCase) -> List[DetectedRisk]:
    """Deterministically detect which library risks apply, and score each."""
    library = _load_risk_rules().get("risk_library", [])
    text = _use_case_text(use_case)
    detected: List[DetectedRisk] = []

    for risk in library:
        trigger_fields = risk.get("trigger_fields", [])
        # All listed fields must hold together (e.g. R-33 needs both
        # generative_ai AND external_provider) - matches the AND semantics
        # used by the control router's applies_when lists.
        field_hit = bool(trigger_fields) and all(getattr(use_case, field, False) for field in trigger_fields)
        keyword_hit = any(kw.lower() in text for kw in risk.get("trigger_keywords", []))

        if not (field_hit or keyword_hit):
            continue

        if field_hit and keyword_hit:
            likelihood = _LIKELIHOOD_BOTH
        elif field_hit:
            likelihood = _LIKELIHOOD_FIELD_ONLY
        else:
            likelihood = _LIKELIHOOD_KEYWORD_ONLY

        impact = risk["impact"]

        detected.append(
            DetectedRisk(
                risk_id=risk["id"],
                domain=risk["domain"],
                title=risk["title"],
                trigger=risk["trigger"],
                likelihood=likelihood,
                impact=impact,
                inherent_score=likelihood * impact,
                treatment=risk["recommended_treatment"],
                controls=risk.get("controls", []),
                source_refs=risk.get("source_refs", []),
            )
        )

    return detected


def _band(score: int, bands) -> RiskLevel:
    for band in bands:
        if score <= band["max_score"]:
            return RiskLevel(band["level"])
    return RiskLevel.CRITICAL


def compute_overall_risk(detected_risks: List[DetectedRisk]) -> Tuple[RiskLevel, int]:
    """Roll a risk register up into one overall level/score (0-100).

    The single worst detected risk drives the base score (its
    inherent_score, out of the maximum possible 25, scaled to 100) -
    matching standard risk-register practice where the most severe
    finding governs the overall classification. Each additional
    triggered risk adds a small, capped compounding bump, reflecting that
    several risk factors appearing together raise the overall risk beyond
    any single one of them (as in the library's worked examples).
    """
    bands = _load_risk_rules().get("scoring", {}).get("bands", [])

    if not detected_risks:
        return _band(0, bands), 0

    max_inherent = max(r.inherent_score for r in detected_risks)
    base_score = round((max_inherent / 25) * 100)
    compounding = min(10, 3 * max(0, len(detected_risks) - 1))
    score = min(100, base_score + compounding)

    return _band(score, bands), score
