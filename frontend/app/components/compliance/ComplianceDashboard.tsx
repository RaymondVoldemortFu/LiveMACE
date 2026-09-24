/**
 * Compliance Dashboard - Main view for Rule-Aware Agent compliance monitoring
 * Shows overview of all rule-aware accounts with comparison charts
 */
import { useState, useEffect } from 'react'
import { Shield, CheckCircle, AlertCircle, Activity, BarChart3, Info } from 'lucide-react'
import { Line } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  ChartOptions,
} from 'chart.js'
import { getComplianceHistory, getComplianceStats, getRuleSummary } from '@/lib/api/compliance'
import type { ComplianceHistory, ComplianceStats, RuleSummary, TradingAccount } from '@/lib/api/generated-types'
import RuleSummaryCard from './RuleSummaryCard'

// Register Chart.js components
ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
)

interface ComplianceDashboardProps {
  accounts: TradingAccount[]
}

interface AccountComplianceData {
  account: TradingAccount
  stats: ComplianceStats | null
  history: ComplianceHistory | null
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
        const [stats, history] = await Promise.all([
          getComplianceStats(account.id),
          getComplianceHistory(account.id, 50)
        ])
        return {
          account,
          stats,
          history,
          loading: false,
          error: null
        }
      } catch (err) {
        console.error(`Failed to load stats for account ${account.id}:`, err)
        return {
          account,
          stats: null,
          history: null,
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

  // Generate colors for each agent
  const agentColors = [
    { border: 'rgb(59, 130, 246)', bg: 'rgba(59, 130, 246, 0.1)' },   // blue
    { border: 'rgb(168, 85, 247)', bg: 'rgba(168, 85, 247, 0.1)' },   // purple
    { border: 'rgb(34, 197, 94)', bg: 'rgba(34, 197, 94, 0.1)' },     // green
    { border: 'rgb(234, 179, 8)', bg: 'rgba(234, 179, 8, 0.1)' },     // yellow
    { border: 'rgb(239, 68, 68)', bg: 'rgba(239, 68, 68, 0.1)' },     // red
    { border: 'rgb(20, 184, 166)', bg: 'rgba(20, 184, 166, 0.1)' },   // teal
  ]

  // Helper function to normalize timestamp to nearest 5-minute interval
  const normalizeToFiveMinutes = (timestamp: string): string => {
    const date = new Date(timestamp)
    const minutes = date.getMinutes()
    const normalizedMinutes = Math.round(minutes / 5) * 5
    date.setMinutes(normalizedMinutes, 0, 0) // Set to normalized minutes, clear seconds and milliseconds
    return date.toISOString()
  }

  // Prepare time series data for each metric
  const prepareTimeSeriesData = (metric: 's_rule_sat' | 's_audit' | 'final_score') => {
    // Collect and normalize timestamps, grouping records by agent and normalized timestamp
    const normalizedDataByAgent = new Map<number, Map<string, number[]>>()
    
    accountsData.forEach(data => {
      const agentId = data.account.id
      const normalizedRecords = new Map<string, number[]>()
      
      if (data.history?.records) {
        data.history.records.forEach(record => {
          if (!record.timestamp) return
          const normalizedTime = normalizeToFiveMinutes(record.timestamp)
          const value = metric === 's_rule_sat' ? record.s_rule_sat :
                       metric === 's_audit' ? record.s_audit :
                       record.final_score
          
          if (value !== null && value !== undefined) {
            if (!normalizedRecords.has(normalizedTime)) {
              normalizedRecords.set(normalizedTime, [])
            }
            normalizedRecords.get(normalizedTime)!.push(value)
          }
        })
      }
      
      normalizedDataByAgent.set(agentId, normalizedRecords)
    })
    
    // Collect all unique normalized timestamps across all agents
    const allNormalizedTimestamps = new Set<string>()
    normalizedDataByAgent.forEach(records => {
      records.forEach((_, timestamp) => allNormalizedTimestamps.add(timestamp))
    })
    
    const sortedTimestamps = Array.from(allNormalizedTimestamps).sort()
    
    // Create datasets for each agent
    const datasets = accountsData.map((data, index) => {
      const color = agentColors[index % agentColors.length]
      const agentRecords = normalizedDataByAgent.get(data.account.id)
      
      // For each timestamp, take the average if multiple values exist, or null if no data
      const dataPoints = sortedTimestamps.map(ts => {
        const values = agentRecords?.get(ts)
        if (!values || values.length === 0) return null
        // Take the average of all values in the same 5-minute window
        const avg = values.reduce((sum, v) => sum + v, 0) / values.length
        return avg
      })
      
      return {
        label: data.account.name,
        data: dataPoints,
        borderColor: color.border,
        backgroundColor: color.bg,
        tension: 0.4,
        spanGaps: true,
        pointRadius: 3,
        pointHoverRadius: 5,
      }
    })
    
    return {
      labels: sortedTimestamps.map(ts => {
        const date = new Date(ts)
        return date.toLocaleString('en-US', { 
          month: 'short', 
          day: 'numeric', 
          hour: '2-digit', 
          minute: '2-digit' 
        })
      }),
      datasets
    }
  }

  const createChartOptions = (title: string, yAxisLabel: string): ChartOptions<'line'> => ({
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'top' as const,
        labels: {
          usePointStyle: true,
          padding: 15,
        },
      },
      title: {
        display: true,
        text: title,
        font: {
          size: 16,
          weight: 'bold',
        },
      },
      tooltip: {
        mode: 'index',
        intersect: false,
        callbacks: {
          label: function(context: any) {
            let label = context.dataset.label || '';
            if (label) {
              label += ': ';
            }
            if (context.parsed.y !== null) {
              label += context.parsed.y.toFixed(3);
            } else {
              label += 'N/A';
            }
            return label;
          }
        }
      },
    },
    scales: {
      y: {
        beginAtZero: true,
        max: 1.0,
        ticks: {
          callback: function(value: any) {
            return (Number(value) * 100).toFixed(0) + '%';
          }
        },
        title: {
          display: true,
          text: yAxisLabel,
        },
      },
      x: {
        title: {
          display: true,
          text: 'Time',
        },
        ticks: {
          maxRotation: 45,
          minRotation: 45,
        },
      },
    },
    interaction: {
      mode: 'nearest',
      axis: 'x',
      intersect: false
    },
  })

  const ruleSatData = prepareTimeSeriesData('s_rule_sat')
  const auditScoreData = prepareTimeSeriesData('s_audit')
  const finalScoreData = prepareTimeSeriesData('final_score')

  const ruleSatOptions = createChartOptions('Rule Satisfaction Score Over Time', 'Rule Satisfaction')
  const auditScoreOptions = createChartOptions('LLM Audit Score Over Time', 'Audit Score')
  const finalScoreOptions = createChartOptions('Final Combined Score Over Time', 'Final Score')

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
            {ruleAwareAccounts.length} rule-aware agent{ruleAwareAccounts.length > 1 ? 's' : ''} monitored
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

      {/* Three Compliance Score Charts */}
      <div className="grid grid-cols-1 gap-6">
        {/* Rule Satisfaction Chart */}
        <div className="bg-card border rounded-lg p-6">
          <div className="h-80">
            <Line data={ruleSatData} options={ruleSatOptions} />
          </div>
        </div>

        {/* Audit Score Chart */}
        <div className="bg-card border rounded-lg p-6">
          <div className="h-80">
            <Line data={auditScoreData} options={auditScoreOptions} />
          </div>
        </div>

        {/* Final Score Chart */}
        <div className="bg-card border rounded-lg p-6">
          <div className="h-80">
            <Line data={finalScoreData} options={finalScoreOptions} />
          </div>
        </div>
      </div>

      {/* Accounts Comparison Table */}
      <div className="bg-card border rounded-lg p-6">
        <h3 className="text-lg font-semibold flex items-center gap-2 mb-4">
          <BarChart3 className="w-5 h-5" />
          Agent Compliance Metrics Summary
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b">
                <th className="text-left py-3 px-4 font-medium">Agent</th>
                <th className="text-right py-3 px-4 font-medium">Total<br/>Evaluations</th>
                <th className="text-right py-3 px-4 font-medium">Pass<br/>Rate</th>
                <th className="text-right py-3 px-4 font-medium">Avg Rule<br/>Sat</th>
                <th className="text-right py-3 px-4 font-medium">Avg Audit<br/>Score</th>
                <th className="text-right py-3 px-4 font-medium">Avg Final<br/>Score</th>
                <th className="text-right py-3 px-4 font-medium">Latest Rule<br/>Sat</th>
                <th className="text-right py-3 px-4 font-medium">Latest Audit<br/>Score</th>
                <th className="text-right py-3 px-4 font-medium">Latest Final<br/>Score</th>
                <th className="text-center py-3 px-4 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {accountsData.map((data) => {
                const stats = data.stats
                const allTime = stats?.all_time
                const avgFinalScore = allTime?.avg_final_score || 0
                
                // Get latest scores from history
                const latestRecord = data.history?.records?.[0]
                const latestRuleSat = latestRecord?.s_rule_sat
                const latestAudit = latestRecord?.s_audit
                const latestFinal = latestRecord?.final_score
                
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
                    <td className="py-3 px-4 text-right">
                      <span className="text-blue-600">
                        {allTime?.avg_s_rule_sat?.toFixed(3) || '-'}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <span className="text-purple-600">
                        {allTime?.avg_s_audit?.toFixed(3) || '-'}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <span className={`font-semibold ${
                        avgFinalScore >= 0.9 ? 'text-green-600' : 
                        avgFinalScore >= 0.7 ? 'text-yellow-600' : 
                        'text-red-600'
                      }`}>
                        {avgFinalScore.toFixed(3)}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <span className="text-blue-600 text-sm">
                        {latestRuleSat !== null && latestRuleSat !== undefined ? latestRuleSat.toFixed(3) : '-'}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <span className="text-purple-600 text-sm">
                        {latestAudit !== null && latestAudit !== undefined ? latestAudit.toFixed(3) : '-'}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <span className={`text-sm font-semibold ${
                        latestFinal !== null && latestFinal !== undefined 
                          ? (latestFinal >= 0.9 ? 'text-green-600' : 
                             latestFinal >= 0.7 ? 'text-yellow-600' : 
                             'text-red-600')
                          : ''
                      }`}>
                        {latestFinal !== null && latestFinal !== undefined ? latestFinal.toFixed(3) : '-'}
                      </span>
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
        <div className="mt-4 text-xs text-muted-foreground">
          <p>• <span className="text-blue-600 font-semibold">Rule Sat</span>: Rule satisfaction score (compliance with R0/R1/R2 rules)</p>
          <p>• <span className="text-purple-600 font-semibold">Audit Score</span>: LLM audit score (reasoning quality and rule awareness)</p>
          <p>• <span className="text-green-600 font-semibold">Final Score</span>: Combined score (50% Rule Sat + 50% Audit Score)</p>
        </div>
      </div>
    </div>
  )
}
