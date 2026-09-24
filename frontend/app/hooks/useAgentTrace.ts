import { getAgentTrace, getLatestTraceId, getTraceHistory } from '@/lib/api/agent'

export function useAgentTrace() {
  return { getAgentTrace, getLatestTraceId, getTraceHistory }
}
