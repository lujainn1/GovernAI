import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  FileStack,
  Gauge,
  Hourglass,
  Ban,
} from 'lucide-react'

import { listPolicies, listReports } from '../api.js'
import { daysSince, median, riskTone, timeAgo } from '../format.js'

const RANGES = [
  { key: '7d', label: '7d', days: 7 },
  { key: '30d', label: '30d', days: 30 },
  { key: '90d', label: 'Quarter', days: 90 },
]

const RISK_LEVELS = ['low', 'medium', 'high', 'critical']
const RISK_COLOR = {
  low: 'var(--teal)',
  medium: 'var(--amber)',
  high: 'var(--rose)',
  critical: 'var(--rose)',
}

export default function Overview() {
  const [reports, setReports] = useState(null)
  const [policies, setPolicies] = useState([])
  const [error, setError] = useState(null)
  const [range, setRange] = useState('30d')

  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false

    Promise.all([listReports(), listPolicies().catch(() => [])]).then(
      ([reportData, policyData]) => {
        if (cancelled) return
        setReports(reportData)
        setPolicies(policyData)
      }
    ).catch((err) => {
      if (!cancelled) setError(err.message)
    })

    return () => {
      cancelled = true
    }
  }, [])

  const rangeDays = RANGES.find((r) => r.key === range)?.days ?? 30

  const scoped = useMemo(() => {
    if (!reports) return []
    const cutoff = Date.now() - rangeDays * 86400000
    return reports.filter((r) => new Date(r.created_at).getTime() >= cutoff)
  }, [reports, rangeDays])

  const stats = useMemo(() => {
    const all = reports || []
    const pending = all.filter(
      (r) => r.status === 'pending_human_approval'
    )
    const blocked = all.filter((r) => r.status === 'blocked')
    const oldestPending = pending.reduce((max, r) => {
      const d = daysSince(r.created_at)
      return d > max ? d : max
    }, 0)
    const thisMonth = all.filter(
      (r) => daysSince(r.created_at) <= 30
    ).length
    const scores = scoped
      .map((r) => Number(r.risk_assessment?.risk_score))
      .filter((n) => !Number.isNaN(n))

    return {
      total: all.length,
      thisMonth,
      pendingCount: pending.length,
      oldestPending,
      blockedCount: blocked.length,
      blockedPct: all.length
        ? Math.round((blocked.length / all.length) * 100)
        : 0,
      medianScore: median(scores),
    }
  }, [reports, scoped])

  const riskBars = useMemo(() => {
    const counts = { low: 0, medium: 0, high: 0, critical: 0 }
    scoped.forEach((r) => {
      const level = r.risk_assessment?.risk_level?.toLowerCase()
      if (counts[level] !== undefined) counts[level] += 1
    })
    const max = Math.max(1, ...Object.values(counts))
    return RISK_LEVELS.map((level) => ({
      level,
      n: counts[level],
      pct: Math.round((counts[level] / max) * 100),
    }))
  }, [scoped])

  const spark = useMemo(() => {
    const weeks = 12
    const buckets = new Array(weeks).fill(0)
    const now = Date.now()
    scoped.forEach((r) => {
      const age = now - new Date(r.created_at).getTime()
      const weekIndex = weeks - 1 - Math.floor(age / (7 * 86400000))
      if (weekIndex >= 0 && weekIndex < weeks) buckets[weekIndex] += 1
    })
    const max = Math.max(1, ...buckets)
    return buckets.map((n) => ({ n, pct: Math.max(6, Math.round((n / max) * 100)) }))
  }, [scoped])

  const decisionMix = useMemo(() => {
    const counts = { approve: 0, require_human_approval: 0, block: 0 }
    scoped.forEach((r) => {
      const d = r.decision?.decision
      if (counts[d] !== undefined) counts[d] += 1
    })
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
  }, [scoped])

  const topPolicies = useMemo(() => {
    const counts = {}
    scoped.forEach((r) => {
      (r.policy_compliance?.violated_policies || []).forEach((id) => {
        counts[id] = (counts[id] || 0) + 1
      })
    })
    const byId = Object.fromEntries(policies.map((p) => [p.id, p.title]))
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([id, n]) => ({ id, title: byId[id] || id, n }))
  }, [scoped, policies])

  const pendingCards = useMemo(() => {
    return (reports || [])
      .filter((r) => r.status === 'pending_human_approval')
      .slice(0, 3)
  }, [reports])

  if (error) {
    return <div className="error-banner">{error}</div>
  }

  if (!reports) {
    return <div className="loading">Loading governance overview…</div>
  }

  return (
    <div className="page-rise">
      <div className="ov-head">
        <div>
          <h1>Governance overview</h1>
          <p className="ov-sub">
            {stats.total} use cases governed
            {reports.length > 0 &&
              ` · last pipeline run ${timeAgo(reports[0]?.updated_at)}`}
          </p>
        </div>
        <div className="range-toggle">
          {RANGES.map((r) => (
            <span
              key={r.key}
              className={r.key === range ? 'range-opt active' : 'range-opt'}
              onClick={() => setRange(r.key)}
            >
              {r.label}
            </span>
          ))}
        </div>
      </div>

      <div className="ov-stats-grid">
        <div className="ov-stat-card">
          <div className="ov-stat-label">
            <FileStack size={14} className="tone-teal" />
            Use cases governed
          </div>
          <div className="ov-stat-value">{stats.total}</div>
          <div className="ov-stat-meta tone-teal-text">
            +{stats.thisMonth} this month
          </div>
        </div>

        <div
          className="ov-stat-card ov-stat-amber"
          onClick={() => navigate('/reports?filter=Pending')}
        >
          <div className="ov-stat-label">
            <Hourglass size={14} className="tone-amber" />
            Awaiting human approval
          </div>
          <div className="ov-stat-value tone-amber-text">{stats.pendingCount}</div>
          <div className="ov-stat-meta">
            {stats.oldestPending > 0
              ? `oldest waiting ${stats.oldestPending}d`
              : 'none waiting'}
          </div>
        </div>

        <div className="ov-stat-card">
          <div className="ov-stat-label">
            <Ban size={14} className="tone-rose" />
            Blocked
          </div>
          <div className="ov-stat-value">{stats.blockedCount}</div>
          <div className="ov-stat-meta">{stats.blockedPct}% of submissions</div>
        </div>

        <div className="ov-stat-card">
          <div className="ov-stat-label">
            <Gauge size={14} className="tone-violet" />
            Median risk score
          </div>
          <div className="ov-stat-value">
            {stats.medianScore}
            <span className="ov-stat-value-suffix">/100</span>
          </div>
          <div className="ov-stat-meta">across {scoped.length} in range</div>
        </div>
      </div>

      <div className="ov-mid-grid">
        <div className="ov-panel">
          <div className="ov-panel-head">
            <h2>Risk distribution</h2>
            <span className="ov-panel-note">last {rangeDays} days</span>
          </div>

          <div className="risk-bars">
            {riskBars.map((b) => (
              <div className="risk-bar-row" key={b.level}>
                <span className="risk-bar-label">{b.level}</span>
                <div className="risk-bar-track">
                  <div
                    className="risk-bar-fill"
                    style={{ width: `${b.pct}%`, background: RISK_COLOR[b.level] }}
                  />
                </div>
                <span className="risk-bar-n">{b.n}</span>
              </div>
            ))}
          </div>

          <div className="ov-divider" />

          <div className="ov-panel-head" style={{ marginBottom: 11 }}>
            <h2>Submissions per week</h2>
          </div>
          <div className="sparkline">
            {spark.map((s, i) => (
              <div key={i} className="sparkline-bar" style={{ height: `${s.pct}%` }} />
            ))}
          </div>
        </div>

        <div className="ov-panel">
          <h2 style={{ marginBottom: 14 }}>Decision mix</h2>
          <div className="decision-mix-row">
            <div className="decision-donut" style={{ background: decisionMix.conic }}>
              <div className="decision-donut-center">
                <span className="decision-donut-pct">{decisionMix.approvePct}%</span>
                <span className="decision-donut-label">auto-approved</span>
              </div>
            </div>
            <div className="decision-legend">
              <div>
                <span className="legend-dot" style={{ background: 'var(--teal)' }} />
                Approve <span className="legend-n">{decisionMix.counts.approve}</span>
              </div>
              <div>
                <span className="legend-dot" style={{ background: 'var(--amber)' }} />
                Human review{' '}
                <span className="legend-n">{decisionMix.counts.require_human_approval}</span>
              </div>
              <div>
                <span className="legend-dot" style={{ background: 'var(--rose)' }} />
                Block <span className="legend-n">{decisionMix.counts.block}</span>
              </div>
            </div>
          </div>

          <div className="ov-divider" />

          <h2 style={{ marginBottom: 11 }}>Most violated policies</h2>
          {topPolicies.length === 0 ? (
            <p className="ov-empty-note">No policy violations in range.</p>
          ) : (
            <div className="top-policies">
              {topPolicies.map((p) => (
                <div className="top-policy-row" key={p.id}>
                  <span className="mono tone-violet-text">{p.id}</span>
                  <span className="top-policy-title">{p.title}</span>
                  <span className="top-policy-n">{p.n}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="ov-panel">
        <div className="ov-panel-head">
          <h2>Needs your decision</h2>
          <span className="ov-link" onClick={() => navigate('/reports')}>
            View all reports →
          </span>
        </div>

        {pendingCards.length === 0 ? (
          <p className="ov-empty-note">Nothing is waiting on a human decision.</p>
        ) : (
          <div className="pending-cards">
            {pendingCards.map((r) => (
              <div
                className="pending-card"
                key={r.use_case.id}
                onClick={() => navigate(`/use-cases/${r.use_case.id}`)}
              >
                <div className="pending-card-top">
                  <span className={`pill pill-${riskTone(r.risk_assessment?.risk_level)}`}>
                    {r.risk_assessment?.risk_level} ·{' '}
                    <span className="mono">{r.risk_assessment?.risk_score}</span>
                  </span>
                  <span className="pending-card-age">{timeAgo(r.created_at)}</span>
                </div>
                <div className="pending-card-name">{r.use_case.name}</div>
                <div className="pending-card-owner">{r.use_case.owner}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
