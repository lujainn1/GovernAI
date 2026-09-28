export function pretty(value) {
  if (!value) return '—'
  return String(value).replaceAll('_', ' ').replaceAll('-', ' ')
}

// Maps a risk level / decision / status value to one of the design's five
// pill tones: teal (good), amber (needs review), rose (bad), violet, neutral.
export function riskTone(level) {
  const l = (level || '').toLowerCase()
  if (l === 'low') return 'teal'
  if (l === 'medium') return 'amber'
  if (l === 'high' || l === 'critical') return 'rose'
  return 'neutral'
}

export function decisionTone(decision) {
  const d = (decision || '').toLowerCase()
  if (d === 'approve') return 'teal'
  if (d === 'require_human_approval') return 'amber'
  if (d === 'block') return 'rose'
  return 'neutral'
}

export function statusTone(status) {
  const s = (status || '').toLowerCase()
  if (s === 'blocked' || s === 'rejected_by_human') return 'rose'
  if (s.startsWith('pending')) return 'amber'
  if (s === 'completed' || s === 'approved_by_human') return 'teal'
  return 'neutral'
}

// A step-by-step run has no report until its last step is approved. Until
// then (or if a step was rejected) it is listed next to the reports as a row
// with the same fields the report lists read, plus where it stands and where
// clicking it should go. Completed runs are left out: they have a report.
export function isUnfinishedRun(run) {
  return run.status !== 'completed'
}

function latestStepOutput(run, agent) {
  const step = [...run.steps].reverse().find((s) => s.agent === agent)
  return step?.output
}

export function runAsRow(run) {
  const rejectedStep = run.steps.find((s) => s.status === 'rejected')
  const lastStep = run.steps[run.steps.length - 1]
  const rejected = run.status === 'rejected'

  return {
    id: run.use_case.id,
    use_case: run.use_case,
    risk_assessment: latestStepOutput(run, 'risk_assessment'),
    decision: latestStepOutput(run, 'decision'),
    statusLabel: rejected
      ? `Rejected at step ${rejectedStep?.seq}`
      : `Step ${lastStep?.seq} awaiting approval`,
    statusTone: rejected ? 'rose' : 'amber',
    group: rejected ? 'Other' : 'Pending',
    created_at: run.created_at,
    updated_at: run.updated_at,
    to: `/pipeline-run/${run.use_case.id}`,
  }
}

export function timeAgo(dateString) {
  if (!dateString) return '—'
  const then = new Date(dateString).getTime()
  if (Number.isNaN(then)) return '—'

  const diffMs = Date.now() - then
  const minutes = Math.floor(diffMs / 60000)

  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`

  const days = Math.floor(hours / 24)
  if (days === 1) return 'yesterday'
  if (days < 30) return `${days}d ago`

  const months = Math.floor(days / 30)
  return `${months}mo ago`
}

export function median(numbers) {
  const sorted = [...numbers].sort((a, b) => a - b)
  if (sorted.length === 0) return 0
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 0
    ? Math.round((sorted[mid - 1] + sorted[mid]) / 2)
    : sorted[mid]
}

export function daysSince(dateString) {
  if (!dateString) return 0
  const then = new Date(dateString).getTime()
  if (Number.isNaN(then)) return 0
  return Math.max(0, Math.floor((Date.now() - then) / 86400000))
}
