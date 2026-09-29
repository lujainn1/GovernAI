# GovernAI – Demo Use Cases

Five realistic, Saudi-context use cases that walk through the platform's full
decision range, from a clear approval to a clear block. All organisations
named here are fictional.

| # | File | Domain | Expected risk | Expected decision |
|---|---|---|---|---|
| 1 | `01_meeting_minutes_summarizer.json` | Internal productivity | low | `approve` |
| 2 | `02_municipal_services_chatbot.json` | Government GenAI chatbot (RAG) | medium | `approve` with conditions (`require_human_approval` also acceptable) |
| 3 | `03_hr_cv_screening.json` + `03_hr_cv_screening_DPIA.docx` | HR / employment | high | `require_human_approval` (`block` also acceptable) |
| 4 | `04_deepfake_marketing_campaign.json` | Synthetic media / marketing | critical | `block` |
| 5 | `05_clinic_triage_chatbot_ar.json` (Arabic) | Healthcare | critical | `block` |

## How to run

CLI:
```bash
python -m app.cli submit examples/demo/01_meeting_minutes_summarizer.json
```

UI: copy the fields from the JSON into **Submit Use Case**. For case 3, also
upload `03_hr_cv_screening_DPIA.docx` (bilingual DPIA) to show the Document
Processing Agent.

## What each case demonstrates

**1 – Meeting summarizer (approve).** Controls are in place: human-in-the-loop,
input redaction, in-Kingdom hosting, signed DPA, AI labelling, audit log.
*Check:* no policy should be marked violated; the decision should be `approve`.

**2 – Municipal chatbot (approve with conditions).** A solid public GenAI
service with honest gaps: hallucination testing on only 150 questions, no
monitoring dashboard, prompt-injection testing not done, no accessibility
testing.
*Check:* satisfied policies include AI disclosure (GEN-08 / POL-005) and
in-Kingdom hosting. Conditions should mention GEN-05 (evaluation), GEN-12
(monitoring) and GEN-09 (security). This shows the agents weigh evidence
instead of blocking everything.

**3 – HR CV screening (human approval).** Mixed picture: encryption, RBAC,
audit log and retention are good, but applicants scoring below 40 are
rejected automatically, there is a documented 6-point gender gap, CVs are sent
to a model outside the Kingdom without a transfer assessment, and there is no
AI disclosure.
*Check:* expected violations are POL-003, POL-006 / GEN-06, POL-004 / PDPL-22,
XFER-02 / XFER-07, POL-005 / GEN-08, POL-001 / POL-009 and GEN-03 / GEN-09.
Status should be `pending_human_approval`. Then approve it with a note such as
"Approved on condition auto-rejection is disabled" to show the human gate and
the audit trail.
*Document upload:* the DPIA is detected as Arabic/mixed and classified as
`data_protection_impact_assessment`. Its tables (data inventory, risk
register) are extracted, and `analyze_document` flags nationality, gender,
national ID, date of birth and photo.

**4 – Deepfake ad campaign (block).** A real person's face and voice are
cloned without written consent, with no AI label, a misleading financial
claim, a purchased phone list with unknown consent, and a vendor that reuses
uploaded content for training. Maps directly to SDAIA's Deepfake Ethics
Guidelines and the PDPL.
*Check:* the decision is `block`, with consent, labelling, lawful basis and
vendor issues in the rationale.

**5 – Clinic triage chatbot, Arabic (block).** The whole submission is in
Arabic. A fully autonomous tool diagnoses patients (including children),
suggests medication doses and decides emergency vs home care with no
clinician review. Health data goes abroad without a transfer assessment, with
no SFDA clearance and no AI disclosure.
*Check:* the decision is `block` and the risk is `critical`. This shows Arabic
input is understood end-to-end.

## Suggested demo order

1 → 3 → 5 is the three-minute version (approve, human gate, block). Add 2 and
4 if there is time. Run case 3 twice to show that decisions are consistent.
