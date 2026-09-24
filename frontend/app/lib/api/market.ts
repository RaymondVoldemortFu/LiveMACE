import { apiJson } from './client'
import type { MarketBar } from './generated-types'

export async function getMarketKline(symbol: string, market: string, signal?: AbortSignal): Promise<{ data: MarketBar[] }> {
  return apiJson(`/market/kline/${symbol}?market=${market}&period=1d&count=30`, { signal })
}

export async function getMarketStatus(symbol: string, market: string, signal?: AbortSignal): Promise<{ market_status: string }> {
  return apiJson(`/market/status/${symbol}?market=${market}`, { signal })
}
