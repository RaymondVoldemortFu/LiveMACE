/**
 * Compliance Stats Cards - Display key compliance metrics
 */
import React from 'react'
import { Shield, CheckCircle, TrendingUp, Brain, AlertTriangle } from 'lucide-react'
import { type ComplianceStats } from '@/lib/compliance-api'

interface ComplianceStatsCardsProps {
  stats: ComplianceStats
}

export default function ComplianceStatsCards({ stats }: ComplianceStatsCardsProps) {
  const allTime = stats.all_time
  const recent = stats.recent_7d
  const llmAudit = stats.llm_audit_stats

  // Calculate trend indicators
  const getTrendIndicator = (current: number | null, previous: number | null) => {
    if (current === null || previous === null) return null
    const diff = current - previous
    if (Math.abs(diff) < 0.01) return null
    return diff > 0 ? 'up' : 'down'
  }

  const passRateTrend = getTrendIndicator(
    recent?.gate_pass_rate ?? null,
    allTime?.gate_pass_rate ?? null
  )

  const scoreTrend = getTrendIndicator(
    recent?.avg_final_score ?? null,
    allTime?.avg_final_score ?? null
  )

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      {/* Gate Pass Rate */}
      <div className="bg-card border rounded-lg p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Shield className="w-4 h-4 text-primary" />
            <span className="text-sm font-medium text-muted-foreground">Gate Pass Rate</span>
          </div>
          {passRateTrend && (
            <span className={`text-xs ${passRateTrend === 'up' ? 'text-green-600' : 'text-red-600'}`}>
              {passRateTrend === 'up' ? '↑' : '↓'}
            </span>
          )}
        </div>
        <div className="space-y-1">
          <p className="text-2xl font-bold">
            {allTime ? `${(allTime.gate_pass_rate * 100).toFixed(1)}%` : 'N/A'}
          </p>
          {recent && (
            <p className="text-xs text-muted-foreground">
              7d: {(recent.gate_pass_rate * 100).toFixed(1)}%
            </p>
          )}
        </div>
      </div>

      {/* Final Score */}
      <div className="bg-card border rounded-lg p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-green-600" />
            <span className="text-sm font-medium text-muted-foreground">Avg Final Score</span>
          </div>
          {scoreTrend && (
            <span className={`text-xs ${scoreTrend === 'up' ? 'text-green-600' : 'text-red-600'}`}>
              {scoreTrend === 'up' ? '↑' : '↓'}
            </span>
          )}
        </div>
        <div className="space-y-1">
          <p className="text-2xl font-bold">
            {allTime?.avg_final_score !== null && allTime?.avg_final_score !== undefined ? allTime.avg_final_score.toFixed(3) : 'N/A'}
          </p>
          {recent && recent?.avg_final_score !== null && (
            <p className="text-xs text-muted-foreground">
              7d: {recent.avg_final_score.toFixed(3)}
            </p>
          )}
        </div>
      </div>

      {/* Rule Satisfaction */}
      <div className="bg-card border rounded-lg p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-blue-600" />
            <span className="text-sm font-medium text-muted-foreground">Rule Satisfaction</span>
          </div>
        </div>
        <div className="space-y-1">
          <p className="text-2xl font-bold">
            {allTime?.avg_s_rule_sat !== null && allTime?.avg_s_rule_sat !== undefined ? allTime.avg_s_rule_sat.toFixed(3) : 'N/A'}
          </p>
          {recent && recent?.avg_s_rule_sat !== null && (
            <p className="text-xs text-muted-foreground">
              7d: {recent.avg_s_rule_sat.toFixed(3)}
            </p>
          )}
        </div>
      </div>

      {/* LLM Audit Score */}
      <div className="bg-card border rounded-lg p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-purple-600" />
            <span className="text-sm font-medium text-muted-foreground">LLM Audit Score</span>
          </div>
        </div>
        <div className="space-y-1">
          {llmAudit ? (
            <>
              <p className="text-2xl font-bold">
                {llmAudit.avg_score !== null ? llmAudit.avg_score.toFixed(3) : 'N/A'}
              </p>
              <div className="flex gap-3 text-xs text-muted-foreground">
                <span>Coverage: {llmAudit.avg_coverage?.toFixed(1) ?? 'N/A'}/5</span>
                <span>Conflict: {llmAudit.avg_conflict?.toFixed(1) ?? 'N/A'}/5</span>
              </div>
              <p className="text-xs text-muted-foreground">
                {llmAudit.count} audits
              </p>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">No LLM audit data</p>
          )}
        </div>
      </div>

      {/* Total Evaluations Info */}
      <div className="md:col-span-2 lg:col-span-4 bg-muted/50 border rounded-lg p-4">
        <div className="flex items-center gap-4">
          <AlertTriangle className="w-5 h-5 text-yellow-600" />
          <div>
            <p className="text-sm font-medium">
              Total Evaluations: {stats.total_evaluations}
            </p>
            <p className="text-xs text-muted-foreground">
              All-time: {allTime?.evaluation_count ?? 0} evaluations
              {recent && ` • Last 7 days: ${recent.evaluation_count} evaluations`}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
