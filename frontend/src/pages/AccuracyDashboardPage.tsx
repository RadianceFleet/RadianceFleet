import { useState } from 'react'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { ErrorMessage } from '../components/ui/ErrorMessage'
import { EmptyState } from '../components/ui/EmptyState'
import { PRCurveChart } from '../components/charts/PRCurveChart'
import { FPRateByBandChart } from '../components/charts/FPRateByBandChart'
import {
  useValidation,
  useValidationSignals,
  useValidationSweep,
  useAnalystMetrics,
  useDetectorCorrelation,
  useLiveSignalEffectiveness,
} from '../hooks/useValidation'
import type { SignalEffectiveness } from '../hooks/useValidation'

const cellStyle: React.CSSProperties = { padding: '0.5rem 0.75rem', fontSize: '0.8125rem' }
const headStyle: React.CSSProperties = {
  ...cellStyle,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textAlign: 'left' as const,
  borderBottom: '1px solid var(--border)',
  cursor: 'pointer',
  userSelect: 'none',
}
const metricBox: React.CSSProperties = {
  textAlign: 'center',
  padding: '0.75rem',
  minWidth: 100,
}
const metricValue: React.CSSProperties = { fontSize: '1.5rem', fontWeight: 700, color: 'var(--accent)' }
const metricLabel: React.CSSProperties = { fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }

const THRESHOLD_OPTIONS = ['low', 'medium', 'high', 'critical']

type SortKey = keyof SignalEffectiveness
type SortDir = 'asc' | 'desc'

