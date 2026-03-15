/**
 * Rule Summary Card - Display overview of trading rules
 */
import React from 'react'
import { Shield, AlertTriangle, Info } from 'lucide-react'
import { type RuleSummary } from '@/lib/compliance-api'

interface RuleSummaryCardProps {
  summary: RuleSummary
}

export default function RuleSummaryCard({ summary }: RuleSummaryCardProps) {
  return (
    <div className="bg-card border rounded-lg p-6">
      <div className="flex items-center gap-2 mb-4">
        <Shield className="w-5 h-5 text-primary" />
        <h3 className="text-lg font-semibold">Trading Rules Overview</h3>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* R0 - System Hard Constraints */}
        <div className="bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-800 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className="w-4 h-4 text-red-600" />
            <span className="text-sm font-semibold text-red-900 dark:text-red-100">R0 - System Hard</span>
          </div>
          <p className="text-2xl font-bold text-red-600 mb-1">{summary.r0_count}</p>
          <p className="text-xs text-red-700 dark:text-red-300">
            {summary.categories.r0.description}
          </p>
        </div>

        {/* R1 - Client Hard Rules */}
        <div className="bg-orange-50 dark:bg-orange-950/20 border border-orange-200 dark:border-orange-800 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Shield className="w-4 h-4 text-orange-600" />
            <span className="text-sm font-semibold text-orange-900 dark:text-orange-100">R1 - Client Hard</span>
          </div>
          <p className="text-2xl font-bold text-orange-600 mb-1">{summary.r1_count}</p>
          <p className="text-xs text-orange-700 dark:text-orange-300">
            {summary.categories.r1.description}
          </p>
        </div>

        {/* R2 - Client Soft Preferences */}
        <div className="bg-blue-50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Info className="w-4 h-4 text-blue-600" />
            <span className="text-sm font-semibold text-blue-900 dark:text-blue-100">R2 - Client Soft</span>
          </div>
          <p className="text-2xl font-bold text-blue-600 mb-1">{summary.r2_count}</p>
          <p className="text-xs text-blue-700 dark:text-blue-300">
            {summary.categories.r2.description}
          </p>
        </div>
      </div>

      <div className="mt-4 pt-4 border-t">
        <p className="text-sm text-muted-foreground">
          <span className="font-semibold">Total: {summary.total_rules} rules</span>
          {' • '}
          <span className="text-xs">
            Priority: R0 (Highest) &gt; R1 &gt; R2 (Lowest)
          </span>
        </p>
      </div>
    </div>
  )
}
