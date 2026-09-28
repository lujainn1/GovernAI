// Donut + legend for the approve / human review / block mix. `mix` comes from
// decisionMixFromCounts(). By default the centre shows the auto-approved
// percentage; a page can override the centre text or the ring background.
export default function DecisionDonut({ mix, centerValue, centerLabel, ringBackground }) {
  return (
    <div className="decision-mix-row">
      <div className="decision-donut" style={{ background: ringBackground ?? mix.conic }}>
        <div className="decision-donut-center">
          <span className="decision-donut-pct">{centerValue ?? `${mix.approvePct}%`}</span>
          <span className="decision-donut-label">{centerLabel ?? 'auto-approved'}</span>
        </div>
      </div>
      <div className="decision-legend">
        <div>
          <span className="legend-dot" style={{ background: 'var(--teal)' }} />
          Approve <span className="legend-n">{mix.counts.approve}</span>
        </div>
        <div>
          <span className="legend-dot" style={{ background: 'var(--amber)' }} />
          Human review{' '}
          <span className="legend-n">{mix.counts.require_human_approval}</span>
        </div>
        <div>
          <span className="legend-dot" style={{ background: 'var(--rose)' }} />
          Block <span className="legend-n">{mix.counts.block}</span>
        </div>
      </div>
    </div>
  )
}
