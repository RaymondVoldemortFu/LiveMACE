import { apiJson } from './client'
import type { AgentTrace, TraceSummary } from './generated-types'

export async function getLatestTraceId(accountId: number): Promise<{ trace_id: string | null }> {
  return apiJson(`/agent/latest/${accountId}`)
}

export async function getAgentTrace(traceId: string): Promise<AgentTrace> {
  return apiJson(`/agent/trace/${traceId}`)
}

export async function getTraceHistory(accountId: number): Promise<TraceSummary[]> {
  return apiJson(`/agent/history/${accountId}`)
}
