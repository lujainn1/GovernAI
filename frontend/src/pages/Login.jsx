import { useState } from 'react'
import { Gavel, Globe, Scale, ShieldCheck } from 'lucide-react'

import GovernAILogo from '../components/Logo.jsx'
import { signInWithGoogle, signInWithPassword, signUpWithPassword } from '../auth.js'

export default function Login() {
  const [mode, setMode] = useState('sign-in') // 'sign-in' | 'sign-up'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    setInfo('')
    setSubmitting(true)

    try {
      if (mode === 'sign-up') {
        const session = await signUpWithPassword(email, password)
        if (!session) {
          setInfo('Check your inbox to confirm your email, then sign in.')
          setMode('sign-in')
        }
      } else {
        await signInWithPassword(email, password)
      }
    } catch (err) {
      setError(err.message || 'Something went wrong. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleGoogle() {
    setError('')
    try {
      await signInWithGoogle()
    } catch (err) {
      setError(err.message || 'Could not start Google sign-in.')
    }
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
        <form className="login-form" onSubmit={handleSubmit}>
          <h2>{mode === 'sign-up' ? 'Create account' : 'Sign in'}</h2>
          <p className="login-form-sub">Governance, risk &amp; compliance workspace</p>

          <div className="field">
            <label htmlFor="login-email">Work email</label>
            <input
              id="login-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="login-password">Password</label>
            <input
              id="login-password"
              type="password"
              autoComplete={mode === 'sign-up' ? 'new-password' : 'current-password'}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={6}
              required
            />
          </div>

          {error ? <p className="login-form-error">{error}</p> : null}
          {info ? <p className="login-form-info">{info}</p> : null}

          <button className="login-submit" type="submit" disabled={submitting}>
            {submitting
              ? 'Please wait…'
              : mode === 'sign-up'
                ? 'Create account'
                : 'Sign in'}
          </button>

          <div className="login-divider">
            <span />
            <em>or</em>
            <span />
          </div>

          <button className="login-sso" type="button" onClick={handleGoogle}>
            <Globe size={15} />
            Continue with Google
          </button>

          <p className="login-form-switch">
            {mode === 'sign-up' ? (
              <>
                Already have an account?{' '}
                <button type="button" onClick={() => setMode('sign-in')}>
                  Sign in
                </button>
              </>
            ) : (
              <>
                Need an account?{' '}
                <button type="button" onClick={() => setMode('sign-up')}>
                  Create one
                </button>
              </>
            )}
          </p>
        </form>
      </div>
    </div>
  )
}
