import { apiJson } from './client'
import type {
  ComplianceHistory,
  ComplianceStats,
  ComplianceTrend,
  RecentDecisions,
  RuleItem,
  RuleSummary,
} from './generated-types'

export async function getRuleSummary(): Promise<RuleSummary> {
  return apiJson('/rules/summary')
}

export async function getRuleList(): Promise<{ total: number; rules: RuleItem[] }> {
  return apiJson('/rules/list')
}

export async function getComplianceHistory(
  accountId: number,
  limit: number = 50,
  offset: number = 0,
): Promise<ComplianceHistory> {
  return apiJson(`/compliance/account/${accountId}/history?limit=${limit}&offset=${offset}`)
}

export async function getComplianceTrend(
  accountId: number,
  period: 'day' | 'week' | 'month' = 'day',
  metric: 'gate_pass_rate' | 'final_score' | 's_rule_sat' | 's_audit' = 'final_score',
): Promise<ComplianceTrend> {
  return apiJson(`/compliance/account/${accountId}/trend?period=${period}&metric=${metric}`)
}

export async function getComplianceStats(accountId: number): Promise<ComplianceStats> {
  return apiJson(`/compliance/account/${accountId}/stats`)
}

export async function getRecentDecisions(accountId: number, limit: number = 20): Promise<RecentDecisions> {
  return apiJson(`/compliance/recent-decisions?account_id=${accountId}&limit=${limit}`)
}
