import { supabase } from './supabaseClient.js'

const BASE_URL = '/api'

async function authHeaders() {
  const {
    data: { session },
  } = await supabase.auth.getSession()

  return session ? { Authorization: `Bearer ${session.access_token}` } : {}
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(await authHeaders()),
    },
    ...options,
  })

  if (!res.ok) {
    let detail = res.statusText

    try {
      const body = await res.json()
      detail = body.detail || detail
    } catch {
      // response had no JSON body
    }

    throw new Error(detail)
  }

  return res.json()
}

/* =========================================================
   LIST CACHE
========================================================= */

// Overview, Reports and the global search each load the same three lists, and
// every page change used to fetch them all again. A list is reused for a short
// time, and requests for one that is already loading share that request.
// Anything that changes what the lists contain clears the cache first, so the
// person's own actions always show up immediately.
const LIST_CACHE_TTL_MS = 30_000
const listCache = new Map() // path -> { at, data } | { inflight }

export function clearApiCache() {
  listCache.clear()
}

supabase.auth.onAuthStateChange((event) => {
  if (event === 'SIGNED_OUT') clearApiCache()
})

function cachedList(path) {
  const entry = listCache.get(path)

  if (entry?.inflight) return entry.inflight
  if (entry && Date.now() - entry.at < LIST_CACHE_TTL_MS) {
    return Promise.resolve(entry.data)
  }

  const inflight = request(path).then(
    (data) => {
      // Only keep the result if nothing cleared the cache while it loaded.
      if (listCache.get(path)?.inflight === inflight) {
        listCache.set(path, { at: Date.now(), data })
      }
      return data
    },
    (err) => {
      if (listCache.get(path)?.inflight === inflight) listCache.delete(path)
      throw err
    },
  )
  listCache.set(path, { inflight })
  return inflight
}

// A write that changes what the lists contain.
async function mutate(path, options) {
  clearApiCache()
  try {
    return await request(path, options)
  } finally {
    // Clear again: a list requested while the write was running may have
    // loaded the old state.
    clearApiCache()
  }
}

/* =========================================================
   GOVERNANCE REPORTS
========================================================= */

export function listReports() {
  return cachedList('/use-cases')
}

export function getReport(useCaseId) {
  return request(`/use-cases/${useCaseId}`)
}

export function submitUseCase(payload) {
  return mutate('/use-cases', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/* =========================================================
   STEP-BY-STEP PIPELINE RUNS (a person approves each agent)
========================================================= */

// Runs only the first agent; its output then waits for approve/reject.
export function startPipelineRun(payload) {
  return mutate('/pipeline-runs', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getPipelineRun(useCaseId) {
  return request(`/pipeline-runs/${useCaseId}`)
}

export function listPipelineRuns() {
  return cachedList('/pipeline-runs')
}

// `seq` is the step being decided, so a stale click can't approve a step the
// person hasn't seen. Approving runs the next agent, which can take a while.
export function decidePipelineStep(useCaseId, seq, { approved, notes }) {
  return mutate(`/pipeline-runs/${useCaseId}/steps/${seq}/decision`, {
    method: 'POST',
    body: JSON.stringify({ approved, notes }),
  })
}

/* =========================================================
   DOCUMENTS
========================================================= */

// Multipart upload — bypasses request()'s JSON Content-Type so the browser
// can set its own multipart boundary.
export async function extractDocument(file) {
  const formData = new FormData()
  formData.append('file', file)

  const res = await fetch(`${BASE_URL}/documents/extract`, {
    method: 'POST',
    headers: await authHeaders(),
    body: formData,
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || detail
    } catch {
      // response had no JSON body
    }
    throw new Error(detail)
  }

  return res.json()
}

// The approver is the signed-in user; the backend takes it from the auth
// token, so it isn't sent here.
export function approveUseCase(useCaseId, { approved, notes }) {
  return mutate(`/use-cases/${useCaseId}/approve`, {
    method: 'POST',
    body: JSON.stringify({
      approved,
      notes,
    }),
  })
}

/* =========================================================
   POLICIES
========================================================= */

export function listPolicies() {
  return cachedList('/policies')
}

export function createPolicy(payload) {
  return mutate('/policies', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/* =========================================================
   RISK RULES
========================================================= */

export function listRiskRules() {
  return request('/risk-rules')
}

export function createRiskRule(payload) {
  return request('/risk-rules', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/* =========================================================
   MONITORING
========================================================= */

export function getMetrics(hours = 24) {
  return request(`/metrics?hours=${hours}`)
}

export function getHealthReport(hours = 24) {
  return request(`/metrics/health-report?hours=${hours}`)
}

/* =========================================================
   AUDIT LOG
========================================================= */

export function getAuditLog(useCaseId) {
  const query = useCaseId
    ? `?use_case_id=${encodeURIComponent(useCaseId)}`
    : ''

  return request(`/audit-log${query}`)
}