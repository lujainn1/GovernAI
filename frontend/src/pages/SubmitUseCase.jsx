import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  FileSpreadsheet,
  FileText,
  FileWarning,
  Loader2,
  Play,
  Presentation,
  UploadCloud,
  Wand2,
  X,
} from 'lucide-react'

import { extractDocument } from '../api.js'

const initialForm = {
  name: '',
  description: '',
  owner: '',
  data_classification: '',
  deployment_context: '',
  autonomy_level: '',
}

const PIPELINE_PREVIEW = [
  { title: 'Intake logged', sub: 'Use case recorded to the audit trail.' },
  {
    title: 'Risk Assessment Agent',
    sub: 'Scores likelihood × impact against the risk library.',
  },
  {
    title: 'Policy Compliance Agent',
    sub: 'Checks applicable controls via get_policies.',
  },
  {
    title: 'Decision Agent',
    sub: 'Combines both findings into approve / review / block.',
  },
  { title: 'Report finalized', sub: 'Persisted and ready for review.' },
]

const ACCEPTED_EXTENSIONS = ['pdf', 'docx', 'pptx', 'xlsx', 'md', 'txt']
const MAX_FILE_SIZE = 25 * 1024 * 1024

const FILE_ICON = {
  pdf: FileText,
  docx: FileText,
  md: FileText,
  txt: FileText,
  pptx: Presentation,
  xlsx: FileSpreadsheet,
}

