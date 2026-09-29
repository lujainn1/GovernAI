# governance_cases50_v1

Fifty AI governance submissions for end-to-end evaluation of the whole
pipeline (intake → risk → policy → decision → review → report), and of the
SDAIA retrieval that feeds the Policy Compliance Agent.

    file    data/evaluation/datasets/governance_cases50_v1.json
    cases   50
    sha256  7c94c25463456f4e97e61eeb900f59192e8dae36c445b53190e4bc22c9b0fec9

Written for this evaluation and not used during development of the agents,
prompts, chunking or retrieval, so it is unseen with respect to all of them.
Record the sha256 with any results, and add a `_v2` file rather than editing
this one — a changed dataset silently invalidates every number measured
against it.

## Coverage

16 domains: healthcare (3), banking (4), government (4), hr (4), education (4),
customer_support (4), cybersecurity (4), retail (4), manufacturing (3),
smart_cities (4), internal_productivity (4), cross_border (3),
personal_data (2), public_information (1), high_risk (1), low_risk (1).

Autonomy: human-on-the-loop 27, human-in-the-loop 14, fully-autonomous 9.
Data classification: personal data 20, internal 14, confidential 7, public 6,
restricted 3.

The set is deliberately balanced between submissions that *should* raise
findings and submissions that are well governed and should not. Roughly half
document real controls (a completed DPIA, measured accuracy, a signed
processing agreement, a working appeal route, in-Kingdom residency) so that
false positives are detectable; the other half state an absent control
explicitly so that false negatives are detectable. Without both halves a run
can only measure sensitivity, never precision.

## Fields

`id`, `domain`, `name`, `description`, `owner`, `data_classification`,
`deployment_context`, `autonomy_level`, `documentation` — the submission. These
are the only fields the pipeline may ever see; they map onto `AIUseCase`.

`expect` — an analyst prior, for scoring **after** the pipeline has answered:

- `risk` — risk levels a defensible assessment could return.
- `decision_not` — decisions that would be disproportionate for this
  submission. A decision in this list is a scoring failure.
- `should_flag` — concerns a competent reviewer would surface. If none of
  them appears in `violated_policies` or `undetermined_policies`, that is a
  candidate false negative.
- `should_not_flag` — controls the submission documents as present. A
  violation asserted against one of these is a candidate false positive.

**`expect` must never be passed to an agent, put in a prompt, or used to
select or re-rank retrieval.** It is ground truth for grading only. The two
halves of `should_flag` / `should_not_flag` are prose concepts, not policy
ids, so they locate cases worth reading rather than scoring themselves — the
final call on a false positive or negative is made by reading the case.
