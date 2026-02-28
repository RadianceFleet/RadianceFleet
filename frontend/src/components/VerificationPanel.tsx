import { useState } from 'react'
import { useVerificationBudget, useVerifyVessel, useUpdateOwner } from '../hooks/useVerification'
import { Card } from './ui/Card'

const sectionHead: React.CSSProperties = {
  margin: '0 0 12px',
  fontSize: 14,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: 1,
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  background: 'var(--bg-base)',
  color: 'var(--text-bright)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius)',
  padding: '6px 10px',
  fontSize: 13,
  boxSizing: 'border-box' as const,
}

const btnStyle: React.CSSProperties = {
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius)',
  padding: '6px 14px',
  cursor: 'pointer',
  fontSize: 13,
}

interface VerificationPanelProps {
  vesselId: string
  currentOwner?: string | null
}

export function VerificationPanel({ vesselId, currentOwner }: VerificationPanelProps) {
  const [provider, setProvider] = useState('skylight')
  const [ownerName, setOwnerName] = useState(currentOwner ?? '')
  const [verifiedBy, setVerifiedBy] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [verificationNotes, setVerificationNotes] = useState('')
  const [verifyResult, setVerifyResult] = useState<string | null>(null)
  const [ownerSaved, setOwnerSaved] = useState(false)

  const { data: budget, isLoading: budgetLoading } = useVerificationBudget()
  const verifyMutation = useVerifyVessel(vesselId)
  const ownerMutation = useUpdateOwner(vesselId)

  const handleVerify = () => {
    setVerifyResult(null)
    verifyMutation.mutate(provider, {
      onSuccess: (data) => {
        setVerifyResult(data.verified ? `Verified via ${data.provider} ($${data.cost_usd})` : `Not verified via ${data.provider}`)
      },
      onError: (err: unknown) => {
        setVerifyResult(err instanceof Error ? err.message : 'Verification failed')
      },
    })
  }

  const handleOwnerUpdate = () => {
    setOwnerSaved(false)
    ownerMutation.mutate(
      { owner_name: ownerName || undefined, verified_by: verifiedBy || undefined, source_url: sourceUrl || undefined, verification_notes: verificationNotes || undefined },
      {
        onSuccess: () => setOwnerSaved(true),
      }
    )
  }

  return (
    <Card style={{ marginBottom: 16 }}>
      <h3 style={sectionHead}>Ownership Verification</h3>

      {/* Budget display */}
      {budgetLoading ? (
        <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>Loading budget...</p>
      ) : budget ? (
        <div style={{ marginBottom: 16, padding: '8px 12px', background: 'var(--bg-base)', borderRadius: 'var(--radius)', fontSize: 13 }}>
          <span style={{ color: 'var(--text-muted)' }}>Budget: </span>
          <span style={{ color: budget.remaining_usd > 0 ? 'var(--score-low)' : 'var(--score-critical)', fontWeight: 600 }}>
            ${budget.remaining_usd.toFixed(2)} remaining
          </span>
          <span style={{ color: 'var(--text-dim)' }}> of ${budget.budget_usd.toFixed(2)} ({budget.calls_this_month} calls this month)</span>
        </div>
      ) : null}

      {/* Provider verification */}
      <div style={{ marginBottom: 16 }}>
        <label style={{ fontSize: 12, color: 'var(--text-dim)', display: 'block', marginBottom: 4 }}>Verify via provider</label>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            value={provider}
            onChange={e => setProvider(e.target.value)}
            style={{ ...inputStyle, width: 'auto' }}
          >
            <option value="skylight">Skylight</option>
            <option value="spire">Spire</option>
            <option value="seaweb">SeaWeb</option>
          </select>
          <button
            onClick={handleVerify}
            disabled={verifyMutation.isPending || (budget != null && budget.remaining_usd <= 0)}
            style={{
              ...btnStyle,
              background: 'var(--accent-primary)',
              color: '#fff',
              border: 'none',
              opacity: verifyMutation.isPending ? 0.6 : 1,
            }}
          >
            {verifyMutation.isPending ? 'Verifying...' : 'Verify'}
          </button>
        </div>
        {verifyResult && (
          <p style={{ fontSize: 12, color: verifyMutation.isError ? 'var(--score-critical)' : 'var(--score-low)', marginTop: 6 }}>
            {verifyResult}
          </p>
        )}
      </div>

      {/* Owner update form */}
      <div>
        <label style={{ fontSize: 12, color: 'var(--text-dim)', display: 'block', marginBottom: 4 }}>Update ownership</label>
        <div style={{ display: 'grid', gap: 8, marginBottom: 8 }}>
          <input placeholder="Owner name" value={ownerName} onChange={e => setOwnerName(e.target.value)} style={inputStyle} />
          <input placeholder="Verified by" value={verifiedBy} onChange={e => setVerifiedBy(e.target.value)} style={inputStyle} />
          <input placeholder="Source URL" value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} style={inputStyle} />
          <textarea
            placeholder="Verification notes"
            value={verificationNotes}
            onChange={e => setVerificationNotes(e.target.value)}
            rows={2}
            style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }}
          />
        </div>
        <button
          onClick={handleOwnerUpdate}
          disabled={ownerMutation.isPending}
          style={{
            ...btnStyle,
            background: 'var(--bg-base)',
            color: 'var(--accent)',
            opacity: ownerMutation.isPending ? 0.6 : 1,
          }}
        >
          {ownerMutation.isPending ? 'Saving...' : ownerSaved ? 'Saved' : 'Update Owner'}
        </button>
        {ownerMutation.isError && (
          <p style={{ fontSize: 12, color: 'var(--score-critical)', marginTop: 6 }}>
            {ownerMutation.error instanceof Error ? ownerMutation.error.message : 'Update failed'}
          </p>
        )}
      </div>
    </Card>
  )
}
