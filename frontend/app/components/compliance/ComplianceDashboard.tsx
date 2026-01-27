/**
 * Compliance Dashboard - Main view for Rule-Aware Agent compliance monitoring
 * Shows overview of all rule-aware accounts with comparison charts
 */
import { useState, useEffect } from 'react'
import { Shield, TrendingUp, CheckCircle, AlertCircle, Activity, BarChart3, Info } from 'lucide-react'
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
import {
  getComplianceStats,
  getRuleSummary,
  type ComplianceStats,
  type RuleSummary,
} from '@/lib/compliance-api'
import { type TradingAccount } from '@/lib/api'
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

  // Prepare chart data for compliance score comparison
  const chartData = {
    labels: accountsData.map(d => d.account.name),
    datasets: [
      {
        label: 'Rule Satisfaction (S_rule_sat)',
        data: accountsData.map(d => d.stats?.all_time?.avg_s_rule_sat || 0),
        borderColor: 'rgb(59, 130, 246)', // blue
        backgroundColor: 'rgba(59, 130, 246, 0.1)',
        tension: 0.4,
      },
      {
        label: 'Audit Score (S_audit)',
        data: accountsData.map(d => d.stats?.all_time?.avg_s_audit || 0),
        borderColor: 'rgb(168, 85, 247)', // purple
        backgroundColor: 'rgba(168, 85, 247, 0.1)',
        tension: 0.4,
      },
      {
        label: 'Final Score (Combined)',
        data: accountsData.map(d => d.stats?.all_time?.avg_final_score || 0),
        borderColor: 'rgb(34, 197, 94)', // green
        backgroundColor: 'rgba(34, 197, 94, 0.1)',
        tension: 0.4,
        borderWidth: 3,
      },
    ],
  }

  const chartOptions: ChartOptions<'line'> = {
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
        text: 'Agent Compliance Score Comparison',
        font: {
          size: 16,
          weight: 'bold',
        },
      },
      tooltip: {
        mode: 'index',
        intersect: false,
        callbacks: {
          label: function(context) {
            let label = context.dataset.label || '';
            if (label) {
              label += ': ';
            }
            if (context.parsed.y !== null) {
              label += context.parsed.y.toFixed(3);
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
          callback: function(value) {
            return (Number(value) * 100).toFixed(0) + '%';
          }
        },
        title: {
          display: true,
          text: 'Score',
        },
      },
      x: {
        title: {
          display: true,
          text: 'Agent',
        },
      },
    },
    interaction: {
      mode: 'nearest',
      axis: 'x',
      intersect: false
    },
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

      {/* Compliance Score Chart */}
      <div className="bg-card border rounded-lg p-6">
        <div className="h-80">
          <Line data={chartData} options={chartOptions} />
        </div>
      </div>

      {/* Accounts Comparison Table */}
      <div className="bg-card border rounded-lg p-6">
        <h3 className="text-lg font-semibold flex items-center gap-2 mb-4">
          <BarChart3 className="w-5 h-5" />
          Detailed Compliance Metrics
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b">
                <th className="text-left py-3 px-4 font-medium">Account</th>
                <th className="text-right py-3 px-4 font-medium">Evaluations</th>
                <th className="text-right py-3 px-4 font-medium">Pass Rate</th>
                <th className="text-right py-3 px-4 font-medium">Rule Sat</th>
                <th className="text-right py-3 px-4 font-medium">Audit Score</th>
                <th className="text-right py-3 px-4 font-medium">Final Score</th>
                <th className="text-center py-3 px-4 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {accountsData.map((data) => {
                const stats = data.stats
                const allTime = stats?.all_time
                const finalScore = allTime?.avg_final_score || 0
                
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
                        finalScore >= 0.9 ? 'text-green-600' : 
                        finalScore >= 0.7 ? 'text-yellow-600' : 
                        'text-red-600'
                      }`}>
                        {finalScore.toFixed(3)}
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
      </div>
    </div>
  )
}
