import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  CheckCircle2,
  Gavel,
  LoaderCircle,
  Scale,
  ShieldAlert,
  TerminalSquare,
  Wrench,
} from 'lucide-react'

import { getAuditLog, submitUseCase } from '../api.js'
import { pretty, timeAgo } from '../format.js'

const AGENTS = [
  {
    key: 'risk',
    name: 'Risk Assessment',
    icon: ShieldAlert,
    tools: ['get_risk_rules', 'get_scoring_bands', 'analyze_document'],
  },
  {
    key: 'policy',
    name: 'Policy Compliance',
    icon: Scale,
    tools: ['get_policies', 'search_policies', 'analyze_document'],
  },
  {
    key: 'decision',
    name: 'Decision',
    icon: Gavel,
    tools: ['get_policies', 'log_event'],
  },
]

export default function PipelineRun() {
  const location = useLocation()
  const navigate = useNavigate()
  const payload = location.state?.payload

  const [step, setStep] = useState(0)
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)
  const [logLines, setLogLines] = useState([])
  const started = useRef(false)

  useEffect(() => {
    if (!payload) {
      navigate('/submit', { replace: true })
      return
    }
    if (started.current) return
    started.current = true

    const timer = setInterval(() => {
      setStep((s) => Math.min(s + 1, AGENTS.length - 1))
    }, 4000)

    submitUseCase(payload)
      .then((result) => {
        clearInterval(timer)
        setStep(AGENTS.length - 1)
        setReport(result)
        return getAuditLog(result.use_case.id).catch(() => [])
      })
      .then((entries) => {
        if (entries) setLogLines(entries)
      })
      .catch((err) => {
        clearInterval(timer)
        setError(err.message)
      })

    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const done = Boolean(report)
  const progressPct = error ? 100 : done ? 100 : 18 + step * 32

  if (error) {
    return (
      <div className="page-rise pipeline-run">
        <h1>Running governance pipeline</h1>
        <div className="error-banner" style={{ marginTop: 14 }}>
          {error}
        </div>
        <button className="btn-outline" style={{ marginTop: 14 }} onClick={() => navigate('/submit')}>
          Back to submit
        </button>
      </div>
    )
  }

  return (
    <div className="page-rise pipeline-run">
      <div className="run-title-row">
        <h1>Running governance pipeline</h1>
        <span className={`pill ${done ? 'pill-teal' : 'pill-violet'}`}>
          {done ? 'complete' : 'running'}
        </span>
      </div>
      <p className="ov-sub" style={{ marginBottom: 18 }}>
        {payload?.name || 'AI use case'}
        {report && (
          <>
            {' '}
            · <span className="mono">{report.use_case.id.slice(0, 8)}</span>
          </>
        )}
      </p>

      <div className="run-progress-track">
        <div className="run-progress-fill" style={{ width: `${progressPct}%` }} />
      </div>

      <div className="run-agents-grid">
        {AGENTS.map((agent, i) => {
          const Icon = agent.icon
          const state = done ? 'done' : i < step ? 'done' : i === step ? 'running' : 'queued'
          let result = null
          if (done && report) {
            if (agent.key === 'risk') {
              result = `${report.risk_assessment.risk_level} · ${report.risk_assessment.risk_score}/100`
            } else if (agent.key === 'policy') {
              result = `${pretty(report.policy_compliance.status)} · ${report.policy_compliance.violated_policies.length} violated`
            } else {
              result = pretty(report.decision.decision)
            }
          }
          return (
            <div className={`run-agent-card run-agent-${state}`} key={agent.key}>
              <div className="run-agent-head">
                <div className="run-agent-icon">
                  <Icon size={16} />
                </div>
                <div className="run-agent-meta">
                  <div className="run-agent-name">{agent.name}</div>
                  <div className="run-agent-state">
                    {state === 'done' ? 'complete' : state === 'running' ? 'running…' : 'queued'}
                  </div>
                </div>
                {state === 'running' && <LoaderCircle size={15} className="spin tone-violet" />}
                {state === 'done' && <CheckCircle2 size={15} className="tone-teal" />}
              </div>
              <div className="run-agent-tools">
                {agent.tools.map((t) => (
                  <div className="run-tool-tag" key={t}>
                    <Wrench size={10} />
                    {t}
                  </div>
                ))}
              </div>
              {result && <div className="run-agent-result">{result}</div>}
            </div>
          )
        })}
      </div>

      <div className="run-log-card">
        <div className="run-log-head">
          <TerminalSquare size={13} />
          audit_log.jsonl
          <span className="run-log-spacer" />
          <span className="mono tone-faintest">append-only</span>
        </div>
        <div className="run-log-body">
          {logLines.length === 0 ? (
            <div className="run-log-line tone-faint">
              {done ? 'No audit entries recorded.' : 'Waiting for the orchestrator…'}
            </div>
          ) : (
            logLines.map((entry) => (
              <div className="run-log-line" key={entry.id}>
                <span className="tone-ghost">{timeAgo(entry.timestamp)}</span>{'  '}
                <span className="tone-violet-text">{entry.stage}</span>{'  '}
                {entry.actor}
              </div>
            ))
          )}
        </div>
      </div>

      {done && (
        <div className="run-done-banner">
          {report.status === 'pending_human_approval' || report.status === 'blocked' ? (
            <>
              <ShieldAlert size={19} className="tone-amber" />
              <div className="run-done-copy">
                <strong>
                  {report.status === 'blocked' ? 'Blocked' : 'Requires human approval'}
                </strong>
                <span>
                  {report.risk_assessment.risk_level} risk ({report.risk_assessment.risk_score}/100) ·{' '}
                  {report.policy_compliance.violated_policies.length} policies violated.
                </span>
              </div>
            </>
          ) : (
            <>
              <CheckCircle2 size={19} className="tone-teal" />
              <div className="run-done-copy">
                <strong>Approved</strong>
                <span>No human review required.</span>
              </div>
            </>
          )}
          <button
            className="btn-cta"
            type="button"
            onClick={() => navigate(`/use-cases/${report.use_case.id}`)}
          >
            Open report
          </button>
        </div>
      )}
    </div>
  )
}
