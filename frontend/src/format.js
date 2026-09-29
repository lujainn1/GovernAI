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

// The Policy Compliance Agent now separates policies it found a conflict with
// from ones that merely apply and are unproven (`undetermined_policies`), so
// `partially_compliant` can mean "nothing is broken, some things are not
// evidenced". Showing that in the same red as `non_compliant` reads as a
// policy breach, so it gets amber - the same tone the platform already uses
// for "needs a person to look".
export function complianceTone(status) {
  const s = (status || '').toLowerCase()
  if (s === 'compliant') return 'teal'
  if (s === 'partially_compliant') return 'amber'
  if (s === 'non_compliant') return 'rose'
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

// Share of each governance decision plus the conic-gradient that draws it as
// a donut. `counts` has the keys approve, require_human_approval and block.
export function decisionMixFromCounts(counts) {
  const total = Math.max(1, counts.approve + counts.require_human_approval + counts.block)
  const approvePct = Math.round((counts.approve / total) * 100)
  const humanPct = Math.round((counts.require_human_approval / total) * 100)
  return {
    counts,
    total,
    approvePct,
    humanPct,
    conic: `conic-gradient(var(--teal) 0 ${approvePct}%, var(--amber) ${approvePct}% ${approvePct + humanPct}%, var(--rose) ${approvePct + humanPct}% 100%)`,
  }
}
