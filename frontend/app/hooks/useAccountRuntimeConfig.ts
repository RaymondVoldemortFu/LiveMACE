import { useEffect, useState } from 'react'
import { ApiError } from '@/lib/api/client'
import { getRuntimeConfig, saveRuntimeConfig, validateRuntimeConfig } from '@/lib/api/extensions'
import type { RuntimeConfig, RuntimeConfigBody, ValidationIssue } from '@/lib/api/generated-types'

function issuesFrom(body: unknown): ValidationIssue[] {
  if (!body || typeof body !== 'object') return []
  const details = (body as { error?: { details?: { errors?: ValidationIssue[] } } }).error?.details
  return Array.isArray(details?.errors) ? details.errors : []
}

export function useAccountRuntimeConfig(accountId: number | null) {
  const [saved, setSaved] = useState<RuntimeConfigBody | null>(null)
  const [draft, setDraft] = useState<RuntimeConfigBody | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<ValidationIssue[]>([])

  const [conflict, setConflict] = useState<RuntimeConfig | null>(null)

  useEffect(() => {
    setSaved(null)
    setDraft(null)
    setConflict(null)
    if (accountId === null) return
    let cancelled = false
    setLoading(true)
    getRuntimeConfig(accountId)
      .then((config) => {
        if (cancelled) return
        setSaved(config.config)
        setDraft(config.config)
        setUpdatedAt(config.updated_at)
        setFieldErrors(config.validation_errors || [])
        setError(null)
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'Failed to load runtime config')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [accountId])

  const dirty = JSON.stringify(saved) !== JSON.stringify(draft)

  const save = async () => {
    if (conflict) throw new Error('Resolve the configuration conflict before saving')
    if (accountId === null || !draft) throw new Error('Runtime config is not loaded')
    const validation = await validateRuntimeConfig(accountId, draft)
    if (!validation.valid || !validation.config) {
      setFieldErrors(validation.errors)
      throw new Error(validation.errors[0]?.message || 'Invalid runtime configuration')
    }
    try {
      const stored = await saveRuntimeConfig(accountId, validation.config, updatedAt)
      setSaved(stored.config)
      setDraft(stored.config)
      setUpdatedAt(stored.updated_at)
      setFieldErrors([])
      setError(null)
    } catch (cause) {
      if (cause instanceof ApiError) {
        const issues = issuesFrom(cause.body)
        if (issues.length) setFieldErrors(issues)
        if (cause.status === 409) {
          const latest = await getRuntimeConfig(accountId)
          setConflict(latest)
        }
      }
      throw cause
    }
  }

  const resolveConflict = (keepDraft: boolean) => {
    if (!conflict) return
    setSaved(conflict.config)
    if (!keepDraft) setDraft(conflict.config)
    setUpdatedAt(conflict.updated_at)
    setConflict(null)
    setFieldErrors([])
    setError(null)
  }

  return { conflict, resolveConflict, saved, draft, setDraft, updatedAt, loading, error, fieldErrors, dirty, save }
}
