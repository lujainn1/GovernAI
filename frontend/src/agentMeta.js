import { ClipboardCheck, Gavel, Scale, ShieldAlert } from 'lucide-react'

// Display name and icon of each agent in a step-by-step run. Shared by the run
// page and the report detail's approvals panel.
export const AGENT_META = {
  risk_assessment: { name: 'Risk Assessment', icon: ShieldAlert },
  policy_compliance: { name: 'Policy Compliance', icon: Scale },
  decision: { name: 'Decision', icon: Gavel },
  review: { name: 'Review', icon: ClipboardCheck },
}
