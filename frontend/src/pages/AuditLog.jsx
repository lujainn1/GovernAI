import { useEffect, useMemo, useState } from 'react'

import { getAuditLog } from '../api.js'
import { pretty, timeAgo } from '../format.js'

const FILTERS = {
  'All stages': null,
  'Agent runs': ['risk_assessment', 'policy_compliance', 'decision'],
  'Tool calls': ['tool_call'],
  'Human decisions': ['human_approval'],
}

export default function AuditLog() {
  const [entries, setEntries] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('All stages')

  useEffect(() => {
    let cancelled = false
    getAuditLog()
      .then((data) => {
        if (!cancelled) setEntries(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const rows = useMemo(() => {
    if (!entries) return []
    const stages = FILTERS[filter]
    const filtered = stages
      ? entries.filter((e) => stages.includes(e.stage))
      : entries
    return [...filtered].sort(
      (a, b) => new Date(b.timestamp) - new Date(a.timestamp)
    )
  }, [entries, filter])

  if (error) {
    return <div className="error-banner">{error}</div>
  }

  if (!entries) {
    return <div className="loading">Loading audit log…</div>
  }

  return (
    <div className="page-rise audit-page">
      <h1>Audit log</h1>
      <p className="ov-sub" style={{ marginBottom: 14 }}>
        Append-only. Every agent stage, tool call and human decision, in order.
      </p>

      <div className="audit-filters">
        {Object.keys(FILTERS).map((f) => (
          <span
            key={f}
            className={f === filter ? 'audit-filter-chip active' : 'audit-filter-chip'}
            onClick={() => setFilter(f)}
          >
            {f}
          </span>
        ))}
      </div>

      <div className="audit-panel">
        {rows.length === 0 ? (
          <p className="ov-empty-note">No audit entries match this filter.</p>
        ) : (
          rows.map((entry, i) => (
            <div className="audit-row" key={entry.id}>
              <span className="mono audit-time">
                {new Date(entry.timestamp).toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </span>
              <div className="audit-dot-col">
                <span className="audit-dot" />
                {i < rows.length - 1 && <span className="audit-dot-line" />}
              </div>
              <div className="audit-body">
                <div className="audit-heading">
                  <span className="audit-stage">{pretty(entry.stage)}</span>
                  <span className="tag-neutral">{entry.actor}</span>
                </div>
                <div className="audit-detail">
                  {entry.use_case_id} · {timeAgo(entry.timestamp)}
                </div>
                {entry.data && Object.keys(entry.data).length > 0 && (
                  <pre className="audit-payload">
                    {JSON.stringify(entry.data, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
