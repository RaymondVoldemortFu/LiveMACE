import type { MemoryList, MemoryMetrics, MemoryTimeline } from "./generated-types"
import { apiJson } from './client'

export async function getMemories(accountId: number, market?: string): Promise<MemoryList> {
  const params = market ? `?market=${market}` : ''
  return apiJson(`/memory/${accountId}/list${params}`)
}

export async function getMemoryMetrics(accountId: number, market?: string): Promise<MemoryMetrics> {
  const params = market ? `?market=${market}` : ''
  return apiJson(`/memory/${accountId}/metrics${params}`)
}

export async function getMemoryGrowthTimeline(accountId: number, market?: string): Promise<MemoryTimeline> {
  const params = market ? `?market=${market}` : ''
  return apiJson(`/memory/${accountId}/growth-timeline${params}`)
}