function extOf(name) {
  return (name.split('.').pop() || '').toLowerCase()
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

let nextDocId = 1

export default function SubmitUseCase() {
  const [form, setForm] = useState(initialForm)
  const [docs, setDocs] = useState([])
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef(null)
  const navigate = useNavigate()

  function updateField(field) {
    return (event) =>
      setForm((prev) => ({ ...prev, [field]: event.target.value }))
  }

  function addFiles(fileList) {
    const files = Array.from(fileList || [])

    files.forEach((file) => {
      const ext = extOf(file.name)
      const id = nextDocId++

      if (!ACCEPTED_EXTENSIONS.includes(ext)) {
        setDocs((prev) => [
          ...prev,
          { id, name: file.name, size: file.size, ext, state: 'error', error: 'Unsupported file type' },
        ])
        return
      }

      if (file.size > MAX_FILE_SIZE) {
        setDocs((prev) => [
          ...prev,
          { id, name: file.name, size: file.size, ext, state: 'error', error: 'Exceeds 25 MB limit' },
        ])
        return
      }

      setDocs((prev) => [
        ...prev,
        { id, name: file.name, size: file.size, ext, state: 'parsing' },
      ])

      extractDocument(file)
        .then((result) => {
          setDocs((prev) =>
            prev.map((d) =>
              d.id === id
                ? { ...d, state: 'parsed', text: result.text, wordCount: result.word_count, pages: result.pages }
                : d
            )
          )
        })
        .catch((err) => {
          setDocs((prev) =>
            prev.map((d) => (d.id === id ? { ...d, state: 'error', error: err.message } : d))
          )
        })
    })
  }

  function removeDoc(id) {
    setDocs((prev) => prev.filter((d) => d.id !== id))
  }

  function handleSubmit(event) {
    event.preventDefault()

    const parsedSections = docs
      .filter((d) => d.state === 'parsed' && d.text)
      .map((d) => `--- ${d.name} ---\n${d.text}`)

    const documentation = parsedSections.join('\n\n')

    const payload = {
      name: form.name,
      description: form.description,
      owner: form.owner,
      data_classification: form.data_classification || null,
      deployment_context: form.deployment_context || null,
      autonomy_level: form.autonomy_level || null,
      documentation: documentation || null,
    }

    navigate('/pipeline-run', { state: { payload } })
  }

  const hasDocs = docs.length > 0

  return (
    <div className="page-rise submit-page">
      <h1>Submit an AI use case</h1>
      <p className="ov-sub" style={{ marginBottom: 16 }}>
        Runs the full agent pipeline: risk assessment, policy compliance, then a decision.
      </p>

      <div className="submit-grid">
        <form className="submit-panel" onSubmit={handleSubmit}>
          <div className="submit-fields">
            <div className="field">
              <label htmlFor="name">Name</label>
              <input
                id="name"
                required
                placeholder="e.g. Automated Loan Denial Assistant"
                value={form.name}
                onChange={updateField('name')}
              />
            </div>

            <div className="field">
              <label htmlFor="owner">Owner</label>
              <input
                id="owner"
                required
                placeholder="e.g. lending-platform-team"
                value={form.owner}
                onChange={updateField('owner')}
              />
            </div>

            <div className="field span-2">
              <label htmlFor="description">Description</label>
              <textarea
                id="description"
                required
                placeholder="Describe what the AI system does, who uses it, and what decisions it produces…"
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
                <option value="">Select classification</option>
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
                <option value="">Select deployment</option>
                <option value="internal-tool">internal-tool</option>
                <option value="customer-facing">customer-facing</option>
                <option value="autonomous-agent">autonomous-agent</option>
              </select>
            </div>

            <div className="field span-2">
              <label htmlFor="autonomy_level">Autonomy level</label>
              <div className="autonomy-options">
                {[
                  { value: 'human-in-the-loop', title: 'Human in the loop', sub: 'reviews each output' },
                  { value: 'human-on-the-loop', title: 'Human on the loop', sub: 'monitors, can halt' },
                  { value: 'fully-autonomous', title: 'Fully autonomous', sub: 'no human step' },
                ].map((opt) => (
                  <div
                    key={opt.value}
                    className={
                      form.autonomy_level === opt.value
                        ? 'autonomy-option active'
                        : 'autonomy-option'
                    }
                    onClick={() =>
                      setForm((prev) => ({ ...prev, autonomy_level: opt.value }))
                    }
                  >
                    <div className="autonomy-title">{opt.title}</div>
                    <div className="autonomy-sub">{opt.sub}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="field span-2">
              <label htmlFor="doc-upload">
                Documentation{' '}
                <span className="hint">— design docs, DPIA, model card, vendor DPA</span>
              </label>

              <input
                ref={fileInputRef}
                id="doc-upload"
                type="file"
                multiple
                accept=".pdf,.docx,.pptx,.xlsx,.md,.txt"
                style={{ display: 'none' }}
                onChange={(event) => {
                  addFiles(event.target.files)
                  event.target.value = ''
                }}
              />

              <div
                className={dragOver ? 'dropzone dropzone-active' : 'dropzone'}
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(event) => {
                  event.preventDefault()
                  setDragOver(true)
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(event) => {
                  event.preventDefault()
                  setDragOver(false)
                  addFiles(event.dataTransfer.files)
                }}
              >
                <UploadCloud size={20} className="tone-violet" />
                <div className="dropzone-text">
                  Drop files here or <span className="tone-teal-text">browse</span>
                </div>
                <div className="dropzone-hint">
                  PDF, DOCX, PPTX, XLSX, MD, TXT · up to 25 MB each
                </div>
                <div className="dropzone-types">
                  {ACCEPTED_EXTENSIONS.map((t) => (
                    <span key={t} className="mono dropzone-type-tag">
                      .{t}
                    </span>
                  ))}
                </div>
              </div>

              {hasDocs && (
                <div className="doc-list">
                  {docs.map((doc) => {
                    const Icon = FILE_ICON[doc.ext] || FileText
                    return (
                      <div className="doc-row" key={doc.id}>
                        <div className={`doc-icon-box doc-icon-${doc.ext}`}>
                          <Icon size={15} />
                        </div>
                        <div className="doc-meta">
                          <div className="doc-name">{doc.name}</div>
                          <div className="doc-submeta">
                            {formatSize(doc.size)}
                            {doc.pages ? ` · ${doc.pages} pages` : ''}
                            {doc.wordCount ? ` · ${doc.wordCount} words` : ''}
                            {doc.error ? ` · ${doc.error}` : ''}
                          </div>
                        </div>
                        <span className={`doc-badge doc-badge-${doc.state}`}>
                          {doc.state === 'parsing' && <Loader2 size={11} className="spin" />}
                          {doc.state}
                        </span>
                        <X
                          size={14}
                          className="doc-remove"
                          onClick={() => removeDoc(doc.id)}
                        />
                      </div>
                    )
                  })}
                </div>
              )}

              {hasDocs && (
                <div className="dropzone-note">
                  <Wand2 size={13} className="tone-violet" />
                  Parsed text is passed to the Risk and Policy agents via{' '}
                  <span className="mono">analyze_document()</span>
                </div>
              )}

              {docs.some((d) => d.state === 'error') && (
                <div className="dropzone-note dropzone-note-warn">
                  <FileWarning size={13} className="tone-rose" />
                  Some files couldn&apos;t be parsed and will be skipped.
                </div>
              )}
            </div>
          </div>

          <div className="submit-actions">
            <button className="btn-cta" type="submit">
              <Play size={14} />
              Submit for governance review
            </button>
            <span className="submit-actions-note">≈ 30–90 seconds · 3 agents</span>
          </div>
        </form>

        <aside className="submit-preview">
          <h2>What happens next</h2>
          <p className="submit-preview-sub">Each stage writes to the append-only audit log.</p>
          <div className="submit-preview-steps">
            {PIPELINE_PREVIEW.map((p, i) => (
              <div className="submit-preview-step" key={p.title}>
                <div className="submit-preview-dot-col">
                  <span className="submit-preview-dot" />
                  {i < PIPELINE_PREVIEW.length - 1 && (
                    <span className="submit-preview-line" />
                  )}
                </div>
                <div>
                  <div className="submit-preview-title">{p.title}</div>
                  <div className="submit-preview-note">{p.sub}</div>
                </div>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </div>
  )
}
