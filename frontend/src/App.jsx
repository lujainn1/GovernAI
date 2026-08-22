import { NavLink, Route, Routes } from 'react-router-dom'
import ReportList from './pages/ReportList.jsx'
import ReportDetail from './pages/ReportDetail.jsx'
import SubmitUseCase from './pages/SubmitUseCase.jsx'
import './App.css'

function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>GovernAI</h1>
        <nav>
          <NavLink to="/" end>
            Reports
          </NavLink>
          <NavLink to="/submit">Submit use case</NavLink>
        </nav>
      </header>

      <main className="app-main">
        <Routes>
          <Route path="/" element={<ReportList />} />
          <Route path="/submit" element={<SubmitUseCase />} />
          <Route path="/use-cases/:id" element={<ReportDetail />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
