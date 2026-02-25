import { useRef, useState, useCallback } from 'react'
import { useImportAIS, useIngestionStatus, type ImportResult } from '../hooks/useIngestion'
import { Spinner } from '../components/ui/Spinner'
import { Card } from '../components/ui/Card'

const dropZone: React.CSSProperties = {
  border: '2px dashed var(--border)',
  borderRadius: 'var(--radius-md)',
  padding: '2rem',
  textAlign: 'center',
  cursor: 'pointer',
  transition: 'border-color 0.15s',
}

const dropZoneActive: React.CSSProperties = {
  ...dropZone,
  borderColor: 'var(--accent)',
}

export function IngestionPage() {
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const importAIS = useImportAIS()
  const { data: status } = useIngestionStatus()

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const dropped = e.dataTransfer.files[0]
    if (dropped) setFile(dropped)
  }, [])

  const handleUpload = () => {
    if (!file) return
    importAIS.mutate(file)
  }

  return (
    <div style={{ maxWidth: 700 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        AIS Data Ingestion
      </h2>

      <Card style={{ marginBottom: '1rem' }}>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Upload AIS CSV</h3>
        <div
          style={dragging ? dropZoneActive : dropZone}
          onDragOver={e => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => fileRef.current?.click()}
        >
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            style={{ display: 'none' }}
            onChange={e => {
              const f = e.target.files?.[0]
              if (f) setFile(f)
            }}
          />
          {file ? (
            <p style={{ margin: 0, color: 'var(--text-body)' }}>
              {file.name} ({(file.size / 1024).toFixed(1)} KB)
            </p>
          ) : (
            <p style={{ margin: 0, color: 'var(--text-dim)' }}>
              Drop a CSV file here or click to browse
            </p>
          )}
        </div>

        <div style={{ marginTop: '1rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <button
            onClick={handleUpload}
            disabled={!file || importAIS.isPending}
            style={{
              padding: '0.5rem 1rem',
              background: file ? 'var(--accent-primary)' : 'var(--border)',
              color: 'white',
              border: 'none',
              borderRadius: 'var(--radius)',
              cursor: file ? 'pointer' : 'not-allowed',
              fontSize: '0.875rem',
            }}
          >
            Upload
          </button>
          {importAIS.isPending && <Spinner text="Importing..." />}
        </div>

        {importAIS.isError && (
          <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem', marginTop: '0.5rem' }}>
            {importAIS.error.message}
          </p>
        )}

        {importAIS.isSuccess && (
          <ImportSummary result={importAIS.data} />
        )}
      </Card>

      <Card>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Ingestion Status</h3>
        {status ? (
          <table style={{ fontSize: '0.8125rem' }}>
            <tbody>
              <tr>
                <td style={{ color: 'var(--text-dim)', paddingRight: '1rem' }}>State</td>
                <td style={{ color: 'var(--text-body)' }}>{status.state}</td>
              </tr>
              {status.accepted != null && (
                <tr>
                  <td style={{ color: 'var(--text-dim)', paddingRight: '1rem' }}>Accepted</td>
                  <td style={{ color: 'var(--score-low)' }}>{status.accepted}</td>
                </tr>
              )}
              {status.rejected != null && (
                <tr>
                  <td style={{ color: 'var(--text-dim)', paddingRight: '1rem' }}>Rejected</td>
                  <td style={{ color: status.rejected > 0 ? 'var(--score-high)' : 'var(--text-body)' }}>
                    {status.rejected}
                  </td>
                </tr>
              )}
              {status.started_at && (
                <tr>
                  <td style={{ color: 'var(--text-dim)', paddingRight: '1rem' }}>Started</td>
                  <td style={{ color: 'var(--text-body)' }}>{status.started_at}</td>
                </tr>
              )}
              {status.finished_at && (
                <tr>
                  <td style={{ color: 'var(--text-dim)', paddingRight: '1rem' }}>Finished</td>
                  <td style={{ color: 'var(--text-body)' }}>{status.finished_at}</td>
                </tr>
              )}
            </tbody>
          </table>
        ) : (
          <p style={{ color: 'var(--text-dim)', fontSize: '0.875rem', margin: 0 }}>
            No ingestion data available
          </p>
        )}
      </Card>
    </div>
  )
}

function ImportSummary({ result }: { result: ImportResult }) {
  return (
    <div style={{
      marginTop: '0.75rem',
      padding: '0.75rem',
      background: 'var(--bg-base)',
      borderRadius: 'var(--radius)',
      fontSize: '0.8125rem',
    }}>
      <p style={{ margin: '0 0 0.25rem', color: 'var(--score-low)' }}>
        Accepted: {result.accepted}
      </p>
      <p style={{ margin: '0 0 0.25rem', color: result.rejected > 0 ? 'var(--score-high)' : 'var(--text-body)' }}>
        Rejected: {result.rejected}
      </p>
      {result.errors.length > 0 && (
        <details style={{ marginTop: '0.5rem' }}>
          <summary style={{ cursor: 'pointer', color: 'var(--score-critical)', fontSize: '0.75rem' }}>
            {result.errors.length} error(s)
          </summary>
          <ul style={{ margin: '0.25rem 0 0', paddingLeft: '1rem', color: 'var(--text-muted)', fontSize: '0.75rem' }}>
            {result.errors.map((err, i) => <li key={i}>{err}</li>)}
          </ul>
        </details>
      )}
    </div>
  )
}
