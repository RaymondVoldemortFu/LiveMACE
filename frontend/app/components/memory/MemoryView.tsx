import { useEffect, useState } from 'react'
import { Line, Bar, Doughnut } from 'react-chartjs-2'
import 'chart.js/auto'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Brain } from 'lucide-react'
import { getMemories, getMemoryMetrics, getMemoryGrowthTimeline } from '@/lib/api'

interface MemoryViewProps {
  account: any
  accounts: any[]
}

export function MemoryView({ account, accounts }: MemoryViewProps) {
  // Filter accounts with memory enabled
  const memoryEnabledAccounts = accounts.filter(acc => acc.memory_enabled === 'true')

  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(
    memoryEnabledAccounts.find(a => a.id === account?.id)?.id || memoryEnabledAccounts[0]?.id || null
  )
  const [selectedMarket, setSelectedMarket] = useState<'ALL' | 'CRYPTO' | 'US'>('ALL')
  const [memories, setMemories] = useState<any[]>([])
  const [metrics, setMetrics] = useState<any>(null)
  const [timeline, setTimeline] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Update selected account when prop changes
  useEffect(() => {
    if (account?.id && account.memory_enabled === 'true' && !selectedAccountId) {
      setSelectedAccountId(account.id)
    }
  }, [account?.id, account?.memory_enabled, selectedAccountId])

  useEffect(() => {
    if (!selectedAccountId) {
      setLoading(false)
      return
    }

    let cancelled = false
    setLoading(true)
    setError(null)

    const marketParam = selectedMarket !== 'ALL' ? selectedMarket : undefined

    Promise.all([
      getMemories(selectedAccountId, marketParam),
      getMemoryMetrics(selectedAccountId, marketParam),
      getMemoryGrowthTimeline(selectedAccountId, marketParam)
    ])
      .then(([memData, metricData, timelineData]) => {
        if (!cancelled) {
          setMemories(memData.memories || [])
          setMetrics(metricData)
          setTimeline(timelineData.timeline || [])
          setLoading(false)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message)
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [selectedAccountId, selectedMarket])

  if (memoryEnabledAccounts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-96 space-y-4">
        <p className="text-muted-foreground text-lg">No accounts with memory enabled</p>
        <p className="text-sm text-muted-foreground">Enable memory in account settings to use this feature</p>
      </div>
    )
  }

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
      <div className="h-full flex flex-col p-4 gap-4">
        <div className="flex items-center justify-between">
          <h2 className="text-2xl font-bold flex items-center gap-2">
            <Brain className="w-6 h-6" />
            Memory System
          </h2>
          <div className="flex items-center gap-2">
            <Select value={selectedMarket} onValueChange={(v) => setSelectedMarket(v as 'ALL' | 'CRYPTO' | 'US')}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All Markets</SelectItem>
                <SelectItem value="CRYPTO">Crypto</SelectItem>
                <SelectItem value="US">US Stocks</SelectItem>
              </SelectContent>
            </Select>
            <Select value={selectedAccountId.toString()} onValueChange={(v) => setSelectedAccountId(Number(v))}>
              <SelectTrigger className="w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {memoryEnabledAccounts.map((acc) => (
                  <SelectItem key={acc.id} value={acc.id.toString()}>
                    {acc.name} ({acc.account_type})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
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
    <div className="h-full flex flex-col p-4 gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold flex items-center gap-2">
          <Brain className="w-6 h-6" />
          Memory System
        </h2>
        <div className="flex items-center gap-2">
          <Select value={selectedMarket} onValueChange={(v) => setSelectedMarket(v as 'ALL' | 'CRYPTO' | 'US')}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">All Markets</SelectItem>
              <SelectItem value="CRYPTO">Crypto</SelectItem>
              <SelectItem value="US">US Stocks</SelectItem>
            </SelectContent>
          </Select>
          <Select value={selectedAccountId.toString()} onValueChange={(v) => setSelectedAccountId(Number(v))}>
            <SelectTrigger className="w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {memoryEnabledAccounts.map((acc) => (
                <SelectItem key={acc.id} value={acc.id.toString()}>
                  {acc.name} ({acc.account_type})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
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
                    <TableHead className="w-24">Market</TableHead>
                    <TableHead className="w-32">Retrieval Count</TableHead>
                    <TableHead className="w-48">Created At</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {memories.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell className="font-mono text-sm">{m.content}</TableCell>
                      <TableCell>
                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${m.market === 'US' ? 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200' : 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200'}`}>
                          {m.market || 'CRYPTO'}
                        </span>
                      </TableCell>
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

        {/* Metrics Row: Retrieval Distribution (wider) + Diversity & Growth stacked */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Retrieval Distribution - spans 2 columns */}
          {metrics?.retrieval_distribution && (
            <Card className="md:col-span-2">
              <CardHeader>
                <CardTitle>Retrieval Distribution</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-6">
                  <div>
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
                  </div>
                  <div className="flex items-center justify-center h-full">
                    <div className="grid grid-cols-2 w-full h-full">
                      <div className="p-4 flex flex-col items-center justify-center border-r border-b">
                        <p className="text-xs text-muted-foreground">Zombie Rate</p>
                        <p className="text-2xl font-semibold">{(metrics.retrieval_distribution.zombie_rate * 100).toFixed(1)}%</p>
                      </div>
                      <div className="p-4 flex flex-col items-center justify-center border-b">
                        <p className="text-xs text-muted-foreground">High-Value Rate</p>
                        <p className="text-2xl font-semibold">{(metrics.retrieval_distribution.high_value_rate * 100).toFixed(1)}%</p>
                      </div>
                      <div className="p-4 flex flex-col items-center justify-center border-r">
                        <p className="text-xs text-muted-foreground">Avg Retrieval</p>
                        <p className="text-2xl font-semibold">{metrics.retrieval_distribution.avg_retrieval_count}</p>
                      </div>
                      <div className="p-4 flex flex-col items-center justify-center">
                        <p className="text-xs text-muted-foreground">Recently (24h)</p>
                        <p className="text-2xl font-semibold">{metrics.retrieval_distribution.recently_retrieved_24h ?? 0}</p>
                      </div>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Right column: Diversity on top, Growth Pattern below, equal height */}
          <div className="flex flex-col gap-6">
            {/* Memory Diversity - gauge */}
            {metrics?.memory_diversity && (
              <Card className="flex-1">
                <CardHeader>
                  <CardTitle>Memory Diversity</CardTitle>
                  <CardDescription>
                    {metrics.memory_diversity.interpretation || 'Semantic diversity analysis'}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="flex items-center justify-center">
                    <div className="relative w-36 h-20">
                      <Doughnut
                        data={{
                          labels: ['Diversity', 'Similarity'],
                          datasets: [{
                            data: [
                              metrics.memory_diversity.diversity_score || 0,
                              metrics.memory_diversity.avg_similarity || 0
                            ],
                            backgroundColor: ['rgba(34, 197, 94, 0.5)', 'rgba(239, 68, 68, 0.2)'],
                            borderColor: ['rgb(34, 197, 94)', 'rgb(239, 68, 68)'],
                            borderWidth: 1
                          }]
                        }}
                        options={{
                          responsive: true,
                          maintainAspectRatio: false,
                          circumference: 180,
                          rotation: -90,
                          cutout: '70%',
                          plugins: {
                            legend: { display: false },
                            tooltip: { enabled: false }
                          }
                        }}
                      />
                      <div className="absolute bottom-0 left-1/2 -translate-x-1/2 text-center">
                        <span className="text-lg font-semibold">{(metrics.memory_diversity.diversity_score || 0).toFixed(3)}</span>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Growth Pattern */}
            {metrics?.growth_pattern && (
              <Card className="flex-1">
                <CardHeader>
                  <CardTitle>Growth Pattern</CardTitle>
                  <CardDescription>
                    {metrics.growth_pattern.growth_rate_per_day} memories/day
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-1.5">
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
        </div>

        {/* Memory Growth Trend - full width, compact */}
        {timeline.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Memory Growth Trend</CardTitle>
              <CardDescription>Cumulative memory count</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-96">
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
                    maintainAspectRatio: false,
                    plugins: {
                      legend: { display: false }
                    },
                    scales: {
                      y: { beginAtZero: true }
                    }
                  }}
                />
              </div>
            </CardContent>
          </Card>
        )}
    </div>
  )
}
