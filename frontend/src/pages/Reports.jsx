import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowDownUp, Download, Filter } from 'lucide-react'

import { listReports } from '../api.js'
import { decisionTone, pretty, riskTone, statusTone, timeAgo } from '../format.js'

function reportGroup(report) {
  if (report.status === 'blocked') return 'Blocked'
  if (report.status === 'pending_human_approval') return 'Pending'
  if (report.status === 'approved_by_human' || report.status === 'completed') {
    return 'Approved'
  }
  return 'Other'
}

export default function Reports() {
  const [reports, setReports] = useState(null)
  const [error, setError] = useState(null)
  const [sortAsc, setSortAsc] = useState(false)

  const [searchParams, setSearchParams] = useSearchParams()
  const filter = searchParams.get('filter') || 'All'
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    listReports()
      .then((data) => {
        if (!cancelled) setReports(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const counts = useMemo(() => {
    const all = reports || []
    return {
      All: all.length,
      Pending: all.filter((r) => reportGroup(r) === 'Pending').length,
      Blocked: all.filter((r) => reportGroup(r) === 'Blocked').length,
      Approved: all.filter((r) => reportGroup(r) === 'Approved').length,
    }
  }, [reports])

  const rows = useMemo(() => {
    const all = reports || []
    const filtered =
      filter === 'All' ? all : all.filter((r) => reportGroup(r) === filter)

    return [...filtered].sort((a, b) => {
      const diff = new Date(a.updated_at) - new Date(b.updated_at)
      return sortAsc ? diff : -diff
    })
  }, [reports, filter, sortAsc])

  if (error) {
    return <div className="error-banner">{error}</div>
  }

  if (!reports) {
    return <div className="loading">Loading governance reports…</div>
  }

  return (
    <div className="page-rise">
      <div className="ov-head">
        <div>
          <h1>Governance reports</h1>
          <p className="ov-sub">
            Every submission, its risk level, the agents&apos; decision and where it stands.
          </p>
        </div>
        <button className="btn-outline" type="button">
          <Download size={13} />
          Export
        </button>
      </div>

      <div className="reports-toolbar">
        <div className="filter-chips">
          {['All', 'Pending', 'Blocked', 'Approved'].map((f) => (
            <span
              key={f}
              className={f === filter ? 'filter-chip active' : 'filter-chip'}
              onClick={() => setSearchParams(f === 'All' ? {} : { filter: f })}
            >
              {f}
              <span className="filter-chip-n">{counts[f] ?? 0}</span>
            </span>
          ))}
        </div>
        <div className="toolbar-spacer" />
        <span className="toolbar-btn">
          <Filter size={12} />
          Owner
        </span>
        <span className="toolbar-btn" onClick={() => setSortAsc((v) => !v)}>
          <ArrowDownUp size={12} />
          Updated
        </span>
      </div>

      <div className="reports-table-card">
        <div className="reports-table-head">
          <span>Use case</span>
          <span>Owner</span>
          <span>Risk</span>
          <span>Decision</span>
          <span>Status</span>
          <span style={{ textAlign: 'right' }}>Updated</span>
        </div>

        {rows.length === 0 ? (
          <div className="empty-state">
            <h3>No matching reports</h3>
            <p>Try a different filter.</p>
          </div>
        ) : (
          rows.map((r) => (
            <div
              className="reports-table-row"
              key={r.use_case.id}
              onClick={() => navigate(`/use-cases/${r.use_case.id}`)}
            >
              <span className="reports-name">{r.use_case.name}</span>
              <span className="reports-owner">{r.use_case.owner}</span>
              <span>
                <span className={`pill pill-${riskTone(r.risk_assessment?.risk_level)}`}>
                  {r.risk_assessment?.risk_level}{' '}
                  <span className="mono pill-dim">{r.risk_assessment?.risk_score}</span>
                </span>
              </span>
              <span>
                <span className={`pill pill-${decisionTone(r.decision?.decision)}`}>
                  {pretty(r.decision?.decision)}
                </span>
              </span>
              <span>
                <span className={`pill pill-${statusTone(r.status)}`}>
                  {pretty(r.status)}
                </span>
              </span>
              <span className="reports-updated">{timeAgo(r.updated_at)}</span>
            </div>
          ))
        )}

        <div className="reports-table-foot">
          <span>
            Showing {rows.length} of {reports.length} reports
          </span>
        </div>
      </div>
    </div>
  )
}
