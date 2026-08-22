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

export function approveUseCase(useCaseId, { approved, approver, notes }) {
  return request(`/use-cases/${useCaseId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ approved, approver, notes }),
  })
}
