# Design notes — Automated Loan Decision Agent

## Scope
Approves or denies consumer personal loan applications up to SAR 20,000 and
sends the outcome directly to the applicant. There is no human review step
before a decision is finalized, and no appeal process: denials are final and
executed automatically.

## Inputs
Applicant credit score and credit file (third-party credit bureau scoring
API), declared income, bank account balances and existing debt. Applicant data
is retained indefinitely; no retention period has been defined.

## Model
A third-party hosted scoring API returns a risk grade, which a thin rules layer
turns into approve/deny plus an interest rate. The provider's data-handling
terms have not been reviewed by legal and no data processing agreement has been
signed.

## Testing
Accuracy was measured against 12 months of historical decisions. No bias or
fairness testing has been performed, and no fairness metrics are defined. There
is no documented plan for recurring testing after launch.

## Notifications
Denial emails include the applicant's name and a summary of the credit factors
used. There is no channel for an applicant to contest a decision or request a
human review; a human looks at a denied case only if a complaint is filed later
through the general support queue.
