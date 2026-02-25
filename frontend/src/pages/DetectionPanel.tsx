import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { Spinner } from '../components/ui/Spinner'
import { Card } from '../components/ui/Card'

interface DetectionResult {
  [key: string]: unknown
}

function useDetection(endpoint: string) {
  return useMutation({
    mutationFn: (body?: Record<string, string>) =>
      apiFetch<DetectionResult>(endpoint, {
        method: 'POST',
        body: body ? JSON.stringify(body) : undefined,
      }),
  })
}

const btnBase: React.CSSProperties = {
  padding: '0.5rem 1rem',
  border: 'none',
  borderRadius: 'var(--radius)',
  cursor: 'pointer',
  fontSize: '0.8125rem',
  color: 'white',
}

function DetectionButton({
  label,
  mutation,
  body,
}: {
  label: string
  mutation: ReturnType<typeof useDetection>
  body?: Record<string, string>
}) {
  return (
    <div style={{ marginBottom: '0.75rem' }}>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button
          onClick={() => mutation.mutate(body)}
          disabled={mutation.isPending}
          style={{ ...btnBase, background: 'var(--accent-primary)' }}
        >
          {label}
        </button>
        {mutation.isPending && <Spinner text="Running..." />}
      </div>
      {mutation.isError && (
        <p style={{ color: 'var(--score-critical)', fontSize: '0.75rem', margin: '0.25rem 0 0' }}>
          {mutation.error.message}
        </p>
      )}
      {mutation.isSuccess && (
        <pre style={{
          fontSize: '0.75rem',
          color: 'var(--text-muted)',
          background: 'var(--bg-base)',
          padding: '0.5rem',
          borderRadius: 'var(--radius)',
          margin: '0.5rem 0 0',
          overflow: 'auto',
          maxHeight: 200,
        }}>
          {JSON.stringify(mutation.data, null, 2)}
        </pre>
      )}
    </div>
  )
}

export function DetectionPanel() {
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')

  const gapDetect = useDetection('/gaps/detect')
  const spoofDetect = useDetection('/spoofing/detect')
  const loiterDetect = useDetection('/loitering/detect')
  const stsDetect = useDetection('/sts/detect')
  const scoreAlerts = useDetection('/score-alerts')
  const rescoreAll = useDetection('/rescore-all-alerts')

  const dateBody: Record<string, string> = {}
  if (dateFrom) dateBody.date_from = dateFrom
  if (dateTo) dateBody.date_to = dateTo

  const [pipelineRunning, setPipelineRunning] = useState(false)
  const [pipelineStep, setPipelineStep] = useState('')
  const [pipelineError, setPipelineError] = useState('')
  const [pipelineDone, setPipelineDone] = useState(false)

  const runFullPipeline = async () => {
    setPipelineRunning(true)
    setPipelineError('')
    setPipelineDone(false)

    const steps = [
      { name: 'Gap detection', fn: () => gapDetect.mutateAsync(Object.keys(dateBody).length ? dateBody : undefined) },
      { name: 'Spoofing detection', fn: () => spoofDetect.mutateAsync(Object.keys(dateBody).length ? dateBody : undefined) },
      { name: 'Loitering detection', fn: () => loiterDetect.mutateAsync(undefined) },
      { name: 'STS detection', fn: () => stsDetect.mutateAsync(undefined) },
      { name: 'Scoring alerts', fn: () => scoreAlerts.mutateAsync(undefined) },
    ]

    for (const step of steps) {
      setPipelineStep(step.name)
      try {
        await step.fn()
      } catch (err) {
        setPipelineError(`${step.name} failed: ${err instanceof Error ? err.message : String(err)}`)
        setPipelineRunning(false)
        return
      }
    }

    setPipelineRunning(false)
    setPipelineDone(true)
    setPipelineStep('')
  }

  return (
    <div style={{ maxWidth: 700 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        Detection Panel
      </h2>

      <Card style={{ marginBottom: '1rem' }}>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Date Range (optional)</h3>
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: '0.8125rem', color: 'var(--text-dim)' }}>
            From:
            <input
              type="date"
              value={dateFrom}
              onChange={e => setDateFrom(e.target.value)}
              style={{
                marginLeft: '0.5rem',
                padding: '0.375rem 0.5rem',
                background: 'var(--bg-base)',
                color: 'var(--text-body)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                fontSize: '0.8125rem',
              }}
            />
          </label>
          <label style={{ fontSize: '0.8125rem', color: 'var(--text-dim)' }}>
            To:
            <input
              type="date"
              value={dateTo}
              onChange={e => setDateTo(e.target.value)}
              style={{
                marginLeft: '0.5rem',
                padding: '0.375rem 0.5rem',
                background: 'var(--bg-base)',
                color: 'var(--text-body)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                fontSize: '0.8125rem',
              }}
            />
          </label>
        </div>
      </Card>

      <Card style={{ marginBottom: '1rem' }}>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Individual Detections</h3>
        <DetectionButton label="Detect Gaps" mutation={gapDetect} body={Object.keys(dateBody).length ? dateBody : undefined} />
        <DetectionButton label="Detect Spoofing" mutation={spoofDetect} body={Object.keys(dateBody).length ? dateBody : undefined} />
        <DetectionButton label="Detect Loitering" mutation={loiterDetect} />
        <DetectionButton label="Detect STS" mutation={stsDetect} />
        <DetectionButton label="Score Alerts" mutation={scoreAlerts} />
        <DetectionButton label="Rescore All Alerts" mutation={rescoreAll} />
      </Card>

      <Card>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Full Pipeline</h3>
        <p style={{ fontSize: '0.8125rem', color: 'var(--text-dim)', margin: '0 0 0.75rem' }}>
          Runs: Gaps &rarr; Spoofing &rarr; Loitering &rarr; STS &rarr; Scoring (sequential)
        </p>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <button
            onClick={runFullPipeline}
            disabled={pipelineRunning}
            style={{
              ...btnBase,
              background: pipelineRunning ? 'var(--border)' : 'var(--score-high)',
              cursor: pipelineRunning ? 'not-allowed' : 'pointer',
            }}
          >
            Run Full Pipeline
          </button>
          {pipelineRunning && <Spinner text={pipelineStep + '...'} />}
        </div>
        {pipelineError && (
          <p style={{ color: 'var(--score-critical)', fontSize: '0.8125rem', marginTop: '0.5rem' }}>
            {pipelineError}
          </p>
        )}
        {pipelineDone && (
          <p style={{ color: 'var(--score-low)', fontSize: '0.8125rem', marginTop: '0.5rem' }}>
            Pipeline complete
          </p>
        )}
      </Card>
    </div>
  )
}
