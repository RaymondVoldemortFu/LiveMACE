import { useEffect, useRef, useState } from 'react'
import { toast } from 'react-hot-toast'
import { getAccounts } from '@/lib/api/accounts'
import type {
  AIDecision,
  PortfolioAccount,
  PortfolioOrder,
  PortfolioOverview,
  PortfolioPosition,
  PortfolioTrade,
  PortfolioUser,
  TradingAccount,
} from '@/lib/api/generated-types'
import { sendClientMessage, type ServerMessage } from '@/lib/ws/messages'
import { portfolioSocket, type ConnectionStatus } from '@/lib/ws/client'


function applySnapshot(
  message: Extract<ServerMessage, { type: 'snapshot' | 'snapshot_full' | 'snapshot_fast' }>,
  curves: unknown[],
) {
  return {
    overview: message.overview ?? null,
    positions: message.positions ?? [],
    orders: message.orders ?? [],
    trades: message.trades ?? [],
    aiDecisions: message.ai_decisions ?? [],
    allAssetCurves: message.all_asset_curves ?? curves,
  }
}

export function usePortfolioSnapshot() {
  const [user, setUser] = useState<PortfolioUser | null>(null)
  const [account, setAccount] = useState<PortfolioAccount | null>(null)
  const [overview, setOverview] = useState<PortfolioOverview | null>(null)
  const [positions, setPositions] = useState<PortfolioPosition[]>([])
  const [orders, setOrders] = useState<PortfolioOrder[]>([])
  const [trades, setTrades] = useState<PortfolioTrade[]>([])
  const [aiDecisions, setAiDecisions] = useState<AIDecision[]>([])
  const [allAssetCurves, setAllAssetCurves] = useState<unknown[]>([])
  const [accounts, setAccounts] = useState<TradingAccount[]>([])
  const [accountsLoading, setAccountsLoading] = useState(true)
  const [accountRefreshTrigger, setAccountRefreshTrigger] = useState(0)
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting')
  const wsRef = portfolioSocket.socketRef
  const selectedAccountRef = useRef<number | null>(null)
  const selectedUserRef = useRef<PortfolioUser | null>(null)
  const curvesRef = useRef<unknown[]>([])

  const refreshAccounts = async () => {
    try {
      setAccountsLoading(true)
      setAccounts(await getAccounts())
    } catch (error) {
      console.error('Failed to fetch accounts', error)
    } finally {
      setAccountsLoading(false)
    }
  }

  useEffect(() => portfolioSocket.connect({
    status: (status) => {
      setConnectionStatus(status)
      if (status === 'closed') setOverview(null)
    },
    open: () => {
      portfolioSocket.send({ type: 'bootstrap', username: selectedUserRef.current?.username || 'default', initial_capital: 10000 })
    },
    message: (message) => {
          if (message.type === 'bootstrap_ok') {
            if (message.user) {
              selectedUserRef.current = message.user
              setUser(message.user)
            }
            if (message.account) {
              if (selectedAccountRef.current !== null && selectedAccountRef.current !== message.account.id) {
                sendClientMessage(wsRef.current, { type: 'switch_account', account_id: selectedAccountRef.current })
                void refreshAccounts()
                return
              }
              selectedAccountRef.current = message.account.id
              setAccount(message.account)
            }
            void refreshAccounts()
            sendClientMessage(wsRef.current, { type: 'get_snapshot' })
          } else if (message.type === 'snapshot' || message.type === 'snapshot_full' || message.type === 'snapshot_fast') {
            const snapshotAccount = message.overview?.account
            if (!snapshotAccount) return
            if (selectedAccountRef.current !== null && snapshotAccount.id !== selectedAccountRef.current) return
            if (selectedAccountRef.current === null) {
              if (snapshotAccount.user_id !== selectedUserRef.current?.id) return
              selectedAccountRef.current = snapshotAccount.id
              setAccount(snapshotAccount)
            }
            const next = applySnapshot(message, curvesRef.current)
            setOverview(next.overview)
            setPositions(next.positions)
            setOrders(next.orders)
            setTrades(next.trades)
            setAiDecisions(next.aiDecisions)
            if (message.all_asset_curves) setAllAssetCurves(message.all_asset_curves)
          } else if (message.type === 'trades') {
            setTrades(message.trades || [])
          } else if (message.type === 'order_filled') {
            toast.success('Order filled')
            sendClientMessage(wsRef.current, { type: 'get_snapshot' })
          } else if (message.type === 'order_pending') {
            toast('Order placed, waiting for fill', { icon: '⏳' })
            sendClientMessage(wsRef.current, { type: 'get_snapshot' })
          } else if (message.type === 'user_switched') {
            toast.success(`Switched to ${message.user.username}`)
            selectedUserRef.current = message.user
            selectedAccountRef.current = null
            setAccount(null)
            setOverview(null)
            setUser(message.user)
          } else if (message.type === 'account_switched') {
            toast.success(`Switched to ${message.account.name}`)
            if (selectedAccountRef.current !== message.account.id) setOverview(null)
            selectedAccountRef.current = message.account.id
            setAccount(message.account)
            void refreshAccounts()
          } else if (message.type === 'error') {
            console.error(message.message)
            toast.error(message.message || 'Order error')
          }
    },
  }), [])

  useEffect(() => {
    curvesRef.current = allAssetCurves
  }, [allAssetCurves])

  useEffect(() => {
    void refreshAccounts()
  }, [accountRefreshTrigger])

  const switchUser = (username: string) => {
    try {
      sendClientMessage(wsRef.current, { type: 'switch_user', username })
      toast('Switching account...', { icon: '🔄' })
    } catch (error) {
      console.error(error)
      toast.error('Not connected to server')
    }
  }

  const switchAccount = (accountId: number) => {
    try {
      sendClientMessage(wsRef.current, { type: 'switch_account', account_id: accountId })
      toast('Switching account...', { icon: '🔄' })
    } catch (error) {
      console.error(error)
      toast.error('Not connected to server')
    }
  }

  const requestSnapshot = () => {
    try {
      sendClientMessage(wsRef.current, { type: 'get_snapshot' })
    } catch (error) {
      console.warn('WS not connected, cannot refresh snapshot', error)
    }
  }

  const notifyAccountUpdated = () => {
    setAccountRefreshTrigger((value) => value + 1)
    requestSnapshot()
  }

  return {
    user,
    account,
    overview,
    positions,
    orders,
    trades,
    aiDecisions,
    allAssetCurves,
    accounts,
    accountsLoading,
    accountRefreshTrigger,
    connectionStatus,
    wsRef,
    switchUser,
    switchAccount,
    requestSnapshot,
    notifyAccountUpdated,
  }
}
