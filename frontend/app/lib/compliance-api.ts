/**
 * Compliance API Client - API functions for rule compliance data
 */

const API_BASE_URL = '/api'

/**
 * Rule Configuration APIs
 */
export interface RuleSummary {
  total_rules: number
  r0_count: number
  r1_count: number
  r2_count: number
  categories: {
    r0: { name: string; description: string; count: number }
    r1: { name: string; description: string; count: number }
    r2: { name: string; description: string; count: number }
  }
}

export interface RuleItem {
  id: string
  name: string
  category: string
  category_name: string
  weight?: number
}

export async function getRuleSummary(): Promise<RuleSummary> {
  const response = await fetch(`${API_BASE_URL}/rules/summary`)
  if (!response.ok) throw new Error('Failed to fetch rule summary')
  return response.json()
}

export async function getRuleList(): Promise<{ total: number; rules: RuleItem[] }> {
  const response = await fetch(`${API_BASE_URL}/rules/list`)
  if (!response.ok) throw new Error('Failed to fetch rule list')
  return response.json()
}

/**
 * Compliance Statistics APIs
 */
export interface ComplianceRecord {
  id: number
  timestamp: string
  trace_id: string
  gate_pass: boolean
  s_rule_sat: number | null
  s_audit: number | null
  final_score: number | null
}

export interface ComplianceHistory {
  total: number
  limit: number
  offset: number
  records: ComplianceRecord[]
}

export async function getComplianceHistory(
  accountId: number,
  limit: number = 50,
  offset: number = 0
): Promise<ComplianceHistory> {
  const url = `${API_BASE_URL}/compliance/account/${accountId}/history?limit=${limit}&offset=${offset}`
  const response = await fetch(url)
  if (!response.ok) throw new Error('Failed to fetch compliance history')
  return response.json()
}

export interface TrendDataPoint {
  date: string
  value: number
  count: number
}

export interface ComplianceTrend {
  period: string
  metric: string
  data_points: TrendDataPoint[]
}

export async function getComplianceTrend(
  accountId: number,
  period: 'day' | 'week' | 'month' = 'day',
  metric: 'gate_pass_rate' | 'final_score' | 's_rule_sat' | 's_audit' = 'final_score'
): Promise<ComplianceTrend> {
  const url = `${API_BASE_URL}/compliance/account/${accountId}/trend?period=${period}&metric=${metric}`
  const response = await fetch(url)
  if (!response.ok) throw new Error('Failed to fetch compliance trend')
  return response.json()
}

export interface ComplianceStats {
  total_evaluations: number
  all_time: {
    gate_pass_rate: number
    avg_final_score: number | null
    avg_s_rule_sat: number | null
    avg_s_audit: number | null
    evaluation_count: number
  } | null
  recent_7d: {
    gate_pass_rate: number
    avg_final_score: number | null
    avg_s_rule_sat: number | null
    avg_s_audit: number | null
    evaluation_count: number
  } | null
  llm_audit_stats: {
    count: number
    avg_score: number | null
    avg_coverage: number | null
    avg_conflict: number | null
  } | null
}

export async function getComplianceStats(accountId: number): Promise<ComplianceStats> {
  const url = `${API_BASE_URL}/compliance/account/${accountId}/stats`
  const response = await fetch(url)
  if (!response.ok) throw new Error('Failed to fetch compliance stats')
  return response.json()
}

export interface RecentDecision {
  trace_id: string
  timestamp: string
  operation: string
  symbol: string
  leverage: number
  executed: boolean
  compliance: {
    gate_pass: boolean | null
    final_score: number | null
    s_audit: number | null
  } | null
}

export interface RecentDecisions {
  account_id: number
  count: number
  decisions: RecentDecision[]
}

export async function getRecentDecisions(
  accountId: number,
  limit: number = 20
): Promise<RecentDecisions> {
  const url = `${API_BASE_URL}/compliance/recent-decisions?account_id=${accountId}&limit=${limit}`
  const response = await fetch(url)
  if (!response.ok) throw new Error('Failed to fetch recent decisions')
  return response.json()
}
