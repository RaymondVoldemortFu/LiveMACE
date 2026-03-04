import { useEffect, useState } from 'react'
import { Line, Bar, Doughnut } from 'react-chartjs-2'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { getMemories, getMemoryMetrics, getMemoryGrowthTimeline } from '@/lib/api'

interface MemoryViewProps {
  account: any
  accounts: any[]
}

export function MemoryView({ account, accounts }: MemoryViewProps) {
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(account?.id || null)
  const [memories, setMemories] = useState<any[]>([])
  const [metrics, setMetrics] = useState<any>(null)
  const [timeline, setTimeline] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Update selected account when prop changes
  useEffect(() => {
    if (account?.id && !selectedAccountId) {
      setSelectedAccountId(account.id)
    }
  }, [account?.id, selectedAccountId])

  useEffect(() => {
    if (!selectedAccountId) {
      setLoading(false)
      return
    }

    setLoading(true)
    setError(null)

    Promise.all([
      getMemories(selectedAccountId),
      getMemoryMetrics(selectedAccountId),
      getMemoryGrowthTimeline(selectedAccountId)
    ])
      .then(([memData, metricData, timelineData]) => {
        setMemories(memData.memories || [])
        setMetrics(metricData)
        setTimeline(timelineData.timeline || [])
        setLoading(false)
      })
      .catch((err) => {
        setError(err.message)
        setLoading(false)
      })
  }, [selectedAccountId])

  const selectedAccount = accounts.find(a => a.id === selectedAccountId)

  if (!selectedAccountId) {
    return (
      <div className="flex items-center justify-center h-96">
        <p className="text-muted-foreground">Please select an account</p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <p className="text-muted-foreground">Loading memory data...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-96">
        <p className="text-destructive">Error: {error}</p>
      </div>
    )
  }

  if (memories.length === 0) {
    return (
      <div className="space-y-6 p-6">
        <div className="flex items-center justify-between">
          <h1 className="text-3xl font-bold">Memory System</h1>
          <Select value={selectedAccountId.toString()} onValueChange={(v) => setSelectedAccountId(Number(v))}>
            <SelectTrigger className="w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {accounts.map((acc) => (
                <SelectItem key={acc.id} value={acc.id.toString()}>
                  {acc.name} ({acc.account_type})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center justify-center h-96">
          <p className="text-muted-foreground">
            No memories yet. Agent will create memories during trading.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold">Memory System - {selectedAccount?.name}</h1>
        <Select value={selectedAccountId.toString()} onValueChange={(v) => setSelectedAccountId(Number(v))}>
          <SelectTrigger className="w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {accounts.map((acc) => (
              <SelectItem key={acc.id} value={acc.id.toString()}>
                {acc.name} ({acc.account_type})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Memory List */}
      <Card>
        <CardHeader>
          <CardTitle>All Memories ({memories.length})</CardTitle>
          <CardDescription>Recent memories created by the agent</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="max-h-96 overflow-y-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Content</TableHead>
                  <TableHead className="w-32">Retrieval Count</TableHead>
                  <TableHead className="w-48">Created At</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {memories.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell className="font-mono text-sm">{m.content}</TableCell>
                    <TableCell>{m.retrieval_count}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {new Date(m.created_at).toLocaleString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Metrics Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Retrieval Distribution */}
        {metrics?.retrieval_distribution && (
          <Card>
            <CardHeader>
              <CardTitle>Retrieval Distribution</CardTitle>
              <CardDescription>
                Zombie Rate: {(metrics.retrieval_distribution.zombie_rate * 100).toFixed(1)}% |
                High-Value Rate: {(metrics.retrieval_distribution.high_value_rate * 100).toFixed(1)}%
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Bar
                data={{
                  labels: Object.keys(metrics.retrieval_distribution.histogram),
                  datasets: [{
                    label: 'Memory Count',
                    data: Object.values(metrics.retrieval_distribution.histogram),
                    backgroundColor: 'rgba(59, 130, 246, 0.5)',
                    borderColor: 'rgb(59, 130, 246)',
                    borderWidth: 1
                  }]
                }}
                options={{
                  responsive: true,
                  plugins: {
                    legend: { display: false }
                  },
                  scales: {
                    y: { beginAtZero: true }
                  }
                }}
              />
            </CardContent>
          </Card>
        )}

        {/* Diversity Score */}
        {metrics?.memory_diversity && (
          <Card>
            <CardHeader>
              <CardTitle>Memory Diversity</CardTitle>
              <CardDescription>
                {metrics.memory_diversity.interpretation || 'Semantic diversity analysis'}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-center h-64">
                <Doughnut
                  data={{
                    labels: ['Diversity', 'Similarity'],
                    datasets: [{
                      data: [
                        metrics.memory_diversity.diversity_score || 0,
                        metrics.memory_diversity.avg_similarity || 0
                      ],
                      backgroundColor: ['rgba(34, 197, 94, 0.5)', 'rgba(239, 68, 68, 0.5)'],
                      borderColor: ['rgb(34, 197, 94)', 'rgb(239, 68, 68)'],
                      borderWidth: 1
                    }]
                  }}
                  options={{
                    responsive: true,
                    maintainAspectRatio: false
                  }}
                />
              </div>
              <div className="mt-4 text-center">
                <p className="text-sm text-muted-foreground">
                  Diversity Score: {(metrics.memory_diversity.diversity_score || 0).toFixed(3)}
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Retrieval Relevance */}
        {metrics?.retrieval_relevance && (
          <Card>
            <CardHeader>
              <CardTitle>Retrieval Relevance</CardTitle>
              <CardDescription>Memory usage statistics</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="flex justify-between">
                  <span className="text-sm font-medium">Total Searches:</span>
                  <span className="text-sm">{metrics.retrieval_relevance.search_count}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm font-medium">Ever Retrieved:</span>
                  <span className="text-sm">
                    {metrics.retrieval_relevance.ever_retrieved} ({(metrics.retrieval_relevance.retrieval_rate * 100).toFixed(1)}%)
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm font-medium">Recently Retrieved (24h):</span>
                  <span className="text-sm">{metrics.retrieval_relevance.recently_retrieved_24h}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm font-medium">Avg Searches per Memory:</span>
                  <span className="text-sm">{metrics.retrieval_relevance.avg_searches_per_memory.toFixed(2)}</span>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Growth Pattern */}
        {metrics?.growth_pattern && (
          <Card>
            <CardHeader>
              <CardTitle>Growth Pattern</CardTitle>
              <CardDescription>
                Growth Rate: {metrics.growth_pattern.growth_rate_per_day} memories/day
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="flex justify-between">
                  <span className="text-sm font-medium">Time Span:</span>
                  <span className="text-sm">{metrics.growth_pattern.time_span_days} days</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm font-medium">Add Attempts:</span>
                  <span className="text-sm">{metrics.growth_pattern.add_attempts}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm font-medium">Dedup Rejections:</span>
                  <span className="text-sm">
                    {metrics.growth_pattern.dedup_rejections} ({(metrics.growth_pattern.dedup_rejection_rate * 100).toFixed(1)}%)
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm font-medium">Addition Rate:</span>
                  <span className="text-sm">{metrics.growth_pattern.addition_rate_per_decision.toFixed(3)} per decision</span>
                </div>
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Growth Curve */}
      {timeline.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Memory Growth Over Time</CardTitle>
            <CardDescription>Cumulative memory count</CardDescription>
          </CardHeader>
          <CardContent>
            <Line
              data={{
                labels: timeline.map(t => t.date),
                datasets: [{
                  label: 'Total Memories',
                  data: timeline.map(t => t.cumulative_count),
                  borderColor: 'rgb(59, 130, 246)',
                  backgroundColor: 'rgba(59, 130, 246, 0.1)',
                  fill: true,
                  tension: 0.4
                }]
              }}
              options={{
                responsive: true,
                plugins: {
                  legend: { display: true }
                },
                scales: {
                  y: { beginAtZero: true }
                }
              }}
            />
          </CardContent>
        </Card>
      )}
    </div>
  )
}
