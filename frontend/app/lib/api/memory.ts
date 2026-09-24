import { apiJson } from './client'

export async function getMemories(accountId: number, market?: string): Promise<any> {
  const params = market ? `?market=${market}` : ''
  return apiJson(`/memory/${accountId}/list${params}`)
}

export async function getMemoryMetrics(accountId: number, market?: string): Promise<any> {
  const params = market ? `?market=${market}` : ''
  return apiJson(`/memory/${accountId}/metrics${params}`)
}

export async function getMemoryGrowthTimeline(accountId: number, market?: string): Promise<any> {
  const params = market ? `?market=${market}` : ''
  return apiJson(`/memory/${accountId}/growth-timeline${params}`)
}
