import { useEffect, useMemo, useState } from 'react'
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
import { Card } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { isBaselineAccountName } from '@/lib/baselineAccounts'
import { getDecisionSchedule } from '@/lib/api/accounts'
import { useAssetCurve } from '@/hooks/useAssetCurve'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend)

interface AssetCurveData {
  timestamp?: number
  datetime_str?: string
  date?: string
  total_assets: number
  initial_capital: number
  profit: number
  user_id: number
  username: string
}

type AccountLike = Pick<import('@/lib/api/generated-types').TradingAccount,
  'id' | 'name' | 'model' | 'agent_type' | 'memory_enabled' | 'tool_routing_enabled' | 'enable_rule_aware'>

interface ComprehensiveCurveViewProps {
  data?: AssetCurveData[]
  accounts?: AccountLike[]
  wsRef?: React.MutableRefObject<WebSocket | null>
}

type Timeframe = '5m' | '1h' | '1d'
type CurveCategory =
  | 'avg-by-model'
  | 'baseline'
  | 'tool'
  | 'memory'
  | 'rule'
  | 'react'
  | 'advanced_multi_agent'
  | 'all'

const CATEGORY_ORDER: CurveCategory[] = [
  'avg-by-model',
  'baseline',
  'tool',
  'memory',
  'rule',
  'react',
  'advanced_multi_agent',
  'all',
]

const CATEGORY_LABEL: Record<CurveCategory, string> = {
  'avg-by-model': '同模型跨架构平均收益',
  baseline: 'Baseline',
  tool: 'Tool',
  memory: 'Memory',
  rule: 'Rule',
  react: 'React',
  advanced_multi_agent: 'Advanced Multi Agent',
  all: '全部',
}

const colorPalette = [
  'rgb(59, 130, 246)',
  'rgb(34, 197, 94)',
  'rgb(168, 85, 247)',
  'rgb(239, 68, 68)',
  'rgb(245, 158, 11)',
  'rgb(16, 185, 129)',
  'rgb(139, 92, 246)',
  'rgb(236, 72, 153)',
  'rgb(14, 165, 233)',
  'rgb(132, 204, 22)',
  'rgb(234, 88, 12)',
]

function getCurveCategory(account?: AccountLike): Exclude<CurveCategory, 'avg-by-model' | 'all'> {
  if (!account) return 'react'
  if (isBaselineAccountName(account.name)) return 'baseline'
  if ((account.agent_type || '').trim().toLowerCase() === 'advanced_multi_agent') return 'advanced_multi_agent'
  if (account.enable_rule_aware === true) return 'rule'
  if ((account.memory_enabled || '').trim().toLowerCase() === 'true') return 'memory'
  if ((account.tool_routing_enabled || '').trim().toLowerCase() === 'true') return 'tool'
  return 'react'
}

const parseKeyToDate = (key: string): Date => {
  const trimmed = (key || '').trim()
  if (/^\d+$/.test(trimmed)) {
    const num = Number(trimmed)
    if (trimmed.length <= 10) return new Date(num * 1000)
    return new Date(num)
  }
  return new Date(trimmed)
}

const formatProfit = (item: AssetCurveData): number => {
  if (typeof item.profit === 'number' && !Number.isNaN(item.profit)) return item.profit
  return (item.total_assets || 0) - (item.initial_capital || 0)
}

