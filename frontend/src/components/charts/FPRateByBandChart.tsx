import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Legend, CartesianGrid } from 'recharts'

interface Props {
  data: Record<string, { tp: number; fp: number }>
}

const BAND_ORDER = ['low', 'medium', 'high', 'critical']

export function FPRateByBandChart({ data }: Props) {
  const chartData = BAND_ORDER
    .filter(band => data[band])
    .map(band => ({
      band,
      tp: data[band].tp,
      fp: data[band].fp,
    }))

  if (chartData.length === 0) {
    return <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>No analyst review data available.</p>
  }

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
        <XAxis dataKey="band" tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
        <YAxis tick={{ fontSize: 11 }} stroke="var(--text-muted)" />
        <Tooltip
          contentStyle={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', fontSize: '0.75rem' }}
        />
        <Legend wrapperStyle={{ fontSize: '0.75rem' }} />
        <Bar dataKey="tp" name="True Positives" fill="var(--accent)" />
        <Bar dataKey="fp" name="False Positives" fill="var(--score-critical)" />
      </BarChart>
    </ResponsiveContainer>
  )
}
