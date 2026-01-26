/**
 * Compliance Dashboard - Main view for Rule-Aware Agent compliance monitoring
 * Shows overview of all rule-aware accounts
 */
import { useState, useEffect } from 'react'
import { Shield, TrendingUp, CheckCircle, AlertCircle, Activity, BarChart3, Info } from 'lucide-react'
import {
  getComplianceStats,
  getRuleSummary,
  type ComplianceStats,
  type RuleSummary,
} from '@/lib/compliance-api'
import { type TradingAccount } from '@/lib/api'
import RuleSummaryCard from './RuleSummaryCard'

interface ComplianceDashboardProps {
  accounts: TradingAccount[]
}

interface AccountComplianceData {
  account: TradingAccount
  stats: ComplianceStats | null
  loading: boolean
  error: string | null
}

export default function ComplianceDashboard({ accounts }: ComplianceDashboardProps) {
  // Filter only rule-aware accounts
  const ruleAwareAccounts = accounts.filter(acc => acc.enable_rule_aware === true)
  
  const [accountsData, setAccountsData] = useState<AccountComplianceData[]>([])
  const [ruleSummary, setRuleSummary] = useState<RuleSummary | null>(null)
  const [loading, setLoading] = useState(true)

  // Load all rule-aware accounts data
  useEffect(() => {
    loadAllAccountsData()
    loadRuleSummary()
  }, [ruleAwareAccounts.length])

  const loadAllAccountsData = async () => {
    setLoading(true)
    
    const dataPromises = ruleAwareAccounts.map(async (account) => {
      try {
        const stats = await getComplianceStats(account.id)
        return {
          account,
          stats,
          loading: false,
          error: null
        }
      } catch (err) {
        console.error(`Failed to load stats for account ${account.id}:`, err)
        return {
          account,
          stats: null,
          loading: false,
          error: err instanceof Error ? err.message : 'Failed to load'
        }
      }
    })
    
    const results = await Promise.all(dataPromises)
    setAccountsData(results)
    setLoading(false)
  }

  const loadRuleSummary = async () => {
    try {
      const summary = await getRuleSummary()
      setRuleSummary(summary)
    } catch (err) {
      console.error('Failed to load rule summary:', err)
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

  // Show empty state if no rule-aware accounts
  if (ruleAwareAccounts.length === 0) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center max-w-md">
          <Info className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
          <p className="text-lg font-semibold mb-2">No Rule-Aware Agents</p>
          <p className="text-sm text-muted-foreground mb-4">
            There are no accounts with rule-aware trading enabled.
            Compliance monitoring is only available for rule-aware agents.
          </p>
          <p className="text-xs text-muted-foreground">
            To enable rule-aware trading, edit an account and toggle "Enable Rule-Aware Trading".
          </p>
        </div>
      </div>
    )
  }

  // Calculate aggregate statistics
  const aggregateStats = {
    totalAccounts: accountsData.length,
    totalEvaluations: accountsData.reduce((sum, d) => sum + (d.stats?.total_evaluations || 0), 0),
    avgPassRate: accountsData.length > 0 
      ? accountsData.reduce((sum, d) => sum + (d.stats?.all_time?.gate_pass_rate || 0), 0) / accountsData.length 
      : 0,
    avgFinalScore: accountsData.length > 0
      ? accountsData.reduce((sum, d) => sum + (d.stats?.all_time?.avg_final_score || 0), 0) / accountsData.length
      : 0,
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold flex items-center gap-2">
            <Shield className="w-6 h-6" />
            Rule Compliance Overview
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            {ruleAwareAccounts.length} rule-aware agent{ruleAwareAccounts.length > 1 ? 's' : ''} • {aggregateStats.totalEvaluations} total evaluations
          </p>
        </div>
        <button
          onClick={loadAllAccountsData}
          className="px-4 py-2 bg-secondary text-secondary-foreground rounded-md text-sm hover:bg-secondary/80 transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* Rule Summary */}
      {ruleSummary && <RuleSummaryCard summary={ruleSummary} />}

      {/* Aggregate Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-card border rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-sm text-muted-foreground">Total Agents</p>
            <Shield className="w-4 h-4 text-muted-foreground" />
          </div>
          <p className="text-2xl font-bold">{aggregateStats.totalAccounts}</p>
        </div>
        
        <div className="bg-card border rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-sm text-muted-foreground">Total Evaluations</p>
            <BarChart3 className="w-4 h-4 text-muted-foreground" />
          </div>
          <p className="text-2xl font-bold">{aggregateStats.totalEvaluations}</p>
        </div>
        
        <div className="bg-card border rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-sm text-muted-foreground">Avg Pass Rate</p>
            <CheckCircle className="w-4 h-4 text-green-500" />
          </div>
          <p className="text-2xl font-bold">{(aggregateStats.avgPassRate * 100).toFixed(1)}%</p>
        </div>
        
        <div className="bg-card border rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-sm text-muted-foreground">Avg Final Score</p>
            <TrendingUp className="w-4 h-4 text-blue-500" />
          </div>
          <p className="text-2xl font-bold">{aggregateStats.avgFinalScore.toFixed(3)}</p>
        </div>
      </div>

      {/* Accounts Comparison Table */}
      <div className="bg-card border rounded-lg p-6">
        <h3 className="text-lg font-semibold flex items-center gap-2 mb-4">
          <BarChart3 className="w-5 h-5" />
          Accounts Compliance Comparison
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b">
                <th className="text-left py-3 px-4 font-medium">Account</th>
                <th className="text-right py-3 px-4 font-medium">Evaluations</th>
                <th className="text-right py-3 px-4 font-medium">Pass Rate</th>
                <th className="text-right py-3 px-4 font-medium">Final Score</th>
                <th className="text-right py-3 px-4 font-medium">Rule Sat</th>
                <th className="text-right py-3 px-4 font-medium">Audit Score</th>
                <th className="text-center py-3 px-4 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {accountsData.map((data) => {
                const stats = data.stats
                const allTime = stats?.all_time
                
                return (
                  <tr key={data.account.id} className="border-b hover:bg-muted/50">
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-2">
                        <Shield className="w-4 h-4 text-primary" />
                        <span className="font-medium">{data.account.name}</span>
                      </div>
                    </td>
                    <td className="py-3 px-4 text-right">
                      {stats?.total_evaluations || 0}
                    </td>
                    <td className="py-3 px-4 text-right">
                      {allTime?.gate_pass_rate !== undefined && allTime.gate_pass_rate !== null
                        ? <span className={allTime.gate_pass_rate >= 0.9 ? 'text-green-600' : allTime.gate_pass_rate >= 0.7 ? 'text-yellow-600' : 'text-red-600'}>
                            {(allTime.gate_pass_rate * 100).toFixed(1)}%
                          </span>
                        : '-'}
                    </td>
                    <td className="py-3 px-4 text-right font-medium">
                      {allTime?.avg_final_score?.toFixed(3) || '-'}
                    </td>
                    <td className="py-3 px-4 text-right">
                      {allTime?.avg_s_rule_sat?.toFixed(3) || '-'}
                    </td>
                    <td className="py-3 px-4 text-right">
                      {allTime?.avg_s_audit?.toFixed(3) || '-'}
                    </td>
                    <td className="py-3 px-4 text-center">
                      {data.loading ? (
                        <Activity className="w-4 h-4 animate-spin inline" />
                      ) : data.error ? (
                        <AlertCircle className="w-4 h-4 text-destructive inline" />
                      ) : stats?.total_evaluations === 0 ? (
                        <span className="text-xs text-muted-foreground">No data</span>
                      ) : (
                        <CheckCircle className="w-4 h-4 text-green-500 inline" />
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