export default function ComprehensiveCurveView({ data: initialData, accounts = [], wsRef }: ComprehensiveCurveViewProps) {
  const [timeframe, setTimeframe] = useState<Timeframe>('5m')
  const { data, loading, error } = useAssetCurve<AssetCurveData>(wsRef, timeframe, initialData)
  const [category, setCategory] = useState<CurveCategory>('avg-by-model')
  const [nextDecisionTimeUtc8, setNextDecisionTimeUtc8] = useState<string>('加载中...')

  const locale = typeof navigator !== 'undefined' ? navigator.language : undefined
  const currency2Formatter = new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  const currency0Formatter = new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  })

  const accountByName = useMemo(() => {
    const map = new Map<string, AccountLike>()
    for (const acc of accounts || []) {
      map.set(acc.name, acc)
    }
    return map
  }, [accounts])

  useEffect(() => {
    let mounted = true

    const formatUtc8 = (isoString: string): string => {
      const date = new Date(isoString)
      if (Number.isNaN(date.getTime())) return '时间格式错误'
      return date.toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      })
    }

    const fetchSchedule = async () => {
      try {
        const schedule = await getDecisionSchedule()
        if (!mounted) return
        setNextDecisionTimeUtc8(formatUtc8(schedule.next_decision_time_utc8))
      } catch (err) {
        console.error('Failed to fetch decision schedule', err)
        if (!mounted) return
        setNextDecisionTimeUtc8('获取失败')
      }
    }

    fetchSchedule()
    const timer = setInterval(fetchSchedule, 30000)
    return () => {
      mounted = false
      clearInterval(timer)
    }
  }, [])

  const groupedData = useMemo(() => {
    return data.reduce((acc, item) => {
      const key = item.datetime_str || item.date || item.timestamp?.toString() || ''
      if (!key) return acc
      if (!acc[key]) acc[key] = {}
      acc[key][item.username] = formatProfit(item)
      return acc
    }, {} as Record<string, Record<string, number>>)
  }, [data])

  const timestamps = useMemo(
    () => Object.keys(groupedData).sort((a, b) => parseKeyToDate(a).getTime() - parseKeyToDate(b).getTime()),
    [groupedData]
  )

  const usernames = useMemo(() => Array.from(new Set(data.map(item => item.username))).sort(), [data])

  const categoryByUsername = useMemo(() => {
    const map = new Map<string, Exclude<CurveCategory, 'avg-by-model' | 'all'>>()
    for (const username of usernames) {
      map.set(username, getCurveCategory(accountByName.get(username)))
    }
    return map
  }, [usernames, accountByName])

  const modelByUsername = useMemo(() => {
    const map = new Map<string, string>()
    for (const username of usernames) {
      const acc = accountByName.get(username)
      const modelKey =
        acc && isBaselineAccountName(acc.name)
          ? 'baseline'
          : (acc?.model && String(acc.model).trim()) || 'unknown-model'
      map.set(username, modelKey)
    }
    return map
  }, [usernames, accountByName])

  const formatLabel = (timestamp: string) => {
    const d = parseKeyToDate(timestamp)
    if (timeframe === '5m') {
      return d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', hour12: false })
    }
    if (timeframe === '1h') {
      return d.toLocaleString(locale, { month: '2-digit', day: '2-digit', hour: '2-digit', hour12: false })
    }
    return d.toLocaleDateString(locale, { month: '2-digit', day: '2-digit' })
  }

  const datasets = useMemo(() => {
    if (category === 'avg-by-model') {
      const models = Array.from(new Set(Array.from(modelByUsername.values()))).sort()
      return models.map((model, idx) => {
        const modelUsers = usernames.filter(u => modelByUsername.get(u) === model)
        const values = timestamps.map(ts => {
          const valid = modelUsers
            .map(u => groupedData[ts]?.[u])
            .filter((v): v is number => typeof v === 'number' && !Number.isNaN(v))
          if (valid.length === 0) return 0
          return valid.reduce((sum, v) => sum + v, 0) / valid.length
        })
        const color = colorPalette[idx % colorPalette.length]
        return {
          label: `${model} (AVG)`,
          data: values,
          borderColor: color,
          backgroundColor: color.replace('rgb', 'rgba').replace(')', ', 0.1)'),
          borderWidth: 2.5,
          fill: false,
          tension: 0.15,
        }
      })
    }

    const filteredUsers =
      category === 'all'
        ? usernames
        : usernames.filter(username => categoryByUsername.get(username) === category)

    return filteredUsers.map((username, idx) => {
      const color = colorPalette[idx % colorPalette.length]
      return {
        label: username,
        data: timestamps.map(ts => groupedData[ts]?.[username] ?? 0),
        borderColor: color,
        backgroundColor: color.replace('rgb', 'rgba').replace(')', ', 0.1)'),
        borderWidth: 2,
        fill: false,
        tension: 0.1,
      }
    })
  }, [category, usernames, modelByUsername, timestamps, groupedData, categoryByUsername])

  const chartData = {
    labels: timestamps.map(formatLabel),
    datasets,
  }

  const options: ChartOptions<'line'> = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { position: 'top' as const },
      title: { display: false },
      tooltip: {
        mode: 'index',
        intersect: false,
        callbacks: {
          label: (context) => {
            const label = context.dataset.label || ''
            const value = Number(context.parsed.y)
            const formatted = !Number.isNaN(value) ? currency2Formatter.format(value) : ''
            return `${label}: ${formatted}`
          },
        },
      },
    },
    scales: {
      x: {
        title: { display: true, text: 'Time' },
      },
      y: {
        title: { display: true, text: 'Profit (USD)' },
        ticks: {
          callback: (value: string | number) => {
            const num = Number(value)
            if (Number.isNaN(num)) return ''
            const sign = num >= 0 ? '+' : '-'
            return sign + currency0Formatter.format(Math.abs(num))
          },
        },
      },
    },
    interaction: {
      mode: 'nearest',
      axis: 'x',
      intersect: false,
    },
  }

  if (!data || data.length === 0) {
    return (
      <Card className="p-6 h-full">
        <div className="space-y-4 h-full">
          <div className="flex justify-between items-center gap-4">
            <Tabs value={timeframe} onValueChange={(v) => setTimeframe(v as Timeframe)}>
              <TabsList>
                <TabsTrigger value="5m">5 Minutes</TabsTrigger>
                <TabsTrigger value="1h">1 Hour</TabsTrigger>
                <TabsTrigger value="1d">1 Day</TabsTrigger>
              </TabsList>
            </Tabs>
            <div className="text-sm text-muted-foreground">
              下一次决策时间(UTC+8): <span className="font-medium text-foreground">{nextDecisionTimeUtc8}</span>
            </div>
          </div>
          <div className="flex items-center justify-center h-[72vh]">
            <div className="text-muted-foreground">{loading ? 'Loading...' : error || 'No asset data available'}</div>
          </div>
        </div>
      </Card>
    )
  }

  return (
    <Card className="p-6 h-full">
      <div className="space-y-4 h-full">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Tabs value={timeframe} onValueChange={(v) => setTimeframe(v as Timeframe)}>
            <TabsList>
              <TabsTrigger value="5m">5 Minutes</TabsTrigger>
              <TabsTrigger value="1h">1 Hour</TabsTrigger>
              <TabsTrigger value="1d">1 Day</TabsTrigger>
            </TabsList>
          </Tabs>

          <div className="flex flex-wrap items-center gap-3">
            <div className="text-sm text-muted-foreground">
              下一次决策时间(UTC+8): <span className="font-medium text-foreground">{nextDecisionTimeUtc8}</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {CATEGORY_ORDER.map((c) => (
                <Button
                  key={c}
                  size="sm"
                  variant={category === c ? 'default' : 'outline'}
                  onClick={() => setCategory(c)}
                >
                  {CATEGORY_LABEL[c]}
                </Button>
              ))}
            </div>
          </div>
        </div>

        <div className="h-[78vh]">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-muted-foreground">Loading...</div>
            </div>
          ) : (
            <Line data={chartData} options={options} />
          )}
        </div>
      </div>
    </Card>
  )
}
