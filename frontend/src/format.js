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
