import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { submitUseCase } from '../api.js'

const initialForm = {
  name: '',
  description: '',
  owner: '',
  model_provider: '',
  model_name: '',
  data_sources: '',
  data_classification: '',
  permissions: '',
  deployment_context: '',
  autonomy_level: '',
  documentation: '',
}

function toList(value) {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
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
        model_provider: form.model_provider || null,
        model_name: form.model_name || null,
        data_sources: toList(form.data_sources),
        data_classification: form.data_classification || null,
        permissions: toList(form.permissions),
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
            <label htmlFor="model_provider">Model provider</label>
            <input
              id="model_provider"
              placeholder="e.g. openai, anthropic, in-house"
              value={form.model_provider}
              onChange={updateField('model_provider')}
            />
          </div>

          <div className="field">
            <label htmlFor="model_name">Model name</label>
            <input
              id="model_name"
              placeholder="e.g. gpt-4o, claude-sonnet-5"
              value={form.model_name}
              onChange={updateField('model_name')}
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

          <div className="field">
            <label htmlFor="data_sources">
              Data sources <span className="hint">(comma-separated)</span>
            </label>
            <input
              id="data_sources"
              placeholder="e.g. customer_pii_db, crm_export"
              value={form.data_sources}
              onChange={updateField('data_sources')}
            />
          </div>

          <div className="field">
            <label htmlFor="permissions">
              Permissions <span className="hint">(comma-separated)</span>
            </label>
            <input
              id="permissions"
              placeholder="e.g. read_customer_data, send_email"
              value={form.permissions}
              onChange={updateField('permissions')}
            />
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
