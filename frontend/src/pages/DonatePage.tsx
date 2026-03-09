export function DonatePage() {
  const card: React.CSSProperties = {
    background: 'var(--bg-card)', borderRadius: 'var(--radius-md)',
    padding: 20, marginBottom: 16, border: '1px solid var(--border)',
  }

  return (
    <div style={{ maxWidth: 680 }}>
      <h1 style={{ margin: '0 0 20px', fontSize: 22 }}>Support RadianceFleet</h1>

      <div style={card}>
        <h2 style={{ margin: '0 0 10px', fontSize: 18 }}>Mission</h2>
        <p style={{ margin: '0 0 8px', fontSize: 14, lineHeight: 1.6 }}>
          RadianceFleet is open source maritime anomaly detection for journalists, OSINT researchers,
          and NGO analysts investigating the Russian shadow oil fleet. We process AIS data from
          multiple sources to surface suspicious vessel behavior for human investigation.
        </p>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.6 }}>
          Outputs are anomaly indicators for human investigation -- not legal determinations.
          All findings require independent verification before publication.
        </p>
      </div>

      <div style={card}>
        <h2 style={{ margin: '0 0 12px', fontSize: 18 }}>Infrastructure Costs</h2>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
          <tbody>
            {[
              ['Railway web service (FastAPI backend)', '~$5-10/month'],
              ['Railway PostgreSQL addon', '~$5-10/month'],
              ['Railway cron service (4-hour data updates)', '~$2-5/month'],
              ['Cloudflare Pages (frontend)', 'Free'],
            ].map(([item, cost]) => (
              <tr key={item} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '8px 0', color: 'var(--text-muted)' }}>{item}</td>
                <td style={{ padding: '8px 0', textAlign: 'right', fontWeight: 600 }}>{cost}</td>
              </tr>
            ))}
            <tr>
              <td style={{ padding: '10px 0 0', fontWeight: 700 }}>Total</td>
              <td style={{ padding: '10px 0 0', textAlign: 'right', fontWeight: 700, color: 'var(--accent)' }}>~$12-25/month</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div style={card}>
        <h2 style={{ margin: '0 0 12px', fontSize: 18 }}>How to Support</h2>
        <h3 style={{ margin: '0 0 6px', fontSize: 15, color: 'var(--text-muted)' }}>Donations</h3>
        <p style={{ margin: '0 0 14px', fontSize: 14, lineHeight: 1.6 }}>
          Support via <strong>Open Collective</strong> or <strong>GitHub Sponsors</strong> to cover
          monthly infrastructure costs. Even $5/month from a few supporters keeps the platform running.
        </p>

        <h3 style={{ margin: '0 0 6px', fontSize: 15, color: 'var(--text-muted)' }}>Institutional Support</h3>
        <p style={{ margin: '0 0 8px', fontSize: 14, lineHeight: 1.6 }}>
          Institutional grant funding is the primary sustainability path. We welcome partnerships
          with press freedom, investigative journalism, and accountability organizations.
        </p>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)' }}>
          If you represent a journalism or human rights organization interested in partnering,
          reach out via GitHub Issues.
        </p>
      </div>

      <div style={card}>
        <h2 style={{ margin: '0 0 10px', fontSize: 18 }}>Data Sources</h2>
        <p style={{ margin: '0 0 8px', fontSize: 14 }}>Data updated every 4-6 hours from:</p>
        <ul style={{ fontSize: 14, lineHeight: 1.9, paddingLeft: 20, margin: 0 }}>
          <li><strong>Global Fishing Watch (GFW)</strong> -- dark gaps, vessel encounters, port visits (1-3 day lag)</li>
          <li><strong>Kystverket</strong> -- Norwegian AIS (North Sea, Baltic, Barents Sea, real-time)</li>
          <li><strong>Digitraffic</strong> -- Finnish AIS (Baltic, Gulf of Finland, near real-time)</li>
          <li><strong>CREA Russia Fossil Tracker</strong> -- Russian oil export tracking (daily)</li>
        </ul>
      </div>
    </div>
  )
}
