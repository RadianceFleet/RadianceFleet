import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { buildQueryParams } from '../utils/queryParams'

export interface ValidationResult {
  threshold_band: string
  n_evaluated: number
  confusion_matrix: { tp: number; fp: number; tn: number; fn: number }
  precision: number
  recall: number
  f2_score: number
  pr_auc: number
  per_source: Record<string, { tp: number; fp: number; tn: number; fn: number }>
  score_distribution: {
    positives: { n: number; mean?: number; p25?: number; p50?: number; p75?: number; max?: number }
    negatives: { n: number; mean?: number; p25?: number; p50?: number; p75?: number; max?: number }
  }
  error?: string
}

export interface SignalEffectiveness {
  signal: string
  tp_freq: number
  fp_freq: number
  lift: number | string
  spurious: boolean
}

export interface SweepPoint {
  threshold: number
  precision: number | null
  recall: number | null
  f2_score: number
}

export interface AnalystMetrics {
  total_reviewed: number
  confirmed_tp: number
  confirmed_fp: number
  fp_rate: number
  by_score_band: Record<string, { tp: number; fp: number }>
  by_corridor: Record<string, { tp: number; fp: number }>
}

export function useValidation(threshold?: string) {
  const params = buildQueryParams({ threshold_band: threshold })
  const qs = params.toString()
  return useQuery({
    queryKey: ['validation', threshold],
    queryFn: () => apiFetch<ValidationResult>(`/admin/validate${qs ? `?${qs}` : ''}`),
    retry: false,
  })
}

export function useValidationSignals() {
  return useQuery({
    queryKey: ['validation-signals'],
    queryFn: () => apiFetch<SignalEffectiveness[]>('/admin/validate/signals'),
    retry: false,
  })
}

export function useValidationSweep() {
  return useQuery({
    queryKey: ['validation-sweep'],
    queryFn: () => apiFetch<SweepPoint[]>('/admin/validate/sweep'),
    retry: false,
  })
}

export function useAnalystMetrics() {
  return useQuery({
    queryKey: ['analyst-metrics'],
    queryFn: () => apiFetch<AnalystMetrics>('/admin/validate/analyst-metrics'),
    retry: false,
  })
}

export interface LiveSignalEffectiveness {
  signal: string
  tp_count: number
  fp_count: number
  tp_freq: number
  fp_freq: number
  lift: number | string
}

export function useLiveSignalEffectiveness() {
  return useQuery({
    queryKey: ['live-signal-effectiveness'],
    queryFn: () => apiFetch<LiveSignalEffectiveness[]>('/accuracy/signal-effectiveness'),
    retry: false,
  })
}

export interface DetectorCorrelation {
  category_a: string
  category_b: string
  co_occurrence_count: number
  fp_count: number
  fp_rate: number
}

export function useDetectorCorrelation() {
  return useQuery({
    queryKey: ['detector-correlation'],
    queryFn: () => apiFetch<DetectorCorrelation[]>('/admin/validate/detector-correlation'),
    retry: false,
  })
}
