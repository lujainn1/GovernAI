import { supabase } from './supabaseClient.js'

export function getSession() {
  return supabase.auth.getSession().then(({ data }) => data.session)
}

export function onAuthStateChange(callback) {
  const {
    data: { subscription },
  } = supabase.auth.onAuthStateChange((_event, session) => callback(session))

  return () => subscription.unsubscribe()
}

export async function signInWithPassword(email, password) {
  const { data, error } = await supabase.auth.signInWithPassword({ email, password })
  if (error) throw error
  return data.session
}

export async function signUpWithPassword(email, password) {
  const { data, error } = await supabase.auth.signUp({ email, password })
  if (error) throw error
  return data.session
}

export async function signInWithGoogle() {
  const { error } = await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: window.location.origin },
  })
  if (error) throw error
}

export async function signOut() {
  await supabase.auth.signOut()
}
