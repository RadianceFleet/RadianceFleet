/** Widget theme configuration for embeddable components. */

export type ThemeMode = 'light' | 'dark'
export type SizePreset = 'compact' | 'normal' | 'large'

export interface WidgetTheme {
  mode: ThemeMode
  bg: string
  text: string
  textSecondary: string
  border: string
  accent: string
  width: string
  fontSize: string
  padding: string
  tierColors: Record<string, string>
}

const LIGHT_BASE: Omit<WidgetTheme, 'accent' | 'width' | 'fontSize' | 'padding' | 'mode'> = {
  bg: '#ffffff',
  text: '#1a1a2e',
  textSecondary: '#6b7280',
  border: '#e5e7eb',
  tierColors: {
    critical: '#ef4444',
    high: '#f97316',
    medium: '#eab308',
    low: '#22c55e',
    minimal: '#6b7280',
    unknown: '#9ca3af',
  },
}

const DARK_BASE: Omit<WidgetTheme, 'accent' | 'width' | 'fontSize' | 'padding' | 'mode'> = {
  bg: '#1a1a2e',
  text: '#e5e7eb',
  textSecondary: '#9ca3af',
  border: '#374151',
  tierColors: {
    critical: '#f87171',
    high: '#fb923c',
    medium: '#facc15',
    low: '#4ade80',
    minimal: '#9ca3af',
    unknown: '#6b7280',
  },
}

const SIZE_PRESETS: Record<SizePreset, { width: string; fontSize: string; padding: string }> = {
  compact: { width: '280px', fontSize: '12px', padding: '8px' },
  normal: { width: '400px', fontSize: '14px', padding: '16px' },
  large: { width: '600px', fontSize: '16px', padding: '20px' },
}

export interface ThemeParams {
  theme?: string
  accent?: string
  size?: string
}

export function getTheme(params: ThemeParams): WidgetTheme {
  const mode: ThemeMode = params.theme === 'dark' ? 'dark' : 'light'
  const base = mode === 'dark' ? DARK_BASE : LIGHT_BASE
  const sizeKey = (params.size || 'normal') as SizePreset
  const sizeConfig = SIZE_PRESETS[sizeKey] || SIZE_PRESETS.normal
  const accent = params.accent || '#60a5fa'

  return {
    mode,
    ...base,
    accent,
    ...sizeConfig,
  }
}
