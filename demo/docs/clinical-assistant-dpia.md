# DPIA (draft) — Clinical Notes Diagnosis Assistant

## Processing
Each request sends a patient's full clinical notes to an external hosted LLM
API, which returns a ranked list of likely diagnoses for the treating clinician
to consider. The clinician decides; the assistant writes nothing back to the
patient record.

## Data categories
Restricted health data: free-text clinical notes, existing diagnoses,
medication history and genetic test results. Notes are sent as-is — no
de-identification, redaction or pseudonymization step exists, so the national
ID and full name in the note header are transmitted with it.

## Transfer and vendor
Data is sent to the vendor's public API endpoint. No data processing agreement
has been signed and the processing location has not been confirmed. The vendor
keeps plain-text request logs for 30 days for debugging, accessible to its
support staff.

## Status of assessment
This DPIA is a draft and has not been signed off. Outstanding: lawful basis,
retention schedule, transfer risk assessment, safeguards for the vendor, and
patient-facing transparency notice.
