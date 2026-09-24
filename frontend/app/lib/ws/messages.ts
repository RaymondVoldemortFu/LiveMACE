import type {
  AIDecision,
  PortfolioAccount,
  PortfolioOrder,
  PortfolioOverview,
  PortfolioPosition,
  PortfolioTrade,
  PortfolioUser,
} from '@/lib/api/generated-types'

export type AssetCurvePoint = {
  timestamp?: number
  datetime_str?: string
  date?: string
  total_assets: number
  initial_capital: number
  profit: number
  user_id: number
  username: string
}

export type ServerMessage =
  | { type: 'bootstrap_ok'; user?: PortfolioUser; account?: PortfolioAccount }
  | {
      type: 'snapshot' | 'snapshot_full' | 'snapshot_fast'
      overview?: PortfolioOverview
      positions?: PortfolioPosition[]
      orders?: PortfolioOrder[]
      trades?: PortfolioTrade[]
      ai_decisions?: AIDecision[]
      all_asset_curves?: unknown[]
    }
  | { type: 'trades'; trades?: PortfolioTrade[] }
  | { type: 'order_filled' }
  | { type: 'order_pending' }
  | { type: 'user_switched'; user: PortfolioUser }
  | { type: 'account_switched'; account: PortfolioAccount }
  | { type: 'error'; message?: string }
  | { type: 'pong' }
  | { type: 'asset_curve_data'; timeframe?: string; data?: AssetCurvePoint[] }
  | { type: 'asset_curve_error'; timeframe?: string; message?: string }
  | { type: 'asset_curve_update'; timeframe?: string; data?: AssetCurvePoint[] }

const KNOWN_MESSAGE_TYPES = new Set<ServerMessage['type']>([
  'bootstrap_ok',
  'snapshot',
  'snapshot_full',
  'snapshot_fast',
  'trades',
  'order_filled',
  'order_pending',
  'user_switched',
  'account_switched',
  'error',
  'pong',
  'asset_curve_data',
  'asset_curve_error',
  'asset_curve_update',
])

export function decodeServerMessage(raw: string): ServerMessage | null {
  const value = JSON.parse(raw) as { type?: unknown }
  if (!value || typeof value.type !== 'string' || !KNOWN_MESSAGE_TYPES.has(value.type as ServerMessage['type'])) {
    return null
  }
  return value as ServerMessage
}

export type ClientMessage =
  | { type: 'bootstrap'; username: string; initial_capital: number }
  | { type: 'switch_user'; username: string }
  | { type: 'switch_account'; account_id: number }
  | { type: 'get_snapshot' }
  | { type: 'get_asset_curve'; timeframe: string }
  | { type: 'ping' }
  | ({ type: 'place_order' } & Record<string, unknown>)

export function resolveWsUrl(): string {
  if (typeof window === 'undefined') return 'ws://localhost:5611/ws'
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws`
}

export function sendClientMessage(socket: WebSocket | null, message: ClientMessage): void {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    throw new Error('Not connected to server')
  }
  socket.send(JSON.stringify(message))
}
