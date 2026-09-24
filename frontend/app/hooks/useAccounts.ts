import { useEffect, useState } from 'react'
import { getAccounts } from '@/lib/api/accounts'
import type { TradingAccount } from '@/lib/api/generated-types'

export function useAccounts(refreshToken = 0) {
  const [accounts, setAccounts] = useState<TradingAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getAccounts()
      .then((list) => {
        if (!cancelled) {
          setAccounts(list)
          setError(null)
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'Failed to load accounts')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [refreshToken])

  return { accounts, loading, error }
}
