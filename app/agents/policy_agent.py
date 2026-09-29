"""Policy Compliance Agent.

Checks a submitted AI use case (with its already-computed risk assessment)
against the organizational policy repository and reports which policies are
satisfied vs. violated.

Policy ids returned by the model are validated against the repository in
`check`: an id that does not exist is dropped and logged, so a fabricated
"POL-999" can never reach the report or the UI.

This agent does NOT rank or select SDAIA evidence. An earlier experiment
had it choose among retrieved passages; it was evaluated
(data/evaluation/results/policy_evidence_en50*) and rejected, and evidence
selection is left to a future dedicated stage. The retrieval and
citation-resolution utilities remain in app/rag/evidence.py for that work.
"""
import logging
from typing import Optional

from app.agents.base import BaseAgent
from app.models import (
    AIUseCase,
    EvidenceCitation,
    ComplianceStatus,
    PolicyComplianceResult,
    RiskAssessmentResult,
)
from app.prompts import build_evidence_context, build_risk_context, build_use_case_brief
from app.rag.evidence import build_evidence_query, resolve_citations, retrieve_evidence
from app.tools.document_analysis import ANALYZE_DOCUMENT_SCHEMA, analyze_document
from app.tools.policy_repository import (
    GET_POLICIES_SCHEMA,
    SEARCH_POLICIES_SCHEMA,
    get_policies,
    policy_ids,
    search_policies,
)

SYSTEM_PROMPT = """You are the Policy Compliance Agent inside a Multi-Agent \
AI Governance Platform. You receive an AI use case and its risk assessment \
(already completed by the Risk Assessment Agent). Your job is to check the \
use case against the organization's AI governance policies and report \
compliance.

RETRIEVING POLICIES
The repository holds around 140 rules from several different sources, \
and most of them have nothing to do with any one submission. Fetch the \
ones the submission's own facts implicate rather than pulling the whole \
repository: call get_policies with the `category` argument (its \
description lists the categories that exist) once per area the use case \
actually touches, and use search_policies for a specific topic. You may \
call analyze_document on the use case's documentation to check a factual \
claim (e.g. whether PII appears to be present).

APPLICABILITY COMES FIRST
Every policy carries an "applies_when" condition describing the facts \
that bring it into scope - for example "personal_data == true", \
"generative_ai == true and data_outside_ksa == true", or "always". \
Before judging a policy, decide whether the submission's facts meet its \
condition. If they do not, the policy is not applicable to this use \
case: put it in not_applicable_policies and do not judge it. Do not \
assume a fact you were not told in order to bring a policy into scope.

JUDGING AN APPLICABLE POLICY
Put each applicable policy in exactly one of three lists:
- satisfied_policies: the submission gives reasonable evidence that the \
policy's "requires" items are met. Name the part of the submission that \
shows it in your rationale.
- violated_policies: the submission gives reasonable evidence of a \
conflict with the policy - it describes something the policy forbids, or \
states that a control the policy requires is absent or deliberately \
omitted.
- undetermined_policies: the policy applies and the submission neither \
shows the requirement met nor shows a conflict.

A missing fact is not a violation. If the submission is simply silent \
about a requirement, that policy is undetermined, not violated. Many \
policies ask for documented evidence - assessments, inventories, \
sign-offs, test records - that a short intake form will not contain; \
undetermined is the correct and honest answer for those. The Decision \
Agent carries those gaps forward, so reporting them accurately does not \
let anything through unchecked. Equally, silence is never evidence of \
compliance: never put a policy in satisfied_policies merely because \
nothing contradicts it.

STATUS
Set status from your own findings:
- non_compliant: at least one applicable policy is violated.
- partially_compliant: nothing is violated, but applicable policies \
remain undetermined.
- compliant: every applicable policy is satisfied.

Return real policy IDs from the repository (e.g. "POL-003", "PD.11") and \
a rationale that explains the applicability calls you made and the \
evidence behind each violated or satisfied finding."""


logger = logging.getLogger("governai.agents.policy")


