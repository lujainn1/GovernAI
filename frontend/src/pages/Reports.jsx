import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowDownUp, Download, Filter } from 'lucide-react'

import { listPipelineRuns, listReports } from '../api.js'
import {
  decisionTone,
  isUnfinishedRun,
  pretty,
  riskTone,
  runAsRow,
  statusTone,
  timeAgo,
} from '../format.js'

function reportGroup(report) {
  if (report.status === 'blocked') return 'Blocked'
  if (report.status === 'pending_human_approval') return 'Pending'
  if (report.status === 'approved_by_human' || report.status === 'completed') {
    return 'Approved'
  }
  return 'Other'
}

function reportAsRow(report) {
  return {
    id: report.use_case.id,
    use_case: report.use_case,
    risk_assessment: report.risk_assessment,
    decision: report.decision,
    statusLabel: pretty(report.status),
    statusTone: statusTone(report.status),
    group: reportGroup(report),
    updated_at: report.updated_at,
    to: `/use-cases/${report.use_case.id}`,
  }
}

export default function Reports() {
  // Reports and unfinished step-by-step runs, as one list of rows.
  const [entries, setEntries] = useState(null)
  const [error, setError] = useState(null)
  const [sortAsc, setSortAsc] = useState(false)

  const [searchParams, setSearchParams] = useSearchParams()
  const filter = searchParams.get('filter') || 'All'
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    // A run's list failing (e.g. its migration isn't applied) must not hide the reports.
    Promise.all([listReports(), listPipelineRuns().catch(() => [])])
      .then(([reportData, runData]) => {
        if (cancelled) return
        setEntries([
          ...reportData.map(reportAsRow),
          ...runData.filter(isUnfinishedRun).map(runAsRow),
        ])
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const counts = useMemo(() => {
    const all = entries || []
    return {
      All: all.length,
      Pending: all.filter((r) => r.group === 'Pending').length,
      Blocked: all.filter((r) => r.group === 'Blocked').length,
      Approved: all.filter((r) => r.group === 'Approved').length,
    }
  }, [entries])

  const rows = useMemo(() => {
    const all = entries || []
    const filtered =
      filter === 'All' ? all : all.filter((r) => r.group === filter)

    return [...filtered].sort((a, b) => {
      const diff = new Date(a.updated_at) - new Date(b.updated_at)
      return sortAsc ? diff : -diff
    })
  }, [entries, filter, sortAsc])

  if (error) {
    return <div className="error-banner">{error}</div>
  }

  if (!entries) {
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
              key={r.id}
              onClick={() => navigate(r.to)}
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
                <span className={`pill pill-${r.statusTone}`}>{r.statusLabel}</span>
              </span>
              <span className="reports-updated">{timeAgo(r.updated_at)}</span>
            </div>
          ))
        )}

        <div className="reports-table-foot">
          <span>
            Showing {rows.length} of {entries.length} reports
          </span>
        </div>
      </div>
    </div>
  )
}
