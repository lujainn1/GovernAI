import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { submitUseCase } from '../api.js'

const initialForm = {
  name: '',
  description: '',
  owner: '',
  data_classification: '',
  deployment_context: '',
  autonomy_level: '',
  documentation: '',
}

export default function SubmitUseCase() {
  const [form, setForm] = useState(initialForm)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const navigate = useNavigate()

  function updateField(field) {
    return (event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const payload = {
        name: form.name,
        description: form.description,
        owner: form.owner,
        data_classification: form.data_classification || null,
        deployment_context: form.deployment_context || null,
        autonomy_level: form.autonomy_level || null,
        documentation: form.documentation || null,
      }
      const report = await submitUseCase(payload)
      navigate(`/use-cases/${report.use_case.id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <h1>Submit an AI use case</h1>
      <p style={{ marginBottom: 24, color: 'var(--text)' }}>
        Runs the full agent pipeline: risk assessment, policy compliance, then a decision.
      </p>

      {error && <div className="error-banner">{error}</div>}

      <form onSubmit={handleSubmit}>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="name">Name</label>
            <input id="name" required value={form.name} onChange={updateField('name')} />
          </div>

          <div className="field">
            <label htmlFor="owner">Owner</label>
            <input id="owner" required value={form.owner} onChange={updateField('owner')} />
          </div>

          <div className="field span-2">
            <label htmlFor="description">Description</label>
            <textarea
              id="description"
              required
              value={form.description}
              onChange={updateField('description')}
            />
          </div>

          <div className="field">
            <label htmlFor="data_classification">Data classification</label>
            <select
              id="data_classification"
              value={form.data_classification}
              onChange={updateField('data_classification')}
            >
              <option value="">— select —</option>
              <option value="public">public</option>
              <option value="internal">internal</option>
              <option value="confidential">confidential</option>
              <option value="restricted">restricted</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="deployment_context">Deployment context</label>
            <select
              id="deployment_context"
              value={form.deployment_context}
              onChange={updateField('deployment_context')}
            >
              <option value="">— select —</option>
              <option value="internal-tool">internal-tool</option>
              <option value="customer-facing">customer-facing</option>
              <option value="autonomous-agent">autonomous-agent</option>
            </select>
          </div>

          <div className="field">
            <label htmlFor="autonomy_level">Autonomy level</label>
            <select
              id="autonomy_level"
              value={form.autonomy_level}
              onChange={updateField('autonomy_level')}
            >
              <option value="">— select —</option>
              <option value="human-in-the-loop">human-in-the-loop</option>
              <option value="human-on-the-loop">human-on-the-loop</option>
              <option value="fully-autonomous">fully-autonomous</option>
            </select>
          </div>

          <div className="field span-2">
            <label htmlFor="documentation">
              Documentation <span className="hint">(design docs / DPIA excerpt, optional)</span>
            </label>
            <textarea
              id="documentation"
              value={form.documentation}
              onChange={updateField('documentation')}
            />
          </div>
        </div>

        <div className="form-actions">
          <button className="btn btn-primary" type="submit" disabled={submitting}>
            {submitting ? 'Running agents…' : 'Submit for governance review'}
          </button>
        </div>
      </form>
    </>
  )
}
