/**
 * Recent Decisions Table - Display recent trading decisions with compliance scores
 */
import React from 'react'
import { CheckCircle, XCircle, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { type RecentDecisions } from '@/lib/compliance-api'

interface RecentDecisionsTableProps {
  decisions: RecentDecisions
}

export default function RecentDecisionsTable({ decisions }: RecentDecisionsTableProps) {
  if (decisions.decisions.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        <p className="text-sm">No recent decisions</p>
      </div>
    )
  }

  const formatTimestamp = (timestamp: string) => {
    const date = new Date(timestamp)
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const getOperationIcon = (operation: string) => {
    if (operation === 'open') return <TrendingUp className="w-4 h-4 text-green-600" />
    if (operation === 'close') return <TrendingDown className="w-4 h-4 text-red-600" />
    return <Minus className="w-4 h-4 text-muted-foreground" />
  }

  const getScoreColor = (score: number | null) => {
    if (score === null) return 'text-muted-foreground'
    if (score >= 0.8) return 'text-green-600'
    if (score >= 0.6) return 'text-yellow-600'
    return 'text-red-600'
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b">
            <th className="text-left py-2 px-3 font-medium text-muted-foreground">Time</th>
            <th className="text-left py-2 px-3 font-medium text-muted-foreground">Operation</th>
            <th className="text-left py-2 px-3 font-medium text-muted-foreground">Symbol</th>
            <th className="text-center py-2 px-3 font-medium text-muted-foreground">Leverage</th>
            <th className="text-center py-2 px-3 font-medium text-muted-foreground">Gate Pass</th>
            <th className="text-center py-2 px-3 font-medium text-muted-foreground">Final Score</th>
            <th className="text-center py-2 px-3 font-medium text-muted-foreground">LLM Audit</th>
            <th className="text-center py-2 px-3 font-medium text-muted-foreground">Executed</th>
          </tr>
        </thead>
        <tbody>
          {decisions.decisions.map((decision) => (
            <tr key={decision.trace_id} className="border-b hover:bg-muted/50 transition-colors">
              <td className="py-2 px-3 text-xs text-muted-foreground">
                {formatTimestamp(decision.timestamp)}
              </td>
              <td className="py-2 px-3">
                <div className="flex items-center gap-2">
                  {getOperationIcon(decision.operation)}
                  <span className="capitalize">{decision.operation}</span>
                </div>
              </td>
              <td className="py-2 px-3 font-medium">{decision.symbol}</td>
              <td className="py-2 px-3 text-center">{decision.leverage}x</td>
              <td className="py-2 px-3 text-center">
                {decision.compliance && decision.compliance.gate_pass !== null ? (
                  decision.compliance.gate_pass ? (
                    <CheckCircle className="w-4 h-4 text-green-600 mx-auto" />
                  ) : (
                    <XCircle className="w-4 h-4 text-red-600 mx-auto" />
                  )
                ) : (
                  <span className="text-xs text-muted-foreground">N/A</span>
                )}
              </td>
              <td className="py-2 px-3 text-center">
                {decision.compliance && decision.compliance.final_score !== null ? (
                  <span className={getScoreColor(decision.compliance.final_score)}>
                    {decision.compliance.final_score.toFixed(3)}
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground">N/A</span>
                )}
              </td>
              <td className="py-2 px-3 text-center">
                {decision.compliance && decision.compliance.s_audit !== null ? (
                  <span className={getScoreColor(decision.compliance.s_audit)}>
                    {decision.compliance.s_audit.toFixed(3)}
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground">N/A</span>
                )}
              </td>
              <td className="py-2 px-3 text-center">
                {decision.executed ? (
                  <CheckCircle className="w-4 h-4 text-green-600 mx-auto" />
                ) : (
                  <XCircle className="w-4 h-4 text-muted-foreground mx-auto" />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
