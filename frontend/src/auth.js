// Cosmetic-only session flag. There is no backend authentication yet — this
// just gates the sign-in screen so returning visitors skip it.
export const SIGNED_IN_KEY = 'governai_signed_in'

export function isSignedIn() {
  return localStorage.getItem(SIGNED_IN_KEY) === '1'
}

export function signOut() {
  localStorage.removeItem(SIGNED_IN_KEY)
}
