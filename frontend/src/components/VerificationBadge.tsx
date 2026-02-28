interface VerificationBadgeProps {
  verifiedBy?: string | null
  verifiedAt?: string | null
}

export function VerificationBadge({ verifiedBy, verifiedAt }: VerificationBadgeProps) {
  const isVerified = !!verifiedBy

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '2px 8px',
        borderRadius: 'var(--radius)',
        fontSize: 11,
        fontWeight: 700,
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        background: isVerified ? 'var(--score-low)' : 'var(--bg-base)',
        color: isVerified ? 'white' : 'var(--text-dim)',
        border: isVerified ? 'none' : '1px solid var(--border)',
      }}
      title={isVerified ? `Verified by ${verifiedBy}${verifiedAt ? ` on ${verifiedAt.slice(0, 10)}` : ''}` : 'Not verified'}
    >
      {isVerified ? 'Verified' : 'Unverified'}
    </span>
  )
}