export function AccuracyDashboardPage() {
  const [threshold, setThreshold] = useState('high')
  const [signalSort, setSignalSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'lift', dir: 'desc' })

  const { refetch: refetchValidation, ...validation } = useValidation(threshold)
  const { refetch: refetchSignals, ...signals } = useValidationSignals()
  const { refetch: refetchSweep, ...sweep } = useValidationSweep()
  const { refetch: refetchAnalyst, ...analyst } = useAnalystMetrics()
  const correlation = useDetectorCorrelation()
  const { refetch: refetchLiveSignals, ...liveSignals } = useLiveSignalEffectiveness()

  const handleSignalSort = (key: SortKey) => {
    setSignalSort(prev =>
      prev.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' }
    )
  }

  const sortedSignals = [...(signals.data ?? [])].sort((a, b) => {
    const aVal = a[signalSort.key]
    const bVal = b[signalSort.key]
    const aNum = typeof aVal === 'number' ? aVal : aVal === 'inf' ? 9999 : String(aVal)
    const bNum = typeof bVal === 'number' ? bVal : bVal === 'inf' ? 9999 : String(bVal)
    if (typeof aNum === 'number' && typeof bNum === 'number') {
      return signalSort.dir === 'asc' ? aNum - bNum : bNum - aNum
    }
    return signalSort.dir === 'asc'
      ? String(aNum).localeCompare(String(bNum))
      : String(bNum).localeCompare(String(aNum))
  })

  const correlationData = correlation.data

  return (
    <div style={{ maxWidth: 1100 }}>
      <h2 style={{ margin: '0 0 4px', fontSize: 18 }}>Detection Accuracy Dashboard</h2>
      <p style={{ color: 'var(--text-dim)', margin: '0 0 20px', fontSize: 13 }}>
        Validation metrics against ground truth labels (KSE, OFAC SDN).
      </p>

      {/* Validation Summary */}
      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 12 }}>
          <h3 style={{ margin: 0, fontSize: 14, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
            Validation Summary
          </h3>
          <select
            value={threshold}
            onChange={e => setThreshold(e.target.value)}
            style={{
              background: 'var(--bg-base)',
              color: 'var(--text-bright)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              padding: '4px 8px',
              fontSize: 12,
            }}
          >
            {THRESHOLD_OPTIONS.map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>

        {validation.isLoading && <Spinner text="Running validation..." />}
        {validation.error && <ErrorMessage error={validation.error} subject="validation data" onRetry={refetchValidation} />}
        {validation.data && !validation.data.error && (
          <>
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 16 }}>
              {[
                { label: 'F2 Score', value: validation.data.f2_score },
                { label: 'Precision', value: validation.data.precision },
                { label: 'Recall', value: validation.data.recall },
                { label: 'PR-AUC', value: validation.data.pr_auc },
                { label: 'Evaluated', value: validation.data.n_evaluated },
              ].map(m => (
                <div key={m.label} style={metricBox}>
                  <div style={metricValue}>{typeof m.value === 'number' && m.value < 10 ? m.value.toFixed(3) : m.value}</div>
                  <div style={metricLabel}>{m.label}</div>
                </div>
              ))}
            </div>

            <h4 style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 8px' }}>Confusion Matrix</h4>
            <table style={{ borderCollapse: 'collapse', marginBottom: 8 }}>
              <thead>
                <tr>
                  <th style={headStyle}></th>
                  <th style={headStyle}>Predicted +</th>
                  <th style={headStyle}>Predicted -</th>
                </tr>
              </thead>
              <tbody>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ ...cellStyle, fontWeight: 600 }}>Actual +</td>
                  <td style={{ ...cellStyle, color: 'var(--accent)', fontWeight: 700 }}>{validation.data.confusion_matrix.tp}</td>
                  <td style={{ ...cellStyle, color: 'var(--score-critical)' }}>{validation.data.confusion_matrix.fn}</td>
                </tr>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ ...cellStyle, fontWeight: 600 }}>Actual -</td>
                  <td style={{ ...cellStyle, color: 'var(--warning)' }}>{validation.data.confusion_matrix.fp}</td>
                  <td style={cellStyle}>{validation.data.confusion_matrix.tn}</td>
                </tr>
              </tbody>
            </table>
          </>
        )}
        {validation.data?.error && (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>No ground truth data available for validation.</p>
        )}
      </Card>

      {/* PR Curve */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
          Precision-Recall Curve
        </h3>
        {sweep.isLoading && <Spinner text="Loading sweep data..." />}
        {sweep.error && <ErrorMessage error={sweep.error} subject="sweep data" onRetry={refetchSweep} />}
        {!sweep.error && sweep.data && sweep.data.length === 0 && (
          <EmptyState title="No sweep data" description="Requires ground truth labels to generate sweep analysis." />
        )}
        {sweep.data && <PRCurveChart data={sweep.data} />}
      </Card>

      {/* Signal Effectiveness */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
          Signal Effectiveness
        </h3>
        {signals.isLoading && <Spinner text="Loading signals..." />}
        {signals.error && <ErrorMessage error={signals.error} subject="signals" onRetry={refetchSignals} />}
        {signals.data && signals.data.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle} onClick={() => handleSignalSort('signal')}>Signal</th>
                  <th style={headStyle} onClick={() => handleSignalSort('tp_freq')}>TP Freq</th>
                  <th style={headStyle} onClick={() => handleSignalSort('fp_freq')}>FP Freq</th>
                  <th style={headStyle} onClick={() => handleSignalSort('lift')}>Lift</th>
                </tr>
              </thead>
              <tbody>
                {sortedSignals.map(s => {
                  const liftNum = typeof s.lift === 'number' ? s.lift : Infinity
                  return (
                    <tr key={s.signal} style={{
                      borderBottom: '1px solid var(--border)',
                      background: liftNum < 1.0 ? 'rgba(239, 68, 68, 0.08)' : undefined,
                    }}>
                      <td style={cellStyle}>{s.signal}</td>
                      <td style={cellStyle}>{s.tp_freq.toFixed(3)}</td>
                      <td style={cellStyle}>{s.fp_freq.toFixed(3)}</td>
                      <td style={{
                        ...cellStyle,
                        color: liftNum < 1.0 ? 'var(--score-critical)' : 'var(--text-bright)',
                        fontWeight: liftNum < 1.0 ? 700 : 400,
                      }}>
                        {s.lift === 'inf' ? '\u221E' : typeof s.lift === 'number' ? s.lift.toFixed(2) : s.lift}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        {signals.data && signals.data.length === 0 && (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>No signal data (need both TP and FP predictions).</p>
        )}
      </Card>

      {/* Live Signal Effectiveness (from verdicts) */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
          Live Signal Effectiveness (from verdicts)
        </h3>
        {liveSignals.isLoading && <Spinner text="Loading live signals..." />}
        {liveSignals.error && <ErrorMessage error={liveSignals.error} subject="live signals" onRetry={refetchLiveSignals} />}
        {liveSignals.data && liveSignals.data.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>Signal</th>
                  <th style={headStyle}>TP Count</th>
                  <th style={headStyle}>FP Count</th>
                  <th style={headStyle}>TP Freq</th>
                  <th style={headStyle}>FP Freq</th>
                  <th style={headStyle}>Lift</th>
                </tr>
              </thead>
              <tbody>
                {liveSignals.data.map(s => {
                  const liftNum = typeof s.lift === 'number' ? s.lift : Infinity
                  return (
                    <tr key={s.signal} style={{
                      borderBottom: '1px solid var(--border)',
                      background: liftNum < 1.0 ? 'rgba(239, 68, 68, 0.08)' : undefined,
                    }}>
                      <td style={cellStyle}>{s.signal}</td>
                      <td style={cellStyle}>{s.tp_count}</td>
                      <td style={cellStyle}>{s.fp_count}</td>
                      <td style={cellStyle}>{s.tp_freq.toFixed(4)}</td>
                      <td style={cellStyle}>{s.fp_freq.toFixed(4)}</td>
                      <td style={{
                        ...cellStyle,
                        color: liftNum < 1.0 ? 'var(--score-critical)' : 'var(--text-bright)',
                        fontWeight: liftNum < 1.0 ? 700 : 400,
                      }}>
                        {s.lift === 'inf' ? '\u221E' : typeof s.lift === 'number' ? s.lift.toFixed(2) : s.lift}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        {liveSignals.data && liveSignals.data.length === 0 && (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>No verdict data available yet.</p>
        )}
      </Card>

      {/* Analyst FP Rate */}
      <Card style={{ marginBottom: 16 }}>
        <h3 style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
          Analyst FP Rate by Score Band
        </h3>
        {analyst.isLoading && <Spinner text="Loading analyst metrics..." />}
        {analyst.error && <ErrorMessage error={analyst.error} subject="analyst metrics" onRetry={refetchAnalyst} />}
        {analyst.data && (
          <>
            <div style={{ display: 'flex', gap: 16, marginBottom: 12, flexWrap: 'wrap' }}>
              <div style={metricBox}>
                <div style={metricValue}>{analyst.data.total_reviewed}</div>
                <div style={metricLabel}>Reviewed</div>
              </div>
              <div style={metricBox}>
                <div style={metricValue}>{analyst.data.confirmed_tp}</div>
                <div style={metricLabel}>True Positives</div>
              </div>
              <div style={metricBox}>
                <div style={{ ...metricValue, color: 'var(--score-critical)' }}>{analyst.data.confirmed_fp}</div>
                <div style={metricLabel}>False Positives</div>
              </div>
              <div style={metricBox}>
                <div style={{ ...metricValue, color: analyst.data.fp_rate > 0.3 ? 'var(--score-critical)' : 'var(--accent)' }}>
                  {(analyst.data.fp_rate * 100).toFixed(1)}%
                </div>
                <div style={metricLabel}>FP Rate</div>
              </div>
            </div>
            <FPRateByBandChart data={analyst.data.by_score_band} />
          </>
        )}
      </Card>

      {/* Detector Correlation — co-occurrence FP rates for signal pairs */}
      {correlationData && correlationData.length > 0 && (
        <Card style={{ marginBottom: 16 }}>
          <h3 style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
            Detector Correlation
          </h3>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-base)' }}>
                <th style={headStyle}>Signal A</th>
                <th style={headStyle}>Signal B</th>
                <th style={headStyle}>Co-occurrences</th>
                <th style={headStyle}>FP Count</th>
                <th style={headStyle}>FP Rate</th>
              </tr>
            </thead>
            <tbody>
              {correlationData.slice(0, 20).map((row) => (
                <tr key={`${row.category_a}-${row.category_b}`} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={cellStyle}>{row.category_a}</td>
                  <td style={cellStyle}>{row.category_b}</td>
                  <td style={cellStyle}>{row.co_occurrence_count}</td>
                  <td style={cellStyle}>{row.fp_count}</td>
                  <td style={{ ...cellStyle, color: row.fp_rate > 0.3 ? 'var(--danger)' : 'var(--text)' }}>
                    {(row.fp_rate * 100).toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {!correlation.error && correlationData && correlationData.length === 0 && (
        <EmptyState title="No correlation data" description="Requires analyst-reviewed alerts with multiple signals." />
      )}
    </div>
  )
}
