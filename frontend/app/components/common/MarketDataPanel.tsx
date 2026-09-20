import { useEffect, useState } from 'react'

interface Bar { timestamp: number; datetime: string; open: number; high: number; low: number; close: number }

export default function MarketDataPanel() {
  const [market, setMarket] = useState('US')
  const [symbol, setSymbol] = useState('AAPL')
  const [bars, setBars] = useState<Bar[]>([])
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(''); setBars([]); setStatus('')
    const read = async (url: string) => {
      const response = await fetch(url, { signal: controller.signal })
      const body = await response.json()
      if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Market data unavailable')
      return body
    }
    Promise.all([
      read(`/api/market/kline/${symbol}?market=${market}&period=1d&count=30`),
      read(`/api/market/status/${symbol}?market=${market}`)
    ]).then(([history, session]) => {
      setBars(history.data.filter((bar: Bar) => [bar.open, bar.high, bar.low, bar.close].every(v => Number.isFinite(v) && v > 0)))
      setStatus(session.market_status)
    }).catch(err => {
      if (!controller.signal.aborted) setError(err.message)
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [market, symbol])
  const min = bars.length ? Math.min(...bars.map(bar => bar.low)) : 0
  const max = bars.length ? Math.max(...bars.map(bar => bar.high)) : 1
  const y = (value: number) => 180 - (value - min) / Math.max(max - min, 0.01) * 150
  return <section className="border rounded p-4 mb-4 space-y-3" aria-label="Market data">
    <div className="flex gap-4 items-center flex-wrap">
      <h2 className="font-semibold">Market Data</h2>
      <select aria-label="Market" value={market} onChange={event => { const next = event.target.value; setMarket(next); setSymbol(next === 'US' ? 'AAPL' : 'BTC') }} className="border p-1 bg-background">
        <option value="US">US Stocks</option><option value="CRYPTO">Crypto</option>
      </select>
      <select aria-label="Market symbol" value={symbol} onChange={event => setSymbol(event.target.value)} className="border p-1 bg-background">
        {(market === 'US' ? ['AAPL', 'NVDA', 'TSLA'] : ['BTC', 'ETH', 'SOL']).map(item => <option key={item}>{item}</option>)}
      </select>
      <span className="text-sm">{market === 'US' ? 'Alpaca · IEX' : 'Hyperliquid'} · Daily candles · {status || 'Loading'}</span>
    </div>
    {loading ? <p>Loading market data...</p> : error ? <p role="alert" className="text-red-600">{error}</p> : bars.length === 0 ? <p>No market data available.</p> : <>
      <svg viewBox="0 0 900 210" className="w-full h-52" role="img" aria-label={`${symbol} daily candlestick chart`}>
        <text x="0" y="20" fill="currentColor" fontSize="12">${max.toFixed(2)}</text>
        <text x="0" y="185" fill="currentColor" fontSize="12">${min.toFixed(2)}</text>
        {bars.map((bar, index) => {
          const x = 80 + index * 800 / bars.length
          const color = bar.close >= bar.open ? '#16803c' : '#dc2626'
          return <g key={bar.timestamp}><title>{bar.datetime}: O {bar.open} H {bar.high} L {bar.low} C {bar.close}</title>
            <line x1={x} x2={x} y1={y(bar.high)} y2={y(bar.low)} stroke={color}/>
            <rect x={x - 4} width={8} y={Math.min(y(bar.open), y(bar.close))} height={Math.max(1, Math.abs(y(bar.open) - y(bar.close)))} fill={color}/>
          </g>
        })}
      </svg>
      <p className="text-xs text-muted-foreground">Latest candle: {bars[bars.length - 1].datetime} · Close ${bars[bars.length - 1].close.toFixed(2)}</p>
    </>}
  </section>
}