class PolicyComplianceAgent(BaseAgent):
    name = "policy_compliance_agent"

    def __init__(self, model: Optional[str] = None):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            tools=[GET_POLICIES_SCHEMA, SEARCH_POLICIES_SCHEMA, ANALYZE_DOCUMENT_SCHEMA],
            tool_functions={
                "get_policies": get_policies,
                "search_policies": search_policies,
                "analyze_document": analyze_document,
            },
            model=model,
        )

    CITATION_INSTRUCTION = (
        "\n\nThe passages above are retrieved SDAIA source material. If any "
        "of them supports a finding you make, list the ids of the ones you "
        "actually used in evidence_ids (for example [\"E2\"]). Cite only ids "
        "shown above, cite nothing if none applies, and do not write out "
        "source names or page numbers yourself - they are resolved from the "
        "retrieval record."
    )

    def check(self, use_case: AIUseCase, risk: RiskAssessmentResult) -> PolicyComplianceResult:
        # Retrieved SDAIA evidence, when retrieval is available. Retrieval is
        # best-effort: retrieve_evidence returns [] if the index or the
        # embedding API is unavailable, and the check then runs exactly as it
        # did before, so a retrieval outage cannot fail a submission.
        candidates = retrieve_evidence(build_evidence_query(use_case, risk))

        message = f"{build_use_case_brief(use_case)}\n\n{build_risk_context(risk)}"
        if candidates:
            message += f"\n\n{build_evidence_context(candidates)}{self.CITATION_INSTRUCTION}"

        result = self.run(message, PolicyComplianceResult)
        result = self._validate_policy_ids(result, use_case)
        result = _reconcile_status(result)

        # Source and page come from the retrieval record, never from the model,
        # so an invented citation cannot reach the report; unknown ids are
        # dropped and logged by resolve_citations.
        # model_copy(update=...) skips validation, so the citations are built
        # into EvidenceCitation instances explicitly rather than left as dicts.
        citations, _rejected = resolve_citations(result.evidence_ids, candidates)
        return result.model_copy(
            update={"evidence": [EvidenceCitation(**citation) for citation in citations]}
        )

    # Order matters: a policy the model listed twice is kept in the most
    # conservative list it appeared in, so a "violated" claim is never
    # downgraded by a stray duplicate further down.
    _DETERMINATION_FIELDS = (
        "violated_policies",
        "satisfied_policies",
        "undetermined_policies",
        "not_applicable_policies",
    )

    def _validate_policy_ids(
        self,
        result: PolicyComplianceResult,
        use_case: AIUseCase,
    ) -> PolicyComplianceResult:
        """Drop policy ids that do not exist in the repository.

        The model chooses which policies it cites, so a hallucinated id is
        possible; it must not reach the report or the UI. Valid ids keep
        their order, a policy is kept in only one determination list (see
        `_DETERMINATION_FIELDS`), and everything rejected is logged with
        the use case it came from.
        """
        known = policy_ids()
        cleaned = {}
        rejected = {}
        duplicated = {}
        claimed = set()

        for field in self._DETERMINATION_FIELDS:
            kept, dropped, repeated = [], [], []

            for policy_id in getattr(result, field):
                if policy_id in claimed:
                    repeated.append(policy_id)
                    continue

                claimed.add(policy_id)
                (kept if policy_id in known else dropped).append(policy_id)

            cleaned[field] = kept

            if dropped:
                rejected[field] = dropped
            if repeated:
                duplicated[field] = repeated

        if rejected:
            logger.warning(
                "Policy Compliance Agent returned policy ids that are not in the "
                "repository for use case %s and they were dropped: %s",
                use_case.id,
                "; ".join(f"{field}={ids}" for field, ids in rejected.items()),
            )
        if duplicated:
            logger.warning(
                "Policy Compliance Agent listed the same policy in more than one "
                "determination for use case %s; the first (most conservative) "
                "listing was kept: %s",
                use_case.id,
                "; ".join(f"{field}={ids}" for field, ids in duplicated.items()),
            )

        return result.model_copy(update=cleaned)


def _reconcile_status(result: PolicyComplianceResult) -> PolicyComplianceResult:
    """Ensure `status` agrees with the determinations behind it.

    The prompt states the rule - violated means non_compliant, outstanding
    undetermined policies mean partially_compliant, all-satisfied means
    compliant - but a model sometimes picks a status its own lists do not
    support. A live run returned non_compliant while listing no violated
    policy at all. A compliance verdict that contradicts its own findings is
    worse than a clearly-labeled deterministic correction, so this applies
    the same auto-correction pattern as risk_agent._reconcile_with_bands.

    The correction only ever follows the evidence: it cannot invent a
    violation, and a case with nothing assessed at all is left as the model
    reported it.
    """
    if result.violated_policies:
        expected = ComplianceStatus.NON_COMPLIANT
    elif result.undetermined_policies:
        expected = ComplianceStatus.PARTIALLY_COMPLIANT
    elif result.satisfied_policies:
        expected = ComplianceStatus.COMPLIANT
    else:
        # Nothing was assessed either way; there is no finding to reconcile
        # against, so the model's own status stands.
        return result

    if expected is result.status:
        return result

    note = (
        f"\n\n(Note: status auto-adjusted from '{result.status.value}' to "
        f"'{expected.value}' to stay consistent with the findings: "
        f"{len(result.violated_policies)} violated, "
        f"{len(result.satisfied_policies)} satisfied, "
        f"{len(result.undetermined_policies)} undetermined.)"
    )
    logger.warning(
        "Policy compliance status auto-adjusted from %s to %s: "
        "%d violated, %d satisfied, %d undetermined.",
        result.status.value,
        expected.value,
        len(result.violated_policies),
        len(result.satisfied_policies),
        len(result.undetermined_policies),
    )
    return result.model_copy(
        update={"status": expected, "rationale": result.rationale + note}
    )
