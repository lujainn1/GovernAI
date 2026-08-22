import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listReports } from '../api.js'

export default function ReportList() {
  const [reports, setReports] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    listReports()
      .then((data) => {
        if (!cancelled) setReports(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <>
      <div className="page-head">
        <h1>Governance reports</h1>
        <Link className="btn btn-primary" to="/submit">
          + Submit use case
        </Link>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {!reports && !error && <div className="loading">Loading reports…</div>}

      {reports && reports.length === 0 && (
        <div className="empty-state">
          <p>No use cases have been submitted yet.</p>
        </div>
      )}

      {reports && reports.length > 0 && (
        <table className="report-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Owner</th>
              <th>Risk</th>
              <th>Decision</th>
              <th>Status</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {reports.map((report) => (
              <tr key={report.use_case.id}>
                <td>
                  <Link to={`/use-cases/${report.use_case.id}`}>
                    {report.use_case.name}
                  </Link>
                </td>
                <td>{report.use_case.owner}</td>
                <td>
                  <span className={`badge badge-${report.risk_assessment.risk_level}`}>
                    {report.risk_assessment.risk_level}
                  </span>
                </td>
                <td>
                  <span className={`badge badge-${report.decision.decision}`}>
                    {report.decision.decision.replaceAll('_', ' ')}
                  </span>
                </td>
                <td>
                  <span className={`badge badge-${report.status}`}>
                    {report.status.replaceAll('_', ' ')}
                  </span>
                </td>
                <td>{new Date(report.updated_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}
