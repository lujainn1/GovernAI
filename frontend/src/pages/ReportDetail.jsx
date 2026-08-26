import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { approveUseCase, getReport } from '../api.js'

const ROUTING_FLAG_LABELS = {
  personal_data: 'Personal data',
  sensitive_data: 'Sensitive data',
  generative_ai: 'Generative AI',
  external_provider: 'External provider',
  data_outside_ksa: 'Data outside KSA',
  high_impact_decision: 'High-impact decision',
  user_facing_chat: 'User-facing chat',
  research_purpose: 'Research purpose',
}

const RESULT_ORDER = { fail: 0, review: 1, not_applicable: 2, pass: 3 }

function List({ items }) {
  if (!items || items.length === 0) return <p className="hint">None</p>
  return (
    <ul className="tag-list">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  )
}

function ActiveFlags({ useCase }) {
  const active = Object.entries(ROUTING_FLAG_LABELS).filter(([field]) => useCase[field])
  if (active.length === 0) return <p className="hint">None set</p>
  return (
    <ul className="tag-list">
      {active.map(([field, label]) => (
        <li key={field}>{label}</li>
      ))}
    </ul>
  )
}

function RiskRegister({ detectedRisks }) {
  if (!detectedRisks || detectedRisks.length === 0) {
    return <p className="hint">No named risks were triggered by this submission.</p>
  }
  return (
    <div className="scroll-table">
      <table>
        <thead>
          <tr>
            <th>Risk</th>
            <th>Domain</th>
            <th>L × I = Score</th>
            <th>Treatment</th>
          </tr>
        </thead>
        <tbody>
          {detectedRisks.map((risk) => (
            <tr key={risk.risk_id}>
              <td>
                <span className="mono-id">{risk.risk_id}</span> {risk.title}
              </td>
              <td>{risk.domain}</td>
              <td>
                {risk.likelihood} × {risk.impact} = {risk.inherent_score}/25
              </td>
              <td>{risk.treatment}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ControlEvaluations({ evaluations }) {
  if (!evaluations || evaluations.length === 0) {
    return <p className="hint">No controls were routed for this use case.</p>
  }
  const counts = evaluations.reduce((acc, e) => {
    acc[e.result] = (acc[e.result] || 0) + 1
    return acc
  }, {})
  const sorted = [...evaluations].sort((a, b) => RESULT_ORDER[a.result] - RESULT_ORDER[b.result])

  return (
    <>
      <div className="result-count-summary">
        <span>
          <strong>{counts.fail || 0}</strong> fail
        </span>
        <span>
          <strong>{counts.review || 0}</strong> review
        </span>
        <span>
          <strong>{counts.pass || 0}</strong> pass
        </span>
        <span>
          <strong>{evaluations.length}</strong> controls evaluated
        </span>
      </div>
      <div className="scroll-table">
        <table>
          <thead>
            <tr>
              <th>Control</th>
              <th>Module</th>
              <th>Result</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((e) => (
              <tr key={e.control_id}>
                <td>
                  <span className="mono-id">{e.control_id}</span>
                  {e.critical && ' ⚑'}
                  <br />
                  {e.principle}
                </td>
                <td>{e.module}</td>
                <td>
                  <span className={`badge badge-${e.result}`}>{e.result.replaceAll('_', ' ')}</span>
                </td>
                <td>{e.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

export default function ReportDetail() {
  const { id } = useParams()
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)
  const [approver, setApprover] = useState('')
  const [notes, setNotes] = useState('')
  const [deciding, setDeciding] = useState(false)

  function refresh() {
    getReport(id)
      .then(setReport)
      .catch((err) => setError(err.message))
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
      const updated = await approveUseCase(id, { approved, approver, notes: notes || null })
      setReport(updated)
    } catch (err) {
      setError(err.message)
    } finally {
      setDeciding(false)
    }
  }

  if (error && !report) {
    return (
      <>
        <Link to="/">&larr; Back to reports</Link>
        <div className="error-banner" style={{ marginTop: 16 }}>
          {error}
        </div>
      </>
    )
  }

  if (!report) return <div className="loading">Loading report…</div>

  const { use_case, risk_assessment, policy_compliance, decision, status, human_approval } = report

  return (
    <>
      <Link to="/">&larr; Back to reports</Link>

      <div className="page-head" style={{ marginTop: 16 }}>
        <h1>{use_case.name}</h1>
        <span className={`badge badge-${status}`}>{status.replaceAll('_', ' ')}</span>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="card">
        <h2>Use case</h2>
        <dl className="kv-list">
          <dt>Owner</dt>
          <dd>{use_case.owner}</dd>
          <dt>Description</dt>
          <dd>{use_case.description}</dd>
          <dt>Data classification</dt>
          <dd>{use_case.data_classification || '—'}</dd>
          <dt>Deployment context</dt>
          <dd>{use_case.deployment_context || '—'}</dd>
          <dt>Autonomy level</dt>
          <dd>{use_case.autonomy_level || '—'}</dd>
          <dt>Deployment status</dt>
          <dd>{use_case.deployment_status}</dd>
          <dt>Control-routing flags</dt>
          <dd>
            <ActiveFlags useCase={use_case} />
          </dd>
        </dl>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Risk assessment</h2>
          <span className={`badge badge-${risk_assessment.risk_level}`}>
            {risk_assessment.risk_level} · {risk_assessment.risk_score}/100
          </span>
        </div>
        <h3>Risk factors</h3>
        <List items={risk_assessment.risk_factors} />
        <p className="rationale">{risk_assessment.rationale}</p>
        <h3 style={{ marginTop: 12 }}>Detected risk register</h3>
        <RiskRegister detectedRisks={risk_assessment.detected_risks} />
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Policy compliance</h2>
          <span className={`badge badge-${policy_compliance.status}`}>
            {policy_compliance.status.replaceAll('_', ' ')}
          </span>
        </div>
        <p className="rationale">{policy_compliance.rationale}</p>
        <h3 style={{ marginTop: 12 }}>Routed controls</h3>
        <ControlEvaluations evaluations={policy_compliance.control_evaluations} />
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Decision</h2>
          <span className={`badge badge-${decision.decision}`}>
            {decision.decision.replaceAll('_', ' ')}
          </span>
        </div>
        <h3>Conditions</h3>
        <List items={decision.conditions} />
        <p className="rationale">{decision.rationale}</p>
      </div>

      {human_approval && (
        <div className="card">
          <h2>Human decision</h2>
          <dl className="kv-list">
            <dt>Decision</dt>
            <dd>{human_approval.approved ? 'Approved' : 'Rejected'}</dd>
            <dt>Approver</dt>
            <dd>{human_approval.approver}</dd>
            <dt>Notes</dt>
            <dd>{human_approval.notes || '—'}</dd>
            <dt>Decided at</dt>
            <dd>{new Date(human_approval.decided_at).toLocaleString()}</dd>
          </dl>
        </div>
      )}

      {status === 'pending_human_approval' && (
        <div className="card">
          <h2>Record human decision</h2>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="approver">Approver</label>
              <input
                id="approver"
                value={approver}
                onChange={(event) => setApprover(event.target.value)}
                placeholder="you@example.com"
              />
            </div>
            <div className="field span-2">
              <label htmlFor="notes">Notes (optional)</label>
              <textarea
                id="notes"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </div>
          </div>
          <div className="form-actions">
            <button
              className="btn btn-primary"
              disabled={deciding}
              onClick={() => handleDecision(true)}
            >
              Approve
            </button>
            <button
              className="btn btn-danger"
              disabled={deciding}
              onClick={() => handleDecision(false)}
            >
              Reject
            </button>
          </div>
        </div>
      )}
    </>
  )
}
