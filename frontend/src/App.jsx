import { useEffect, useState } from 'react'
import {
  Activity,
  Bell,
  ChevronRight,
  Files,
  History,
  LayoutGrid,
  LogOut,
  Plus,
  PlusCircle,
  Scale,
  Workflow,
  Zap,
} from 'lucide-react'
import { NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import GlobalSearch from './components/GlobalSearch.jsx'
import GovernAILogo from './components/Logo.jsx'
import { getHealthReport } from './api.js'
import { getSession, onAuthStateChange, signOut } from './auth.js'
import Login from './pages/Login.jsx'
import Overview from './pages/Overview.jsx'
import Reports from './pages/Reports.jsx'
import SubmitUseCase from './pages/SubmitUseCase.jsx'
import PipelineRun from './pages/PipelineRun.jsx'
import StepwiseRun from './pages/StepwiseRun.jsx'
import ReportDetail from './pages/ReportDetail.jsx'
import Policies from './pages/Policies.jsx'
import AuditLog from './pages/AuditLog.jsx'
import Monitoring from './pages/Monitoring.jsx'

import './App.css'

const NAV_MAIN = [
  { to: '/', label: 'Overview', icon: LayoutGrid, end: true },
  { to: '/reports', label: 'Reports', icon: Files },
  { to: '/submit', label: 'Submit use case', icon: PlusCircle },
  { to: '/pipeline-run', label: 'Pipeline runs', icon: Workflow },
]

const NAV_GOV = [
  { to: '/policies', label: 'Policies', icon: Scale },
  { to: '/audit-log', label: 'Audit log', icon: History },
  { to: '/monitoring', label: 'Monitoring', icon: Activity },
]

const TITLES = {
  '/': 'Overview',
  '/reports': 'Reports',
  '/submit': 'Submit use case',
  '/pipeline-run': 'Pipeline run',
  '/policies': 'Policies',
  '/audit-log': 'Audit log',
  '/monitoring': 'Monitoring',
}

function pageTitle(pathname) {
  if (TITLES[pathname]) return TITLES[pathname]
  if (pathname.startsWith('/use-cases/')) return 'Report detail'
  if (pathname.startsWith('/pipeline-run/')) return 'Pipeline run'
  return 'GovernAI'
}

const HEALTH_TONE = { HEALTHY: 'tone-teal', DEGRADED: 'tone-amber', CRITICAL: 'tone-rose' }
const HEALTH_REFRESH_MS = 60_000

function AppShell({ user, onSignOut }) {
  const navigate = useNavigate()
  const location = useLocation()
  const [health, setHealth] = useState(null)

  useEffect(() => {
    let cancelled = false

    function refresh() {
      getHealthReport(24)
        .then((report) => {
          if (!cancelled) setHealth(report)
        })
        .catch(() => {
          // keep the last good report; the card says "unavailable" until one
          // loads, and the next tick retries
        })
    }

    refresh()
    const timer = setInterval(refresh, HEALTH_REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <NavLink to="/" className="sidebar-brand">
          <GovernAILogo size={26} gradientId="sidebar-logo" />
          <span className="sidebar-brand-text">
            Govern<span className="brand-ai">AI</span>
          </span>
        </NavLink>

        <div className="nav-group-label">Workspace</div>
        <nav className="sidebar-nav">
          {NAV_MAIN.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
            >
              <item.icon size={16} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="nav-group-label">Governance</div>
        <nav className="sidebar-nav">
          {NAV_GOV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
            >
              <item.icon size={16} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-spacer" />

        <div
          className="agents-online-card"
          style={{ cursor: 'pointer' }}
          onClick={() => navigate('/monitoring')}
        >
          <div className="agents-online-head">
            <Zap size={13} className={health ? HEALTH_TONE[health.status] : 'tone-faint'} />
            System health
          </div>
          <div className="agents-online-note">
            {health
              ? `${health.status} · ${health.summary.runs} agent runs in 24h`
              : 'Health unavailable'}
          </div>
        </div>

        <div className="sidebar-user-row">
          <div className="sidebar-avatar">
            {(user?.email || 'GA').slice(0, 2).toUpperCase()}
          </div>
          <div className="sidebar-user-text">
            <div className="sidebar-user-name">{user?.email || 'Governance Team'}</div>
            <div className="sidebar-user-role">GRC Lead</div>
          </div>
          <LogOut size={15} className="sidebar-signout" onClick={onSignOut} />
        </div>
      </aside>

      <div className="main-col">
        <header className="topbar">
          <div className="breadcrumb">
            <span>GovernAI</span>
            <ChevronRight size={11} />
            <span className="breadcrumb-current">{pageTitle(location.pathname)}</span>
          </div>
          <div className="topbar-spacer" />
          <GlobalSearch />
          <div className="topbar-bell">
            <Bell size={15} />
            <span className="topbar-bell-dot" />
          </div>
          <button className="topbar-cta" onClick={() => navigate('/submit')}>
            <Plus size={14} />
            New use case
          </button>
        </header>

        <main className="app-main">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/submit" element={<SubmitUseCase />} />
            <Route path="/pipeline-run" element={<PipelineRun />} />
            <Route path="/pipeline-run/:id" element={<StepwiseRun />} />
            <Route path="/policies" element={<Policies />} />
            <Route path="/audit-log" element={<AuditLog />} />
            <Route path="/monitoring" element={<Monitoring />} />
            <Route path="/use-cases/:id" element={<ReportDetail />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

function App() {
  const [session, setSession] = useState(undefined) // undefined = loading

  useEffect(() => {
    getSession().then(setSession)
    return onAuthStateChange(setSession)
  }, [])

  if (session === undefined) {
    return null
  }

  if (!session) {
    return <Login />
  }

  return <AppShell user={session.user} onSignOut={signOut} />
}

export default App
