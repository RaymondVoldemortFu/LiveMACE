import AccountDataView from './AccountDataView'
import type {
  AIDecision,
  PortfolioOrder as Order,
  PortfolioOverview as Overview,
  PortfolioPosition as Position,
  PortfolioTrade as Trade,
} from '@/lib/api/generated-types'

interface ComprehensiveViewProps {
  overview: Overview | null
  positions: Position[]
  orders: Order[]
  trades: Trade[]
  aiDecisions: AIDecision[]
  allAssetCurves: any[]
  wsRef?: React.MutableRefObject<WebSocket | null>
  onSwitchUser: (username: string) => void
  onSwitchAccount: (accountId: number) => void
  onRefreshData: () => void
  accountRefreshTrigger?: number
  accounts?: any[]
  loadingAccounts?: boolean
}

export default function ComprehensiveView({
  overview,
  positions,
  orders,
  trades,
  aiDecisions,
  allAssetCurves,
  wsRef,
  onSwitchAccount,
  onRefreshData,
  accountRefreshTrigger,
  accounts,
  loadingAccounts
}: ComprehensiveViewProps) {

  return (
    <AccountDataView
      overview={overview}
      positions={positions}
      orders={orders}
      trades={trades}
      aiDecisions={aiDecisions}
      allAssetCurves={allAssetCurves}
      wsRef={wsRef}
      onSwitchAccount={onSwitchAccount}
      onRefreshData={onRefreshData}
      accountRefreshTrigger={accountRefreshTrigger}
      accounts={accounts}
      loadingAccounts={loadingAccounts}
      showAssetCurves={true}
      showTradingPanel={false}
    />
  )
}

