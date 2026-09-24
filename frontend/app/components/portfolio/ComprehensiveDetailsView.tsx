import AccountDataView from './AccountDataView'
import type {
  AIDecision,
  PortfolioOrder as Order,
  PortfolioOverview as Overview,
  PortfolioPosition as Position,
  PortfolioTrade as Trade,
} from '@/lib/api/generated-types'

interface ComprehensiveDetailsViewProps {
  overview: Overview | null
  positions: Position[]
  orders: Order[]
  trades: Trade[]
  aiDecisions: AIDecision[]
  allAssetCurves: any[]
  wsRef?: React.MutableRefObject<WebSocket | null>
  onSwitchAccount: (accountId: number) => void
  onRefreshData: () => void
  accountRefreshTrigger?: number
  accounts?: any[]
  loadingAccounts?: boolean
}

export default function ComprehensiveDetailsView(props: ComprehensiveDetailsViewProps) {
  return <AccountDataView {...props} showAssetCurves={false} showTradingPanel={false} />
}
