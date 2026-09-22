"""Review Agent.

An independent second opinion on the governance pipeline's output. It reads
the use case together with the completed Risk Assessment, Policy Compliance
and Decision results and either signs off on them or sends the Decision
back for revision with concrete feedback (see the review loop in
app.orchestrator.GovernanceOrchestrator.run).

It plays the "reviewer" half of a generator -> reviewer -> revise loop: it
critiques for accuracy (are the findings consistent and grounded in the
submission and the policy repository?), balance (is the recommendation
neither too lenient nor needlessly restrictive?) and practicality (are the
conditions concrete enough to act on?). It only reviews - it never edits
the other agents' output itself.
"""
from typing import Optional

from app.agents.base import BaseAgent
from app.models import (
    AIUseCase,
    DecisionResult,
    PolicyComplianceResult,
    ReviewResult,
    RiskAssessmentResult,
)
from app.prompts import (
    build_compliance_context,
    build_decision_context,
    build_risk_context,
    build_use_case_brief,
)
from app.tools.policy_repository import GET_POLICIES_SCHEMA, get_policies

SYSTEM_PROMPT = """You are the Review Agent inside a Multi-Agent AI \
Governance Platform. The Risk Assessment, Policy Compliance and Decision \
agents have already analyzed an AI use case. You are an independent, \
critical second reader: your job is to catch mistakes in their output before \
it becomes the platform's recommendation. You review; you do not redo the \
analysis from scratch.

Check four things:
1. Accuracy - Are the findings consistent with each other and grounded in \
the submission? Examples of problems: a decision that contradicts the risk \
level or compliance status (e.g. approve while non-compliant, or block a \
low-risk compliant use case), a compliance status that disagrees with its \
own violated/satisfied policy lists, claims in a rationale the submission \
does not support, or something the submission states that the findings \
ignore. Policy IDs cited as violated or satisfied must really exist - call \
get_policies to verify them and to check what a policy actually requires. \
Unverified claims must be treated conservatively, never assumed compliant.
2. Balance - Is the recommendation proportionate to the risk? Flag both a \
recommendation that is too lenient for the risk and one that is needlessly \
restrictive.
3. Practicality - Are the decision's conditions concrete, actionable, and \
tied to the violated policies (not vague advice like "improve security")? \
Is anything required missing from them?
4. Consistency - If the message includes "Relevant Past Cases", compare the \
decision with them. A close precedent that was decided differently (or that \
a human reviewer rejected) needs a stated reason in the decision's \
rationale; flag an unexplained inconsistency. Precedent informs the review, \
it does not override the facts of this submission.

Verdict:
- "approved": the recommendation is sound and could be acted on as it \
stands. You may still list non-blocking suggestions.
- "needs_revision": there is at least one substantive problem. List each in \
"issues" as a specific, concrete statement of what is wrong and why, and \
give the matching fix in "suggestions". Do not flag style, wording or \
preference - only problems that would change the decision, its conditions, \
or the trustworthiness of its rationale.

Your feedback is applied by re-running only the Decision Agent, so phrase \
issues in terms of the decision, its conditions and its rationale. If you \
believe an upstream risk or compliance finding is wrong, name the finding \
and say why; the Decision Agent will weigh that conservatively."""


class ReviewAgent(BaseAgent):
    name = "review_agent"

    def __init__(self, model: Optional[str] = None):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            tools=[GET_POLICIES_SCHEMA],
            tool_functions={"get_policies": get_policies},
            model=model,
        )

    def review(
        self,
        use_case: AIUseCase,
        risk: RiskAssessmentResult,
        compliance: PolicyComplianceResult,
        decision: DecisionResult,
    ) -> ReviewResult:
        message = (
            f"{build_use_case_brief(use_case)}\n\n"
            f"{build_risk_context(risk)}\n\n"
            f"{build_compliance_context(compliance)}\n\n"
            f"{build_decision_context(decision)}"
        )
        return self.run(message, ReviewResult)
