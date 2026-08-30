import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  CheckCircle2,
  CheckSquare,
  ShieldAlert,
  XCircle,
} from 'lucide-react'

import { approveUseCase, getAuditLog, getReport } from '../api.js'
import { decisionTone, pretty, statusTone, timeAgo } from '../format.js'

function Tags({ items, tone }) {
  if (!items || items.length === 0) {
    return <p className="ov-empty-note">None</p>
  }
  return (
    <div className="tag-row">
      {items.map((item) => (
        <span className={`tag tag-${tone}`} key={item}>
          {item}
        </span>
      ))}
    </div>
  )
}

export default function ReportDetail() {
  const { id } = useParams()

  const [report, setReport] = useState(null)
  const [audit, setAudit] = useState([])
  const [error, setError] = useState(null)
  const [approver, setApprover] = useState('')
  const [notes, setNotes] = useState('')
  const [deciding, setDeciding] = useState(false)

  function refresh() {
    getReport(id)
      .then(setReport)
      .catch((err) => setError(err.message))
    getAuditLog(id)
      .then(setAudit)
      .catch(() => setAudit([]))
  }

  useEffect(refresh, [id])

  async function handleDecision(approved) {
    if (!approver.trim()) {
      setError('Enter an approver name/email before recording a decision.')
      return
    }

    setDeciding(true)
    setError(null)

    try {
      const updated = await approveUseCase(id, {
        approved,
        approver,
        notes: notes || null,
      })
      setReport(updated)
      getAuditLog(id).then(setAudit).catch(() => {})
    } catch (err) {
      setError(err.message)
    } finally {
      setDeciding(false)
    }
  }

  if (error && !report) {
    return (
      <div className="page-rise">
        <Link className="back-link" to="/reports">
          <ArrowLeft size={13} />
          Back to reports
        </Link>
        <div className="error-banner">{error}</div>
      </div>
    )
  }

  if (!report) {
    return <div className="loading">Loading report…</div>
  }

  const { use_case, risk_assessment, policy_compliance, decision, status, human_approval } =
    report

  const riskScore = Number(risk_assessment?.risk_score || 0)
  const riskLevel = risk_assessment?.risk_level || 'unknown'
  const isPending = status === 'pending_human_approval'

  return (
    <div className="page-rise report-detail">
      <Link className="back-link" to="/reports">
        <ArrowLeft size={13} />
        Back to reports
      </Link>

      <div className="report-title-row">
        <h1>{use_case.name}</h1>
        <span className={`pill pill-${statusTone(status)}`}>{pretty(status)}</span>
        <div className="toolbar-spacer" />
        <span className="mono tone-faintest">{use_case.id.slice(0, 8)}</span>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="detail-grid">
        <div className="detail-main">
          <div className="ov-panel">
            <h2 style={{ marginBottom: 12 }}>Use case</h2>
            <dl className="kv-grid">
              <dt>Owner</dt>
              <dd>{use_case.owner}</dd>
              <dt>Description</dt>
              <dd className="kv-desc">{use_case.description}</dd>
              <dt>Data classification</dt>
              <dd>
                <span className="tag tag-rose">{pretty(use_case.data_classification)}</span>
              </dd>
              <dt>Deployment</dt>
              <dd>{pretty(use_case.deployment_context)}</dd>
              <dt>Autonomy</dt>
              <dd>{pretty(use_case.autonomy_level)}</dd>
            </dl>
          </div>

          <div className="ov-panel">
            <div className="ov-panel-head">
              <h2>Risk assessment</h2>
              <span className="agent-tag">
                <ShieldAlert size={13} className="tone-rose" />
                Risk Assessment Agent
              </span>
            </div>
            <div className="risk-donut-row">
              <div
                className="risk-donut"
                style={{
                  background: `conic-gradient(var(--rose) 0 ${riskScore}%, var(--line-faint) ${riskScore}% 100%)`,
                }}
              >
                <div className="risk-donut-center">
                  <span className="risk-donut-score">{riskScore}</span>
                  <span className="risk-donut-level">{riskLevel}</span>
                </div>
              </div>
              <div className="risk-factors-col">
                <div className="detail-label">Risk factors</div>
                <Tags items={risk_assessment?.risk_factors} tone="rose" />
              </div>
            </div>
            <p className="agent-rationale-text">{risk_assessment?.rationale}</p>
          </div>

          <div className="ov-panel">
            <div className="ov-panel-head">
              <h2>Policy compliance</h2>
              <span className={`pill pill-${policy_compliance?.status === 'compliant' ? 'teal' : 'rose'}`}>
                {pretty(policy_compliance?.status)}
              </span>
            </div>
            <div className="compliance-two-col">
              <div>
                <div className="detail-label tone-rose-text">
                  Violated · {policy_compliance?.violated_policies?.length || 0}
                </div>
                <div className="policy-mini-list">
                  {(policy_compliance?.violated_policies || []).map((pid) => (
                    <div className="policy-mini-row policy-mini-rose" key={pid}>
                      <span className="mono">{pid}</span>
                    </div>
                  ))}
                  {(policy_compliance?.violated_policies || []).length === 0 && (
                    <p className="ov-empty-note">None</p>
                  )}
                </div>
              </div>
              <div>
                <div className="detail-label tone-teal-text">
                  Satisfied · {policy_compliance?.satisfied_policies?.length || 0}
                </div>
                <div className="policy-mini-list">
                  {(policy_compliance?.satisfied_policies || []).map((pid) => (
                    <div className="policy-mini-row policy-mini-teal" key={pid}>
                      <span className="mono">{pid}</span>
                    </div>
                  ))}
                  {(policy_compliance?.satisfied_policies || []).length === 0 && (
                    <p className="ov-empty-note">None</p>
                  )}
                </div>
              </div>
            </div>
            <p className="agent-rationale-text">{policy_compliance?.rationale}</p>
          </div>

          <div className="ov-panel decision-panel">
            <div className="ov-panel-head">
              <h2>Decision</h2>
              <span className={`pill pill-${decisionTone(decision?.decision)}`}>
                {pretty(decision?.decision)}
              </span>
            </div>
            <div className="detail-label">Conditions</div>
            <div className="conditions-list">
              {(decision?.conditions || []).length === 0 ? (
                <p className="ov-empty-note">None</p>
              ) : (
                decision.conditions.map((c) => (
                  <div className="condition-row" key={c}>
                    <CheckSquare size={13} className="tone-violet" />
                    {c}
                  </div>
                ))
              )}
            </div>
            <p className="agent-rationale-text">{decision?.rationale}</p>
          </div>
        </div>

        <div className="detail-rail">
          {isPending && (
            <div className="ov-panel decide-panel">
              <h2 style={{ marginBottom: 3 }}>Record human decision</h2>
              <p className="ov-empty-note" style={{ marginBottom: 13 }}>
                Logged to the audit trail with your name.
              </p>
              <div className="field">
                <label htmlFor="approver">Approver</label>
                <input
                  id="approver"
                  value={approver}
                  onChange={(e) => setApprover(e.target.value)}
                  placeholder="you@example.com"
                />
              </div>
              <div className="field">
                <label htmlFor="notes">Notes</label>
                <textarea
                  id="notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="e.g. added human review step"
                />
              </div>
              <div className="decide-actions">
                <button
                  className="btn-approve"
                  type="button"
                  disabled={deciding}
                  onClick={() => handleDecision(true)}
                >
                  Approve
                </button>
                <button
                  className="btn-reject"
                  type="button"
                  disabled={deciding}
                  onClick={() => handleDecision(false)}
                >
                  Reject
                </button>
              </div>
            </div>
          )}

          {human_approval && (
            <div className={`ov-panel decided-panel ${human_approval.approved ? 'approved' : 'rejected'}`}>
              <div className="decided-head">
                {human_approval.approved ? (
                  <CheckCircle2 size={16} className="tone-teal" />
                ) : (
                  <XCircle size={16} className="tone-rose" />
                )}
                <h2>{human_approval.approved ? 'Approved by human' : 'Rejected by human'}</h2>
              </div>
              <dl className="kv-grid kv-grid-tight">
                <dt>Approver</dt>
                <dd>{human_approval.approver}</dd>
                <dt>Decided</dt>
                <dd>{timeAgo(human_approval.decided_at)}</dd>
                <dt>Logged</dt>
                <dd className="mono">audit_log.jsonl</dd>
              </dl>
            </div>
          )}

          <div className="ov-panel">
            <div className="ov-panel-head">
              <h2>Audit trail</h2>
            </div>
            <div className="mini-audit">
              {audit.length === 0 ? (
                <p className="ov-empty-note">No entries yet.</p>
              ) : (
                audit.map((entry, i) => (
                  <div className="mini-audit-row" key={entry.id}>
                    <div className="mini-audit-dot-col">
                      <span className="audit-dot" />
                      {i < audit.length - 1 && <span className="audit-dot-line" />}
                    </div>
                    <div>
                      <div className="mini-audit-stage">{pretty(entry.stage)}</div>
                      <div className="mini-audit-meta">
                        {entry.actor} · {timeAgo(entry.timestamp)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
