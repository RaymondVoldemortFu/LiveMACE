import React, { useEffect, useRef, useState } from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import { Toaster, toast } from 'react-hot-toast'

const resolveWsUrl = () => {
  if (typeof window === 'undefined') return 'ws://localhost:5611/ws'
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws`
}


import Header from '@/components/layout/Header'
import Sidebar from '@/components/layout/Sidebar'
import Portfolio from '@/components/portfolio/Portfolio'
import ComprehensiveCurveView from '@/components/portfolio/ComprehensiveCurveView'
import ComprehensiveDetailsView from '@/components/portfolio/ComprehensiveDetailsView'
import AgentStatusView from '@/components/agent/AgentStatusView'
import { MemoryView } from '@/components/memory/MemoryView'
import ComplianceDashboard from '@/components/compliance/ComplianceDashboard'
import { AIDecision, getAccounts } from '@/lib/api'

interface User {
  id: number
  username: string
}

interface Account {
  id: number
  user_id: number
  name: string
  account_type: string
  initial_capital: number
  current_cash: number
  frozen_cash: number
  is_active: boolean
  enable_rule_aware?: boolean  // Add support for rule-aware flag
}

interface Overview {
  account: Account
  // Required by child components
  return_rate: number
  total_notional_value: number
  positions_notional_value: number
  // Optional extras for compatibility with snapshots
  total_assets?: number
  positions_value?: number
  positions_market_value?: number
  portfolio?: {
    total_assets: number
    positions_value: number
  }
}
interface Position { id: number; account_id: number; symbol: string; name: string; market: string; quantity: number; available_quantity: number; avg_cost: number; leverage: number; last_price?: number | null; market_value?: number | null; notional_value?: number | null }
interface Order { id: number; order_no: string; symbol: string; name: string; market: string; side: string; order_type: string; price?: number; quantity: number; leverage: number; filled_quantity: number; status: string }
interface Trade { id: number; order_id: number; account_id: number; symbol: string; name: string; market: string; side: string; price: number; quantity: number; commission: number; trade_time: string }

const PAGE_TITLES: Record<string, string> = {
  portfolio: 'Crypto Paper Trading',
  comprehensive: '同花顺Bench - 曲线总览',
  'comprehensive-details': '同花顺Bench - 数据明细',
  'agent-status': 'Agent Status',
  memory: 'Memory System',
  compliance: 'Rule Compliance',
}

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [account, setAccount] = useState<Account | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [orders, setOrders] = useState<Order[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [aiDecisions, setAiDecisions] = useState<AIDecision[]>([])
  const [allAssetCurves, setAllAssetCurves] = useState<any[]>([])
  const [currentPage, setCurrentPage] = useState<string>('comprehensive')
  const [accountRefreshTrigger, setAccountRefreshTrigger] = useState<number>(0)
  const wsRef = useRef<WebSocket | null>(null)
  const selectedAccountRef = useRef<number | null>(null)
  const selectedUserRef = useRef<User | null>(null)
  const [accounts, setAccounts] = useState<any[]>([])
  const [accountsLoading, setAccountsLoading] = useState<boolean>(true)

  useEffect(() => {
    let reconnectTimer: NodeJS.Timeout | null = null
    let ws: WebSocket | null = null
    let disposed = false
    let removeHandlers = () => {}
    
    const connectWebSocket = () => {
      if (disposed) return
      removeHandlers()
      try {
        const socket = new WebSocket(resolveWsUrl())
        ws = socket
        wsRef.current = socket
        
        const handleOpen = () => {
          if (disposed) return
          console.log('WebSocket connected')
          socket.send(JSON.stringify({ type: 'bootstrap', username: selectedUserRef.current?.username || 'default', initial_capital: 10000 }))
        }
        
        const handleMessage = (e: MessageEvent) => {
          if (disposed || wsRef.current !== socket) return
          try {
            const msg = JSON.parse(e.data)
            if (msg.type === 'bootstrap_ok') {
              if (msg.user) {
                selectedUserRef.current = msg.user
                setUser(msg.user)
              }
              if (msg.account) {
                if (selectedAccountRef.current !== null && selectedAccountRef.current !== msg.account.id) {
                  socket.send(JSON.stringify({ type: 'switch_account', account_id: selectedAccountRef.current }))
                  refreshAccounts()
                  return
                }
                selectedAccountRef.current = msg.account.id
                setAccount(msg.account)
              }
              // refresh accounts list once bootstrapped
              refreshAccounts()
              // request initial snapshot
              socket.send(JSON.stringify({ type: 'get_snapshot' }))
            } else if (msg.type === 'snapshot' || msg.type === 'snapshot_full' || msg.type === 'snapshot_fast') {
              const snapshotAccount = msg.overview?.account
              if (!snapshotAccount) return
              if (selectedAccountRef.current !== null && snapshotAccount.id !== selectedAccountRef.current) return
              if (selectedAccountRef.current === null) {
                // switch_user acknowledges only the user; its snapshot selects
                // the new default account. Ignore queued snapshots from the old user.
                if (snapshotAccount.user_id !== selectedUserRef.current?.id) return
                selectedAccountRef.current = snapshotAccount.id
                setAccount(snapshotAccount)
              }
              setOverview(msg.overview)
              setPositions(msg.positions)
              setOrders(msg.orders)
              setTrades(msg.trades || [])
              setAiDecisions(msg.ai_decisions || [])
              // Only update asset curves if provided (snapshot_full includes them)
              if (msg.all_asset_curves) {
                setAllAssetCurves(msg.all_asset_curves)
              }
            } else if (msg.type === 'trades') {
              setTrades(msg.trades || [])
            } else if (msg.type === 'order_filled') {
              toast.success('Order filled')
              socket.send(JSON.stringify({ type: 'get_snapshot' }))
            } else if (msg.type === 'order_pending') {
              toast('Order placed, waiting for fill', { icon: '⏳' })
              socket.send(JSON.stringify({ type: 'get_snapshot' }))
            } else if (msg.type === 'user_switched') {
              toast.success(`Switched to ${msg.user.username}`)
              selectedUserRef.current = msg.user
              selectedAccountRef.current = null
              setAccount(null)
              setOverview(null)
              setUser(msg.user)
            } else if (msg.type === 'account_switched') {
              toast.success(`Switched to ${msg.account.name}`)
              if (selectedAccountRef.current !== msg.account.id) setOverview(null)
              selectedAccountRef.current = msg.account.id
              setAccount(msg.account)
              refreshAccounts()
            } else if (msg.type === 'error') {
              console.error(msg.message)
              toast.error(msg.message || 'Order error')
            }
          } catch (err) {
            console.error('Failed to parse WebSocket message:', err)
          }
        }
        
        const handleClose = (event: CloseEvent) => {
          console.log('WebSocket closed:', event.code, event.reason)
          if (wsRef.current === socket) wsRef.current = null
          // Server shutdown also uses normal close codes (1000/1001).
          // Only this effect's cleanup makes a disconnect intentional.
          if (!disposed) {
            setOverview(null)
            reconnectTimer = setTimeout(connectWebSocket, 3000)
          }
        }
        
        const handleError = (event: Event) => {
          console.error('WebSocket error:', event)
          // Don't show toast for every error to avoid spam
          // toast.error('Connection error')
        }

        socket.addEventListener('open', handleOpen)
        socket.addEventListener('message', handleMessage)
        socket.addEventListener('close', handleClose)
        socket.addEventListener('error', handleError)
        
        removeHandlers = () => {
          socket.removeEventListener('open', handleOpen)
          socket.removeEventListener('message', handleMessage)
          socket.removeEventListener('close', handleClose)
          socket.removeEventListener('error', handleError)
        }
      } catch (err) {
        console.error('Failed to create WebSocket:', err)
        // Retry connection after 5 seconds
        reconnectTimer = setTimeout(connectWebSocket, 5000)
      }
    }
    
    connectWebSocket()

    return () => {
      disposed = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      removeHandlers()
      if (wsRef.current === ws) wsRef.current = null
      ws?.close(1000, 'App unmounted')
    }
  }, [])

  // Centralized accounts fetcher
  const refreshAccounts = async () => {
    try {
      setAccountsLoading(true)
      const list = await getAccounts()
      setAccounts(list)
    } catch (e) {
      console.error('Failed to fetch accounts', e)
    } finally {
      setAccountsLoading(false)
    }
  }

  // Fetch accounts on mount and when settings updated
  useEffect(() => {
    refreshAccounts()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountRefreshTrigger])

  const switchUser = (username: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.warn('WS not connected, cannot switch user')
      toast.error('Not connected to server')
      return
    }
    try {
      wsRef.current.send(JSON.stringify({ type: 'switch_user', username }))
      toast('Switching account...', { icon: '🔄' })
    } catch (e) {
      console.error(e)
      toast.error('Failed to switch user')
    }
  }

  const switchAccount = (accountId: number) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.warn('WS not connected, cannot switch account')
      toast.error('Not connected to server')
      return
    }
    try {
      wsRef.current.send(JSON.stringify({ type: 'switch_account', account_id: accountId }))
      toast('Switching account...', { icon: '🔄' })
    } catch (e) {
      console.error(e)
      toast.error('Failed to switch account')
    }
  }

  const handleAccountUpdated = () => {
    // Increment refresh trigger to force AccountSelector to refresh
    setAccountRefreshTrigger(prev => prev + 1)
    
    // Also refresh the current data snapshot
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'get_snapshot' }))
    }
  }

  if (!user || !account || !overview) return <div className="p-8">Connecting to trading server...</div>

  const renderMainContent = () => {
    const refreshData = () => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'get_snapshot' }))
      }
    }

    return (
      <main className="flex-1 min-h-0 overflow-auto">
        <div className="min-h-full min-w-0 p-4 pb-20 md:pb-4">
          {currentPage === 'portfolio' && (
            <Portfolio
              overview={overview}
              positions={positions}
              orders={orders}
              trades={trades}
              aiDecisions={aiDecisions}
              allAssetCurves={allAssetCurves}
              wsRef={wsRef}
              onSwitchAccount={switchAccount}
              onRefreshData={refreshData}
              accountRefreshTrigger={accountRefreshTrigger}
              accounts={accounts}
              loadingAccounts={accountsLoading}
            />
          )}
          
          {currentPage === 'comprehensive' && (
            <ComprehensiveCurveView
              data={allAssetCurves}
              accounts={accounts}
              wsRef={wsRef}
            />
          )}

          {currentPage === 'comprehensive-details' && (
            <ComprehensiveDetailsView
              overview={overview}
              positions={positions}
              orders={orders}
              trades={trades}
              aiDecisions={aiDecisions}
              allAssetCurves={allAssetCurves}
              wsRef={wsRef}
              onSwitchAccount={switchAccount}
              onRefreshData={refreshData}
              accountRefreshTrigger={accountRefreshTrigger}
              accounts={accounts}
              loadingAccounts={accountsLoading}
            />
          )}
          
          {currentPage === 'agent-status' && (
            <AgentStatusView accounts={accounts} />
          )}

          {currentPage === 'memory' && (
            <MemoryView account={account} accounts={accounts} />
          )}

          {currentPage === 'compliance' && (
            <ComplianceDashboard accounts={accounts} />
          )}
        </div>
      </main>
    )
  }

  const pageTitle = PAGE_TITLES[currentPage] ?? PAGE_TITLES.portfolio

  return (
    <div className="h-screen flex overflow-hidden">
      <Sidebar
        currentPage={currentPage}
        onPageChange={setCurrentPage}
        onAccountUpdated={handleAccountUpdated}
      />
      <div className="flex-1 min-w-0 flex flex-col">
        <Header
          title={pageTitle}
          currentUser={user}
          currentAccount={account}
          showAccountSelector={currentPage === 'portfolio' || currentPage === 'comprehensive-details'}
          onUserChange={switchUser}
        />
        {renderMainContent()}
      </div>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Toaster position="top-right" />
    <App />
  </React.StrictMode>,
)
