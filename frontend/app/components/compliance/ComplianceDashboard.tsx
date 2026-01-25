/**
 * Compliance Dashboard - Main view for Rule-Aware Agent compliance monitoring
 */
import React, { useState, useEffect } from 'react'
import { Shield, TrendingUp, CheckCircle, AlertCircle, Activity, BarChart3 } from 'lucide-react'
import {
  getComplianceStats,
  getComplianceTrend,
  getRecentDecisions,
  getRuleSummary,
  type ComplianceStats,
  type RecentDecisions,
  type RuleSummary,
  type ComplianceTrend,
} from '@/lib/compliance-api'
import ComplianceStatsCards from './ComplianceStatsCards'
import ComplianceTrendChart from './ComplianceTrendChart'
import RecentDecisionsTable from './RecentDecisionsTable'
import RuleSummaryCard from './RuleSummaryCard'

interface ComplianceDashboardProps {
  accountId: number
  accountName?: string
}

export default function ComplianceDashboard({ accountId, accountName }: ComplianceDashboardProps) {
  const [stats, setStats] = useState<ComplianceStats | null>(null)
  const [trendData, setTrendData] = useState<ComplianceTrend | null>(null)
  const [recentDecisions, setRecentDecisions] = useState<RecentDecisions | null>(null)
  const [ruleSummary, setRuleSummary] = useState<RuleSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Trend chart controls
  const [trendPeriod, setTrendPeriod] = useState<'day' | 'week' | 'month'>('day')
  const [trendMetric, setTrendMetric] = useState<'gate_pass_rate' | 'final_score' | 's_rule_sat' | 's_audit'>('final_score')

  useEffect(() => {
    loadData()
  }, [accountId])

  useEffect(() => {
    loadTrendData()
  }, [accountId, trendPeriod, trendMetric])

  const loadData = async () => {
    try {
      setLoading(true)
      setError(null)

      const [statsData, decisionsData, rulesData] = await Promise.all([
        getComplianceStats(accountId),
        getRecentDecisions(accountId, 10),
        getRuleSummary(),
      ])

      setStats(statsData)
      setRecentDecisions(decisionsData)
      setRuleSummary(rulesData)
    } catch (err) {
      console.error('Failed to load compliance data:', err)
      setError(err instanceof Error ? err.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }

  const loadTrendData = async () => {
    try {
      const trend = await getComplianceTrend(accountId, trendPeriod, trendMetric)
      setTrendData(trend)
    } catch (err) {
      console.error('Failed to load trend data:', err)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <Activity className="w-8 h-8 animate-spin mx-auto mb-2 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">Loading compliance data...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <AlertCircle className="w-12 h-12 mx-auto mb-4 text-destructive" />
          <p className="text-sm text-destructive mb-2">Failed to load compliance data</p>
          <p className="text-xs text-muted-foreground">{error}</p>
          <button
            onClick={loadData}
            className="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (!stats || stats.total_evaluations === 0) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <Shield className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
          <p className="text-sm text-muted-foreground mb-2">No compliance data available</p>
          <p className="text-xs text-muted-foreground">
            This account hasn't made any decisions with rule-aware agent yet
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold flex items-center gap-2">
            <Shield className="w-6 h-6" />
            Rule Compliance Dashboard
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            {accountName || `Account #${accountId}`} • {stats.total_evaluations} evaluations
          </p>
        </div>
        <button
          onClick={loadData}
          className="px-4 py-2 bg-secondary text-secondary-foreground rounded-md text-sm hover:bg-secondary/80 transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* Rule Summary */}
      {ruleSummary && <RuleSummaryCard summary={ruleSummary} />}

      {/* Stats Cards */}
      <ComplianceStatsCards stats={stats} />

      {/* Trend Chart */}
      <div className="bg-card border rounded-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <TrendingUp className="w-5 h-5" />
            Compliance Trend
          </h3>
          <div className="flex gap-2">
            {/* Period selector */}
            <select
              value={trendPeriod}
              onChange={(e) => setTrendPeriod(e.target.value as any)}
              className="px-3 py-1 bg-background border rounded-md text-sm"
            >
              <option value="day">Last 30 Days</option>
              <option value="week">Last 90 Days</option>
              <option value="month">Last Year</option>
            </select>
            {/* Metric selector */}
            <select
              value={trendMetric}
              onChange={(e) => setTrendMetric(e.target.value as any)}
              className="px-3 py-1 bg-background border rounded-md text-sm"
            >
              <option value="final_score">Final Score</option>
              <option value="gate_pass_rate">Gate Pass Rate</option>
              <option value="s_rule_sat">Rule Satisfaction</option>
              <option value="s_audit">LLM Audit Score</option>
            </select>
          </div>
        </div>
        {trendData && <ComplianceTrendChart data={trendData} />}
      </div>

      {/* Recent Decisions */}
      {recentDecisions && (
        <div className="bg-card border rounded-lg p-6">
          <h3 className="text-lg font-semibold flex items-center gap-2 mb-4">
            <BarChart3 className="w-5 h-5" />
            Recent Decisions
          </h3>
          <RecentDecisionsTable decisions={recentDecisions} />
        </div>
      )}
    </div>
  )
}
