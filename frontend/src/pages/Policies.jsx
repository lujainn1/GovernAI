import { useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'

import { createPolicy, listPolicies } from '../api.js'
import { riskTone } from '../format.js'

const emptyForm = {
  id: '',
  title: '',
  category: '',
  description: '',
  status: 'active',
  coverage: 80,
  min_risk_level: 'low',
}

export default function Policies() {
  const [policies, setPolicies] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showModal, setShowModal] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState(emptyForm)

  useEffect(() => {
    listPolicies()
      .then(setPolicies)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  function updateField(field) {
    return (event) =>
      setForm((current) => ({ ...current, [field]: event.target.value }))
  }

  async function handleAddPolicy(event) {
    event.preventDefault()
    setSaving(true)
    setError(null)

    const payload = {
      id: form.id.trim(),
      title: form.title.trim(),
      category: form.category.trim(),
      description: form.description.trim(),
      status: form.status,
      coverage: Number(form.coverage),
      min_risk_level: form.min_risk_level,
    }

    try {
      const created = await createPolicy(payload)
      setPolicies((current) => [...current, created])
      setForm(emptyForm)
      setShowModal(false)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="loading">Loading governance policies…</div>
  }

  return (
    <div className="page-rise">
      <div className="ov-head">
        <div>
          <h1>Policy repository</h1>
          <p className="ov-sub">
            {policies.length} organizational policies · read by the Policy Compliance Agent
            via <span className="mono tone-violet-text">get_policies</span>
          </p>
        </div>
        <button className="btn-outline" onClick={() => setShowModal(true)}>
          <Plus size={13} />
          Add policy
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="policy-cards-grid">
        {policies.map((p) => (
          <article className="policy-card-v2" key={p.id}>
            <div className="policy-card-v2-head">
              <span className="mono policy-id-chip">{p.id}</span>
              <span className="policy-card-v2-title">{p.title}</span>
              <span className={`pill pill-${riskTone(p.min_risk_level)}`}>
                {p.min_risk_level}
              </span>
            </div>
            <p className="policy-card-v2-desc">{p.description}</p>
            <div className="tag-row">
              {(p.requires || []).map((r) => (
                <span className="requires-tag mono" key={r}>
                  {r}
                </span>
              ))}
            </div>
          </article>
        ))}
      </div>

      {showModal && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setShowModal(false)
          }}
        >
          <div className="modal-card">
            <div className="modal-head">
              <div>
                <h2>Add governance policy</h2>
                <p className="ov-empty-note">
                  This policy will be stored in the backend policy repository.
                </p>
              </div>
              <button className="modal-close" type="button" onClick={() => setShowModal(false)}>
                <X size={16} />
              </button>
            </div>

            <form className="modal-form" onSubmit={handleAddPolicy}>
              <div className="field">
                <label htmlFor="policy-id">Policy ID</label>
                <input
                  id="policy-id"
                  required
                  placeholder="e.g. POL-141"
                  value={form.id}
                  onChange={updateField('id')}
                />
              </div>

              <div className="field">
                <label htmlFor="policy-title">Title</label>
                <input
                  id="policy-title"
                  required
                  placeholder="e.g. Data Minimization"
                  value={form.title}
                  onChange={updateField('title')}
                />
              </div>

              <div className="field">
                <label htmlFor="policy-category">Category</label>
                <input
                  id="policy-category"
                  required
                  placeholder="e.g. data_privacy"
                  value={form.category}
                  onChange={updateField('category')}
                />
              </div>

              <div className="field">
                <label htmlFor="policy-risk">Minimum risk level</label>
                <select
                  id="policy-risk"
                  value={form.min_risk_level}
                  onChange={updateField('min_risk_level')}
                >
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="critical">critical</option>
                </select>
              </div>

              <div className="field span-2">
                <label htmlFor="policy-description">Description</label>
                <textarea
                  id="policy-description"
                  required
                  placeholder="Describe the policy requirement…"
                  value={form.description}
                  onChange={updateField('description')}
                />
              </div>

              <div className="modal-actions">
                <button className="btn-outline" type="button" onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button className="btn-cta" type="submit" disabled={saving}>
                  {saving ? 'Saving…' : 'Save policy'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
