/**
 * Compliance Trend Chart - Line chart showing compliance metrics over time
 */
import React, { useEffect, useRef } from 'react'
import { type ComplianceTrend } from '@/lib/compliance-api'

interface ComplianceTrendChartProps {
  data: ComplianceTrend
}

export default function ComplianceTrendChart({ data }: ComplianceTrendChartProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (!canvasRef.current || !data.data_points.length) return

    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // Set canvas size
    const dpr = window.devicePixelRatio || 1
    const rect = canvas.getBoundingClientRect()
    canvas.width = rect.width * dpr
    canvas.height = rect.height * dpr
    ctx.scale(dpr, dpr)

    const width = rect.width
    const height = rect.height
    const padding = { top: 20, right: 20, bottom: 40, left: 60 }
    const chartWidth = width - padding.left - padding.right
    const chartHeight = height - padding.top - padding.bottom

    // Clear canvas
    ctx.clearRect(0, 0, width, height)

    // Get data points
    const points = data.data_points
    if (points.length === 0) return

    // Calculate scales
    const values = points.map((p) => p.value)
    const minValue = Math.min(...values) * 0.95
    const maxValue = Math.max(...values) * 1.05

    const xScale = chartWidth / (points.length - 1 || 1)
    const yScale = chartHeight / (maxValue - minValue || 1)

    // Draw grid lines
    ctx.strokeStyle = '#e5e7eb'
    ctx.lineWidth = 1
    for (let i = 0; i <= 5; i++) {
      const y = padding.top + (chartHeight * i) / 5
      ctx.beginPath()
      ctx.moveTo(padding.left, y)
      ctx.lineTo(padding.left + chartWidth, y)
      ctx.stroke()
    }

    // Draw y-axis labels
    ctx.fillStyle = '#6b7280'
    ctx.font = '12px sans-serif'
    ctx.textAlign = 'right'
    for (let i = 0; i <= 5; i++) {
      const value = maxValue - ((maxValue - minValue) * i) / 5
      const y = padding.top + (chartHeight * i) / 5
      ctx.fillText(value.toFixed(3), padding.left - 10, y + 4)
    }

    // Draw line chart
    ctx.strokeStyle = '#3b82f6'
    ctx.lineWidth = 2
    ctx.beginPath()
    points.forEach((point, i) => {
      const x = padding.left + i * xScale
      const y = padding.top + chartHeight - (point.value - minValue) * yScale

      if (i === 0) {
        ctx.moveTo(x, y)
      } else {
        ctx.lineTo(x, y)
      }
    })
    ctx.stroke()

    // Draw points
    ctx.fillStyle = '#3b82f6'
    points.forEach((point, i) => {
      const x = padding.left + i * xScale
      const y = padding.top + chartHeight - (point.value - minValue) * yScale
      ctx.beginPath()
      ctx.arc(x, y, 3, 0, Math.PI * 2)
      ctx.fill()
    })

    // Draw x-axis labels (show every nth label to avoid crowding)
    ctx.fillStyle = '#6b7280'
    ctx.font = '11px sans-serif'
    ctx.textAlign = 'center'
    const labelStep = Math.ceil(points.length / 8)
    points.forEach((point, i) => {
      if (i % labelStep === 0 || i === points.length - 1) {
        const x = padding.left + i * xScale
        const date = new Date(point.date)
        const label = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
        ctx.fillText(label, x, height - padding.bottom + 20)
      }
    })

    // Draw metric name
    ctx.fillStyle = '#374151'
    ctx.font = 'bold 14px sans-serif'
    ctx.textAlign = 'left'
    const metricNames: Record<string, string> = {
      gate_pass_rate: 'Gate Pass Rate',
      final_score: 'Final Score',
      s_rule_sat: 'Rule Satisfaction',
      s_audit: 'LLM Audit Score',
    }
    ctx.fillText(metricNames[data.metric] || data.metric, padding.left, 15)
  }, [data])

  if (!data.data_points.length) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        <p className="text-sm">No trend data available</p>
      </div>
    )
  }

  return (
    <div className="relative w-full h-64">
      <canvas
        ref={canvasRef}
        className="w-full h-full"
        style={{ width: '100%', height: '100%' }}
      />
    </div>
  )
}
