const BASE_URL = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
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
   GOVERNANCE REPORTS
========================================================= */

export function listReports() {
  return request('/use-cases')
}

export function getReport(useCaseId) {
  return request(`/use-cases/${useCaseId}`)
}

export function submitUseCase(payload) {
  return request('/use-cases', {
    method: 'POST',
    body: JSON.stringify(payload),
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

export function approveUseCase(
  useCaseId,
  { approved, approver, notes }
) {
  return request(`/use-cases/${useCaseId}/approve`, {
    method: 'POST',
    body: JSON.stringify({
      approved,
      approver,
      notes,
    }),
  })
}

/* =========================================================
   POLICIES
========================================================= */

export function listPolicies() {
  return request('/policies')
}

export function createPolicy(payload) {
  return request('/policies', {
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
   AUDIT LOG
========================================================= */

export function getAuditLog(useCaseId) {
  const query = useCaseId
    ? `?use_case_id=${encodeURIComponent(useCaseId)}`
    : ''

  return request(`/audit-log${query}`)
}