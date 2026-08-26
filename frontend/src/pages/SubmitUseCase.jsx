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
  personal_data: false,
  sensitive_data: false,
  generative_ai: false,
  external_provider: false,
  data_outside_ksa: false,
  high_impact_decision: false,
  user_facing_chat: false,
  research_purpose: false,
  deployment_status: 'development',
}

const ROUTING_FLAGS = [
  {
    field: 'personal_data',
    label: 'Processes personal data',
    hint: 'Activates the PDPL control module',
  },
  {
    field: 'sensitive_data',
    label: 'Processes sensitive personal data',
    hint: 'Health, biometric, genetic, or other sensitive categories',
  },
  {
    field: 'generative_ai',
    label: 'Uses generative AI / an LLM',
    hint: 'Activates the GenAI control module',
  },
  {
    field: 'external_provider',
    label: 'Relies on an external vendor/API',
    hint: 'Third-party model, cloud, or data provider',
  },
  {
    field: 'data_outside_ksa',
    label: 'Data leaves or is accessible outside KSA',
    hint: 'Activates cross-border transfer controls (with personal data)',
  },
  {
    field: 'high_impact_decision',
    label: 'Affects rights or opportunities',
    hint: 'Employment, credit, access, or similarly consequential decisions',
  },
  {
    field: 'user_facing_chat',
    label: 'User-facing chatbot / interactive AI',
    hint: 'Activates AI-disclosure and transparency controls',
  },
  {
    field: 'research_purpose',
    label: 'Research or statistical purpose',
    hint: 'Activates research-specific data-handling controls',
  },
]

export default function SubmitUseCase() {
  const [form, setForm] = useState(initialForm)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const navigate = useNavigate()

  function updateField(field) {
    return (event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))
  }

  function toggleFlag(field) {
    return (event) => setForm((prev) => ({ ...prev, [field]: event.target.checked }))
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
        personal_data: form.personal_data,
        sensitive_data: form.sensitive_data,
        generative_ai: form.generative_ai,
        external_provider: form.external_provider,
        data_outside_ksa: form.data_outside_ksa,
        high_impact_decision: form.high_impact_decision,
        user_facing_chat: form.user_facing_chat,
        research_purpose: form.research_purpose,
        deployment_status: form.deployment_status,
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
        Runs the full agent pipeline: deterministic risk detection, a control review scoped
        by the flags below, then a decision.
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

          <div className="field">
            <label htmlFor="deployment_status">Deployment status</label>
            <select
              id="deployment_status"
              value={form.deployment_status}
              onChange={updateField('deployment_status')}
            >
              <option value="development">development</option>
              <option value="production">production</option>
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

        <h2 style={{ marginTop: 32 }}>Control-routing flags</h2>
        <p className="hint" style={{ marginBottom: 12 }}>
          These deterministically select which SDAIA control modules and named risks apply —
          they are not left to the agents' judgment.
        </p>
        <div className="checkbox-grid">
          {ROUTING_FLAGS.map(({ field, label, hint }) => (
            <label className="checkbox-field" key={field}>
              <input type="checkbox" checked={form[field]} onChange={toggleFlag(field)} />
              <span>
                <strong>{label}</strong>
                <span className="hint">{hint}</span>
              </span>
            </label>
          ))}
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
