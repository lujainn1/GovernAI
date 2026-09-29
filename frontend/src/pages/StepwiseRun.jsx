import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { CheckCircle2, CheckSquare, LoaderCircle, XCircle } from 'lucide-react'

import { AGENT_META } from '../agentMeta.js'
import StepStatus from '../components/StepStatus.jsx'
import {
  decidePipelineStep,
  getAuditLog,
  getPipelineRun,
  startPipelineRun,
} from '../api.js'
import { complianceTone, decisionTone, pretty, riskTone, timeAgo } from '../format.js'

const RUN_PILL = {
  awaiting_step_approval: { tone: 'amber', label: 'awaiting your approval' },
  completed: { tone: 'teal', label: 'complete' },
  rejected: { tone: 'rose', label: 'rejected' },
}

function List({ items, empty = 'None' }) {
  if (!items || items.length === 0) return <p className="ov-empty-note">{empty}</p>
  return (
    <div className="conditions-list">
      {items.map((item) => (
        <div className="condition-row" key={item}>
          <CheckSquare size={13} className="tone-violet" />
          {item}
        </div>
      ))}
    </div>
  )
}

function Tags({ items, tone }) {
  if (!items || items.length === 0) return <p className="ov-empty-note">None</p>
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

// What the agent produced, laid out per agent so the person can judge it.
function StepOutput({ step }) {
  const out = step.output

  if (step.agent === 'risk_assessment') {
    return (
      <>
        <div className="step-headline">
          <span className={`pill pill-${riskTone(out.risk_level)}`}>{out.risk_level}</span>
          <span className="mono">{out.risk_score}/100</span>
        </div>
        <div className="detail-label">Risk factors</div>
        <Tags items={out.risk_factors} tone="rose" />
        <p className="agent-rationale-text">{out.rationale}</p>
      </>
    )
  }

  if (step.agent === 'policy_compliance') {
    return (
      <>
        <div className="step-headline">
          <span className={`pill pill-${complianceTone(out.status)}`}>
            {pretty(out.status)}
          </span>
        </div>
        <div className="compliance-two-col">
          <div>
            <div className="detail-label tone-rose-text">Violated · {out.violated_policies.length}</div>
            <Tags items={out.violated_policies} tone="rose" />
          </div>
          <div>
            <div className="detail-label tone-teal-text">Satisfied · {out.satisfied_policies.length}</div>
            <Tags items={out.satisfied_policies} tone="teal" />
          </div>
        </div>
        <p className="agent-rationale-text">{out.rationale}</p>
      </>
    )
  }

  if (step.agent === 'decision') {
    return (
      <>
        <div className="step-headline">
          <span className={`pill pill-${decisionTone(out.decision)}`}>{pretty(out.decision)}</span>
        </div>
        <div className="detail-label">Conditions</div>
        <List items={out.conditions} />
        <p className="agent-rationale-text">{out.rationale}</p>
      </>
    )
  }

  return (
    <>
      <div className="step-headline">
        <span className={`pill pill-${out.verdict === 'approved' ? 'teal' : 'amber'}`}>
          {pretty(out.verdict)}
        </span>
      </div>
      {out.issues.length > 0 && (
        <>
          <div className="detail-label tone-rose-text">Issues</div>
          <List items={out.issues} />
        </>
      )}
      {out.suggestions.length > 0 && (
        <>
          <div className="detail-label">Suggestions</div>
          <List items={out.suggestions} />
        </>
      )}
      <p className="agent-rationale-text">{out.rationale}</p>
    </>
  )
}

// What approving this step will do next (the backend decides; this only says so).
function nextHint(step) {
  switch (step.agent) {
    case 'risk_assessment':
      return 'Approving runs the Policy Compliance agent.'
    case 'policy_compliance':
      return 'Approving runs the Decision agent.'
    case 'decision':
      return 'Approving runs the Review agent.'
    default:
      return step.output.verdict === 'needs_revision'
        ? 'Approving sends the decision back for revision, if a revision is left; otherwise the report is finalized.'
        : 'Approving finalizes the report.'
  }
}

// Runs a use case one agent at a time. Comes in two ways: from the submit
// form (`payload`: start a new run) or from the URL (`:id`: open a run that
// is already paused, e.g. from the runs list).
export default function StepwiseRun({ payload }) {
  const { id: routeId } = useParams()
  const navigate = useNavigate()

  const [run, setRun] = useState(null)
  const [audit, setAudit] = useState([])
  const [error, setError] = useState(null) // the run couldn't be started or loaded
  const [actionError, setActionError] = useState(null) // a decision failed; the step is still pending
  const [busy, setBusy] = useState(false)
  const [notes, setNotes] = useState('')
  const started = useRef(false)

  const useCaseId = run?.use_case.id

  useEffect(() => {
    if (routeId) {
      getPipelineRun(routeId)
        .then(setRun)
        .catch((err) => setError(err.message))
      return
    }
    if (!payload) {
      navigate('/submit', { replace: true })
      return
    }
    if (started.current) return
    started.current = true

    startPipelineRun(payload)
      .then((result) => {
        // Move to the run's own URL so a refresh re-opens this run instead of
        // submitting the form (and starting a second run) again.
        navigate(`/pipeline-run/${result.use_case.id}`, { replace: true })
      })
      .catch((err) => setError(err.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeId])

  useEffect(() => {
    if (!useCaseId) return
    getAuditLog(useCaseId)
      .then(setAudit)
      .catch(() => setAudit([]))
  }, [useCaseId, run?.steps.length, run?.status])

  async function decide(step, approved) {
    setBusy(true)
    setActionError(null)
    try {
      const updated = await decidePipelineStep(run.use_case.id, step.seq, {
        approved,
        notes: notes.trim() || null,
      })
      setRun(updated)
      setNotes('')
    } catch (err) {
      setActionError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (error) {
    return (
      <div className="page-rise pipeline-run">
        <h1>Step-by-step governance run</h1>
        <div className="error-banner" style={{ marginTop: 14 }}>
          {error}
        </div>
        <button className="btn-outline" style={{ marginTop: 14 }} onClick={() => navigate('/submit')}>
          Back to submit
        </button>
      </div>
    )
  }

  if (!run) {
    return (
      <div className="page-rise pipeline-run">
        <h1>Step-by-step governance run</h1>
        <div className="loading">
          {routeId ? 'Loading run…' : 'Running the Risk Assessment agent…'}
        </div>
      </div>
    )
  }

  const pill = RUN_PILL[run.status]
  const pending = run.status === 'awaiting_step_approval' ? run.steps[run.steps.length - 1] : null
  const rejectedStep = run.steps.find((s) => s.status === 'rejected')

  return (
    <div className="page-rise pipeline-run">
      <div className="run-title-row">
        <h1>Step-by-step governance run</h1>
        <span className={`pill pill-${pill.tone}`}>{pill.label}</span>
      </div>
      <p className="ov-sub" style={{ marginBottom: 18 }}>
        {run.use_case.name} · <span className="mono">{run.use_case.id.slice(0, 8)}</span>
        {' · '}
        an agent&apos;s output must be approved before the next agent runs.
      </p>

      <div className="step-list">
        {run.steps.map((step) => {
          const meta = AGENT_META[step.agent]
          const Icon = meta.icon
          const isPending = pending && step.seq === pending.seq
          return (
            <div
              className={`ov-panel step-panel step-panel-${step.status}`}
              key={step.seq}
            >
              <div className="ov-panel-head">
                <span className="agent-tag">
                  <Icon size={13} className="tone-violet" />
                  Step {step.seq} · {meta.name} Agent
                  {step.revision > 0 && ` (revision ${step.revision})`}
                </span>
                <StepStatus step={step} />
              </div>

              <StepOutput step={step} />

              {step.notes && (
                <p className="step-notes">
                  <span className="detail-label">Reviewer notes</span>
                  {step.notes}
                </p>
              )}

              {isPending && (
                <div className="step-decide">
                  <p className="ov-empty-note" style={{ marginBottom: 10 }}>
                    {nextHint(step)}
                  </p>
                  <div className="field">
                    <label htmlFor="step-notes">Notes (optional)</label>
                    <textarea
                      id="step-notes"
                      value={notes}
                      disabled={busy}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder="e.g. risk level looks too low for this data"
                    />
                  </div>
                  {actionError && (
                    <div className="error-banner" style={{ marginBottom: 10 }}>
                      {actionError}
                    </div>
                  )}
                  <div className="decide-actions">
                    <button
                      className="btn-approve"
                      type="button"
                      disabled={busy}
                      onClick={() => decide(step, true)}
                    >
                      {busy ? (
                        <>
                          <LoaderCircle size={13} className="spin" /> Running…
                        </>
                      ) : (
                        'Approve'
                      )}
                    </button>
                    <button
                      className="btn-reject"
                      type="button"
                      disabled={busy}
                      onClick={() => decide(step, false)}
                    >
                      Reject
                    </button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {run.status === 'rejected' && rejectedStep && (
        <div className="run-done-banner run-done-rejected">
          <XCircle size={19} className="tone-rose" />
          <div className="run-done-copy">
            <strong>Rejected at step {rejectedStep.seq}</strong>
            <span>
              The {AGENT_META[rejectedStep.agent].name} output was rejected, so no later agent ran
              and no report was produced.
            </span>
          </div>
          <button className="btn-outline" type="button" onClick={() => navigate('/pipeline-run')}>
            All runs
          </button>
        </div>
      )}

      {run.status === 'completed' && (
        <div className="run-done-banner">
          <CheckCircle2 size={19} className="tone-teal" />
          <div className="run-done-copy">
            <strong>Every step approved</strong>
            <span>
              The report is ready
              {run.report?.status === 'pending_human_approval' && ' and awaits the final sign-off'}.
            </span>
          </div>
          <button
            className="btn-cta"
            type="button"
            onClick={() => navigate(`/use-cases/${run.use_case.id}`)}
          >
            Open report
          </button>
        </div>
      )}

      <div className="run-log-card">
        <div className="run-log-head">audit_log.jsonl</div>
        <div className="run-log-body">
          {audit.length === 0 ? (
            <div className="run-log-line tone-faint">No audit entries yet.</div>
          ) : (
            audit.map((entry) => (
              <div className="run-log-line" key={entry.id}>
                <span className="tone-ghost">{timeAgo(entry.timestamp)}</span>{'  '}
                <span className="tone-violet-text">{entry.stage}</span>{'  '}
                {entry.actor}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
