import type { CSSProperties, ReactNode } from 'react'

export function Card({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius-md)',
      padding: '1rem',
      ...style,
    }}>
      {children}
    </div>
  )
}
