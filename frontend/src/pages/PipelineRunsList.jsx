import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { listPipelineRuns } from '../api.js'
import { pretty, timeAgo } from '../format.js'

const RUN_PILL = {
  awaiting_step_approval: { tone: 'amber', label: 'awaiting approval' },
  completed: { tone: 'teal', label: 'complete' },
  rejected: { tone: 'rose', label: 'rejected' },
}

// Where a run stands: the agent whose output is waiting (or was last decided).
function currentStep(run) {
  const last = run.steps[run.steps.length - 1]
  return last ? `Step ${last.seq} · ${pretty(last.agent)}` : '—'
}

export default function PipelineRunsList() {
  const navigate = useNavigate()
  const [runs, setRuns] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    listPipelineRuns()
      .then((data) => {
        if (!cancelled) setRuns(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (error) return <div className="error-banner">{error}</div>
  if (!runs) return <div className="loading">Loading pipeline runs…</div>

  // Runs waiting on a person come first; each group keeps the API's newest-first order.
  const waiting = (run) => (run.status === 'awaiting_step_approval' ? 0 : 1)
  const rows = [...runs].sort((a, b) => waiting(a) - waiting(b))

  return (
    <div className="page-rise">
      <div className="ov-head">
        <div>
          <h1>Pipeline runs</h1>
          <p className="ov-sub">
            Step-by-step runs, where a person approves each agent before the next one runs. Runs
            waiting on you are listed first.
          </p>
        </div>
        <button className="btn-cta" type="button" onClick={() => navigate('/submit')}>
          New use case
        </button>
      </div>

      <div className="reports-table-card">
        {rows.length === 0 ? (
          <div className="empty-state">
            <h3>No step-by-step runs yet</h3>
            <p>Submit a use case with the &quot;Human in the loop&quot; autonomy level to start one.</p>
          </div>
        ) : (
          rows.map((run) => {
            const pill = RUN_PILL[run.status]
            return (
              <div
                className="runs-list-row"
                key={run.use_case.id}
                onClick={() => navigate(`/pipeline-run/${run.use_case.id}`)}
              >
                <span className="reports-name">{run.use_case.name}</span>
                <span className="reports-owner">{run.use_case.owner}</span>
                <span className="mono tone-faint">{currentStep(run)}</span>
                <span>
                  <span className={`pill pill-${pill.tone}`}>{pill.label}</span>
                </span>
                <span className="reports-updated">{timeAgo(run.updated_at)}</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
