import { CheckCircle2, XCircle } from 'lucide-react'

import { timeAgo } from '../format.js'

// The human verdict on one step of a step-by-step run.
export default function StepStatus({ step }) {
  if (step.status === 'approved') {
    return (
      <span className="step-verdict tone-teal-text">
        <CheckCircle2 size={14} className="tone-teal" />
        Approved by {step.decided_by} · {timeAgo(step.decided_at)}
      </span>
    )
  }
  if (step.status === 'rejected') {
    return (
      <span className="step-verdict tone-rose-text">
        <XCircle size={14} className="tone-rose" />
        Rejected by {step.decided_by} · {timeAgo(step.decided_at)}
      </span>
    )
  }
  return <span className="pill pill-amber">awaiting approval</span>
}
