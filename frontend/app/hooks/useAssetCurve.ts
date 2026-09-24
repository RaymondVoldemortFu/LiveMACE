import { useEffect, useState } from 'react'
import { sendClientMessage, type AssetCurvePoint } from '@/lib/ws/messages'
import { portfolioSocket } from '@/lib/ws/client'

export function useAssetCurve<T extends AssetCurvePoint>(
  wsRef: React.MutableRefObject<WebSocket | null> | undefined,
  timeframe: string,
  initialData?: T[],
) {
  const [data, setData] = useState<T[]>(initialData || [])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isInitialized, setIsInitialized] = useState(false)

  useEffect(() => {
    if (!wsRef?.current) return
    return portfolioSocket.subscribe({ message: (message) => {
        if ((message.type === 'asset_curve_data' || message.type === 'asset_curve_update') && message.timeframe === timeframe) {
          setData((message.data || []) as T[])
          if (message.type === 'asset_curve_data') {
            setLoading(false)
            setError(null)
          }
          setIsInitialized(true)
        } else if (message.type === 'asset_curve_error' && message.timeframe === timeframe) {
          setData([])
          setLoading(false)
          setError(message.message || 'Failed to load asset curve')
          setIsInitialized(true)
        }
    } })
  }, [wsRef, timeframe])

  useEffect(() => {
    if (wsRef?.current && wsRef.current.readyState === WebSocket.OPEN) {
      setData([])
      setLoading(true)
      setError(null)
      sendClientMessage(wsRef.current, { type: 'get_asset_curve', timeframe })
    } else if (initialData && timeframe === '1h' && !isInitialized) {
      setData(initialData)
      setIsInitialized(true)
    }
  }, [timeframe, wsRef])

  useEffect(() => {
    if (initialData && !isInitialized && timeframe === '1h') {
      setData(initialData)
      setIsInitialized(true)
    }
  }, [])

  return { data, loading, error, isInitialized }
}
