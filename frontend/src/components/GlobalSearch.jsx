import { useEffect, useMemo, useRef, useState } from 'react'
import { Files, Scale, Search } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { listPipelineRuns, listPolicies, listReports } from '../api.js'
import { isUnfinishedRun, pretty, runAsRow } from '../format.js'

const MAX_PER_GROUP = 5
const IS_MAC =
  typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform)

function matches(query, ...fields) {
  const q = query.toLowerCase()
  return fields.some((f) => f && String(f).toLowerCase().includes(q))
}

export default function GlobalSearch() {
  const navigate = useNavigate()
  const inputRef = useRef(null)
  const rootRef = useRef(null)
  const requestRef = useRef(0)

  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [reports, setReports] = useState([])
  const [runs, setRuns] = useState([])
  const [policies, setPolicies] = useState([])
  const [loading, setLoading] = useState(false)

  // Ctrl/⌘+K focuses the search from anywhere; "/" does too outside of inputs.
  useEffect(() => {
    function onKeyDown(event) {
      const isShortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k'
      const tag = event.target?.tagName
      const typing =
        tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || event.target?.isContentEditable

      if (isShortcut || (event.key === '/' && !typing)) {
        event.preventDefault()
        inputRef.current?.focus()
        inputRef.current?.select()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  // Close when clicking outside.
  useEffect(() => {
    function onMouseDown(event) {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false)
    }

    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [])

  // Refresh the searchable data every time the search gains focus so newly
  // submitted use cases and policies show up.
  function refresh() {
    const requestId = ++requestRef.current
    setLoading(true)

    Promise.allSettled([listReports(), listPolicies(), listPipelineRuns()]).then(([r, p, u]) => {
      if (requestId !== requestRef.current) return
      if (r.status === 'fulfilled') setReports(r.value)
      if (p.status === 'fulfilled') setPolicies(p.value)
      if (u.status === 'fulfilled') setRuns(u.value)
      setLoading(false)
    })
  }

  const results = useMemo(() => {
    const q = query.trim()
    if (!q) return []

    // Reports, plus step-by-step runs that have no report yet (waiting on a
    // step approval, or rejected at one).
    const useCases = [
      ...reports.map((r) => ({
        use_case: r.use_case,
        status: pretty(r.status),
        to: `/use-cases/${r.use_case.id}`,
      })),
      ...runs.filter(isUnfinishedRun).map((run) => {
        const row = runAsRow(run)
        return { use_case: row.use_case, status: row.statusLabel, to: row.to }
      }),
    ]

    const reportHits = useCases
      .filter((r) =>
        matches(q, r.use_case.name, r.use_case.owner, r.use_case.description, r.use_case.id),
      )
      .slice(0, MAX_PER_GROUP)
      .map((r) => ({
        key: `report-${r.use_case.id}`,
        group: 'Use cases',
        icon: Files,
        title: r.use_case.name,
        meta: `${r.use_case.owner} · ${r.status}`,
        to: r.to,
      }))

    const policyHits = policies
      .filter((p) => matches(q, p.title, p.id, p.category, p.description))
      .slice(0, MAX_PER_GROUP)
      .map((p) => ({
        key: `policy-${p.id}`,
        group: 'Policies',
        icon: Scale,
        title: p.title,
        meta: p.id,
        to: '/policies',
      }))

    return [...reportHits, ...policyHits]
  }, [query, reports, runs, policies])

  function go(result) {
    navigate(result.to)
    setOpen(false)
    setQuery('')
    inputRef.current?.blur()
  }

  function onInputKeyDown(event) {
    if (event.key === 'Escape') {
      if (query) setQuery('')
      else {
        setOpen(false)
        inputRef.current?.blur()
      }
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (results.length) setActive((i) => (i + 1) % results.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (results.length) setActive((i) => (i - 1 + results.length) % results.length)
    } else if (event.key === 'Enter' && results[active]) {
      event.preventDefault()
      go(results[active])
    }
  }

  const showPanel = open && query.trim().length > 0

  return (
    <div className="topbar-search-wrap" ref={rootRef}>
      <div className={open ? 'topbar-search focused' : 'topbar-search'}>
        <Search size={14} />
        <input
          ref={inputRef}
          type="text"
          className="topbar-search-input"
          placeholder="Search use cases, policies…"
          aria-label="Search use cases and policies"
          autoComplete="off"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            setActive(0)
          }}
          onFocus={() => {
            setOpen(true)
            refresh()
          }}
          onKeyDown={onInputKeyDown}
        />
        <span className="topbar-kbd">{IS_MAC ? '⌘K' : 'Ctrl K'}</span>
      </div>

      {showPanel && (
        <div className="search-panel" role="listbox">
          {results.length === 0 ? (
            <div className="search-empty">
              {loading ? 'Searching…' : `No results for “${query.trim()}”`}
            </div>
          ) : (
            results.map((result, index) => (
              <div key={result.key}>
                {(index === 0 || results[index - 1].group !== result.group) && (
                  <div className="search-group-label">{result.group}</div>
                )}
                <div
                  role="option"
                  aria-selected={index === active}
                  className={index === active ? 'search-item active' : 'search-item'}
                  onMouseEnter={() => setActive(index)}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => go(result)}
                >
                  <result.icon size={14} />
                  <span className="search-item-title">{result.title}</span>
                  <span className="search-item-meta">{result.meta}</span>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
