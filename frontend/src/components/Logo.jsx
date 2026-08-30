// GovernAI brand mark — shield with an open-aperture "G", teal-to-violet
// gradient stroke, matching the published design canvas exactly.
export default function GovernAILogo({ size = 26, gradientId = 'governai-logo-grad' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" fill="none" aria-hidden="true">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="40" x2="40" y2="0">
          <stop offset="0" stopColor="#2de2c5" />
          <stop offset="1" stopColor="#8b5cf6" />
        </linearGradient>
      </defs>
      <path
        d="M20 3.5 34.5 9v11.4c0 8.4-5.7 14.2-14.5 17.1C11.2 34.6 5.5 28.8 5.5 20.4V9L20 3.5Z"
        stroke={`url(#${gradientId})`}
        strokeWidth="2.4"
        fill="rgba(139,92,246,.08)"
      />
      <path
        d="M26 16.4A7.6 7.6 0 1 0 27.2 22h-6.6"
        stroke={`url(#${gradientId})`}
        strokeWidth="2.6"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  )
}
