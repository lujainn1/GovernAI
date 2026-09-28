import { useEffect, useState } from 'react'
import { Activity, Coins, Gauge, Timer } from 'lucide-react'

import { getHealthReport, getMetrics } from '../api.js'
import DecisionDonut from '../components/DecisionDonut.jsx'
import { decisionMixFromCounts, pretty, timeAgo } from '../format.js'

const RANGES = [
  { key: '1h', label: '1h', hours: 1 },
  { key: '24h', label: '24h', hours: 24 },
  { key: '7d', label: '7d', hours: 168 },
]

const REFRESH_MS = 60_000

const STATUS_TONE = { HEALTHY: 'teal', DEGRADED: 'amber', CRITICAL: 'rose' }
const SEVERITY_TONE = { critical: 'rose', high: 'amber', medium: 'violet' }

const pct = (value) => `${(value * 100).toFixed(1)}%`
const ms = (value) => (value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`)
const usd = (value) => `$${value < 0.01 ? value.toFixed(4) : value.toFixed(2)}`
const tokens = (value) => new Intl.NumberFormat().format(value)

function errorTone(rate) {
  if (rate >= 0.1) return 'tone-rose-text'
  if (rate > 0) return 'tone-amber-text'
  return 'tone-teal-text'
}

function Stat({ icon: Icon, label, value, meta, tone }) {
  return (
    <div className="ov-stat-card">
      <div className="ov-stat-label">
        <Icon size={13} className={tone} />
        {label}
      </div>
      <div className="ov-stat-value">{value}</div>
      <div className="ov-stat-meta">{meta}</div>
    </div>
  )
}

function HealthBanner({ health }) {
  const tone = STATUS_TONE[health.status] || 'neutral'
  const worst = health.worst_component

  return (
    <div className={`mon-banner mon-banner-${tone}`}>
      <div className="mon-banner-head">
        <span className={`pill pill-${tone}`}>{health.status}</span>
        <span className="mon-banner-title">
          System health · {health.summary.runs} agent runs, {pct(health.summary.success_rate)} succeeded
        </span>
      </div>

      {health.reasons.length > 0 && (
        <ul className="mon-list">
          {health.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}

      {worst && (
        <p className="mon-banner-note">
          Worst component: <strong>{worst.name}</strong> ({worst.kind}) — {pct(worst.failure_rate)} failing (
          {worst.failed} of {worst.total}), most common: <span className="mono">{worst.top_error}</span>
        </p>
      )}

      {health.notes.map((note) => (
        <p className="mon-banner-note tone-faint" key={note}>
          {note}
        </p>
      ))}
    </div>
  )
}

export default function Monitoring() {
  const [range, setRange] = useState('24h')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  const hours = RANGES.find((r) => r.key === range)?.hours ?? 24

  useEffect(() => {
    let cancelled = false

    function refresh() {
      Promise.all([getMetrics(hours), getHealthReport(hours)])
        .then(([metrics, health]) => {
          if (cancelled) return
          setData({ metrics, health })
          setError(null)
        })
        .catch((err) => {
          if (!cancelled) setError(err.message)
        })
    }

    refresh()
    const timer = setInterval(refresh, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [hours])

  if (error && !data) {
    return <div className="error-banner">{error}</div>
  }

  if (!data) {
    return <div className="loading">Loading agent metrics…</div>
  }

  const { metrics, health } = data
  const failing = health.failure_categories.filter((c) => c.runs_affected > 0)
  const anomalyGroups = health.latency_anomalies.groups

  return (
    <div className="page-rise">
      <div className="ov-head">
        <div>
          <h1>Monitoring</h1>
          <p className="ov-sub">
            Agent runs, tool usage, cost and health · refreshes every minute · generated{' '}
            {timeAgo(health.generated_at)}
          </p>
        </div>
        <div className="range-toggle">
          {RANGES.map((r) => (
            <span
              key={r.key}
              className={r.key === range ? 'range-opt active' : 'range-opt'}
              onClick={() => {
                setData(null)
                setRange(r.key)
              }}
            >
              {r.label}
            </span>
          ))}
        </div>
      </div>

      {error && <div className="error-banner">Could not refresh: {error}</div>}
      {metrics.truncated && (
        <div className="error-banner">Showing the most recent rows only; this window has more runs than the row limit.</div>
      )}

      <HealthBanner health={health} />

      <div className="ov-stats-grid">
        <Stat
          icon={Activity}
          label="Success rate"
          value={pct(metrics.runs.success_rate)}
          meta={`${metrics.runs.success} of ${metrics.runs.total} runs · ${metrics.runs.error} failed`}
          tone={metrics.runs.error ? 'tone-amber' : 'tone-teal'}
        />
        <Stat
          icon={Timer}
          label="Avg latency"
          value={ms(metrics.latency.avg_ms)}
          meta={`P95 ${ms(metrics.latency.p95_ms)} · max ${ms(metrics.latency.max_ms)}`}
          tone="tone-violet"
        />
        <Stat
          icon={Coins}
          label="Estimated cost"
          value={usd(metrics.cost.total_usd)}
          meta={`${usd(metrics.cost.avg_per_run_usd)} per run · estimate, not a bill`}
          tone="tone-teal"
        />
        <Stat
          icon={Gauge}
          label="Tokens"
          value={tokens(metrics.tokens.total)}
          meta={`${tokens(metrics.tokens.prompt)} prompt · ${tokens(metrics.tokens.completion)} completion`}
          tone="tone-violet"
        />
      </div>

      <div className="ov-mid-grid">
        <div className="ov-panel">
          <div className="ov-panel-head">
            <h2>Tool usage</h2>
            <span className="ov-panel-note">calls, error rate, latency</span>
          </div>
          {metrics.tools.length === 0 ? (
            <p className="ov-empty-note">No tool calls in this window.</p>
          ) : (
            <div className="mon-table">
              <div className="mon-row mon-row-head">
                <span>Tool</span>
                <span>Calls</span>
                <span>Errors</span>
                <span>Avg</span>
                <span>P95</span>
              </div>
              {metrics.tools.map((t) => (
                <div className="mon-row" key={t.tool}>
                  <span className="mono">{t.tool}</span>
                  <span>{t.calls}</span>
                  <span className={errorTone(t.error_rate)}>{pct(t.error_rate)}</span>
                  <span>{ms(t.avg_latency_ms)}</span>
                  <span>{ms(t.p95_latency_ms)}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="ov-panel">
          <h2 style={{ marginBottom: 14 }}>Decision mix</h2>
          <DecisionDonut
            mix={decisionMixFromCounts(metrics.decisions.counts)}
            centerValue={metrics.decisions.total}
            centerLabel="decisions"
            ringBackground={metrics.decisions.total === 0 ? 'var(--line)' : undefined}
          />
        </div>
      </div>

      <div className="ov-panel mon-block">
        <div className="ov-panel-head">
          <h2>Agents</h2>
          <span className="ov-panel-note">per agent, all runs in range</span>
        </div>
        {metrics.agents.length === 0 ? (
          <p className="ov-empty-note">No agent runs in this window.</p>
        ) : (
          <div className="mon-table">
            <div className="mon-row mon-row-agents mon-row-head">
              <span>Agent</span>
              <span>Runs</span>
              <span>Success</span>
              <span>Avg</span>
              <span>P95</span>
              <span>Tokens</span>
              <span>Cost</span>
            </div>
            {metrics.agents.map((a) => (
              <div className="mon-row mon-row-agents" key={a.agent}>
                <span className="mono">{a.agent}</span>
                <span>{a.runs}</span>
                <span className={errorTone(1 - a.success_rate)}>{pct(a.success_rate)}</span>
                <span>{ms(a.avg_ms)}</span>
                <span>{ms(a.p95_ms)}</span>
                <span>{tokens(a.total_tokens)}</span>
                <span>{usd(a.cost_usd)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="ov-mid-grid">
        <div className="ov-panel">
          <div className="ov-panel-head">
            <h2>Failure classes</h2>
            <span className="ov-panel-note">most severe first</span>
          </div>
          {failing.length === 0 ? (
            <p className="ov-empty-note">No failures in this window.</p>
          ) : (
            failing.map((c) => (
              <div className="mon-item" key={c.category}>
                <div className="mon-item-head">
                  <span className={`pill pill-${SEVERITY_TONE[c.severity] || 'neutral'}`}>{c.severity}</span>
                  <strong>{pretty(c.category)}</strong>
                  <span className="tag-neutral">{c.runs_affected} runs</span>
                  {c.dominant && <span className="tag-neutral">dominant</span>}
                </div>
                <div className="mon-item-body">{c.description}</div>
                {c.top_errors.map((e) => (
                  <div className="mon-item-body mono" key={e.error}>
                    {e.count}× {e.error}
                  </div>
                ))}
              </div>
            ))
          )}
        </div>

        <div className="ov-panel">
          <div className="ov-panel-head">
            <h2>Recommended actions</h2>
            <span className="ov-panel-note">from what happened in range</span>
          </div>
          {health.recommended_actions.length === 0 ? (
            <p className="ov-empty-note">No action needed. Keep monitoring.</p>
          ) : (
            health.recommended_actions.map((a) => (
              <div className="mon-item" key={`${a.priority}-${a.category}`}>
                <div className="mon-item-head">
                  <span className="mono tone-faint">{a.priority}.</span>
                  <span className={`pill pill-${SEVERITY_TONE[a.severity] || 'neutral'}`}>{a.severity}</span>
                  <strong>{pretty(a.category)}</strong>
                </div>
                <div className="mon-item-body">{a.action}</div>
                <div className="mon-item-body tone-faint">{a.reason}</div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="ov-panel mon-block">
        <div className="ov-panel-head">
          <h2>Latency anomalies</h2>
          <span className="ov-panel-note">{health.latency_anomalies.method}</span>
        </div>
        {anomalyGroups.length === 0 ? (
          <p className="ov-empty-note">Not enough successful calls yet to compute a latency baseline.</p>
        ) : (
          <div className="mon-table">
            <div className="mon-row mon-row-anomaly mon-row-head">
              <span>Group</span>
              <span>Samples</span>
              <span>Mean</span>
              <span>Threshold</span>
              <span>Anomalies</span>
            </div>
            {anomalyGroups.map((g) => (
              <div className="mon-row mon-row-anomaly" key={`${g.kind}-${g.name}`}>
                <span>
                  <span className="mono">{g.name}</span> <span className="tag-neutral">{pretty(g.kind)}</span>
                </span>
                <span>{g.samples}</span>
                <span>{ms(g.mean_ms)}</span>
                <span>{ms(g.threshold_ms)}</span>
                <span className={g.anomalies ? 'tone-amber-text' : 'tone-teal-text'}>
                  {g.anomalies} ({pct(g.anomaly_rate)})
                </span>
              </div>
            ))}
          </div>
        )}
        {health.latency_anomalies.top.length > 0 && (
          <>
            <div className="ov-divider" />
            {health.latency_anomalies.top.map((a) => (
              <div className="mon-item-body" key={`${a.kind}-${a.run_id}-${a.name}-${a.latency_ms}`}>
                <span className="mono">{a.name}</span> took {ms(a.latency_ms)} (threshold {ms(a.threshold_ms)}) ·{' '}
                {timeAgo(a.started_at)} · use case <span className="mono">{a.use_case_id}</span>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  )
}
