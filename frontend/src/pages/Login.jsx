import { useState } from 'react'
import { Building2, Gavel, Scale, ShieldCheck } from 'lucide-react'

import GovernAILogo from '../components/Logo.jsx'
import { SIGNED_IN_KEY } from '../auth.js'

export default function Login({ onSignedIn }) {
  const [email, setEmail] = useState('lujain@acme.com')
  const [password, setPassword] = useState('governai')

  function continueIn(event) {
    event?.preventDefault()
    localStorage.setItem(SIGNED_IN_KEY, '1')
    onSignedIn?.()
  }

  return (
    <div className="login-page">
      <div className="login-hero">
        <div className="login-hero-top">
          <GovernAILogo size={34} gradientId="login-logo" />
          <span className="login-hero-brand">
            Govern<span className="brand-ai">AI</span>
          </span>
        </div>

        <div className="login-hero-mid">
          <div className="login-hero-kicker">Multi-agent governance</div>
          <h1 className="login-hero-title">
            Every AI use case reviewed before it ships.
          </h1>
          <p className="login-hero-sub">
            Risk assessment, policy compliance and a decision — three agents,
            real tools, an append-only audit trail.
          </p>

          <div className="login-hero-pills">
            <span className="login-pill login-pill-teal">
              <ShieldCheck size={13} />
              Risk agent
            </span>
            <span className="login-pill login-pill-violet">
              <Scale size={13} />
              Policy agent
            </span>
            <span className="login-pill login-pill-neutral">
              <Gavel size={13} />
              Decision agent
            </span>
          </div>
        </div>

        <div className="login-hero-foot">
          POL-001 – POL-140 · audit_log.jsonl · OpenAI
        </div>
      </div>

      <div className="login-panel">
        <form className="login-form" onSubmit={continueIn}>
          <h2>Sign in</h2>
          <p className="login-form-sub">Governance, risk &amp; compliance workspace</p>

          <div className="field">
            <label htmlFor="login-email">Work email</label>
            <input
              id="login-email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="login-password">Password</label>
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>

          <button className="login-submit" type="submit">
            Sign in
          </button>

          <div className="login-divider">
            <span />
            <em>or</em>
            <span />
          </div>

          <button className="login-sso" type="button" onClick={continueIn}>
            <Building2 size={15} />
            Continue with SSO
          </button>
        </form>
      </div>
    </div>
  )
}
