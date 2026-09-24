import React from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import { Toaster } from 'react-hot-toast'
import Header from '@/components/layout/Header'
import Sidebar from '@/components/layout/Sidebar'
import Portfolio from '@/components/portfolio/Portfolio'
import ComprehensiveCurveView from '@/components/portfolio/ComprehensiveCurveView'
import ComprehensiveDetailsView from '@/components/portfolio/ComprehensiveDetailsView'
import AgentStatusView from '@/components/agent/AgentStatusView'
import { MemoryView } from '@/components/memory/MemoryView'
import ComplianceDashboard from '@/components/compliance/ComplianceDashboard'
import { usePortfolioSnapshot } from '@/hooks/usePortfolioSnapshot'

const PAGE_TITLES: Record<string, string> = {
  portfolio: 'LiveMACE bench',
  comprehensive: 'LiveMACE bench - 曲线总览',
  'comprehensive-details': 'LiveMACE bench - 数据明细',
  'agent-status': 'Agent Status',
  memory: 'Memory System',
  compliance: 'Rule Compliance',
}

function App() {
  const [currentPage, setCurrentPage] = React.useState<string>('comprehensive')
  const portfolio = usePortfolioSnapshot()

  if (!portfolio.user || !portfolio.account || !portfolio.overview) {
    const label = portfolio.connectionStatus === 'reconnecting' || portfolio.connectionStatus === 'closed'
      ? 'Reconnecting to trading server...'
      : 'Connecting to trading server...'
    return <div className="p-8">{label}</div>
  }

  const renderMainContent = () => (
    <main className="flex-1 min-h-0 overflow-auto">
      <div className="min-h-full min-w-0 p-4 pb-20 md:pb-4">
        {currentPage === 'portfolio' && (
          <Portfolio
            overview={portfolio.overview}
            positions={portfolio.positions}
            orders={portfolio.orders}
            trades={portfolio.trades}
            aiDecisions={portfolio.aiDecisions}
            allAssetCurves={portfolio.allAssetCurves}
            wsRef={portfolio.wsRef}
            onSwitchAccount={portfolio.switchAccount}
            onRefreshData={portfolio.requestSnapshot}
            accountRefreshTrigger={portfolio.accountRefreshTrigger}
            accounts={portfolio.accounts}
            loadingAccounts={portfolio.accountsLoading}
          />
        )}

        {currentPage === 'comprehensive' && (
          <ComprehensiveCurveView
            data={portfolio.allAssetCurves as never}
            accounts={portfolio.accounts}
            wsRef={portfolio.wsRef}
          />
        )}

        {currentPage === 'comprehensive-details' && (
          <ComprehensiveDetailsView
            overview={portfolio.overview}
            positions={portfolio.positions}
            orders={portfolio.orders}
            trades={portfolio.trades}
            aiDecisions={portfolio.aiDecisions}
            allAssetCurves={portfolio.allAssetCurves}
            wsRef={portfolio.wsRef}
            onSwitchAccount={portfolio.switchAccount}
            onRefreshData={portfolio.requestSnapshot}
            accountRefreshTrigger={portfolio.accountRefreshTrigger}
            accounts={portfolio.accounts}
            loadingAccounts={portfolio.accountsLoading}
          />
        )}

        {currentPage === 'agent-status' && (
          <AgentStatusView accounts={portfolio.accounts} />
        )}

        {currentPage === 'memory' && (
          <MemoryView account={portfolio.account} accounts={portfolio.accounts} />
        )}

        {currentPage === 'compliance' && (
          <ComplianceDashboard accounts={portfolio.accounts} />
        )}
      </div>
    </main>
  )

  const pageTitle = PAGE_TITLES[currentPage] ?? PAGE_TITLES.portfolio

  return (
    <div className="h-screen flex overflow-hidden">
      <Sidebar
        currentPage={currentPage}
        onPageChange={setCurrentPage}
        onAccountUpdated={portfolio.notifyAccountUpdated}
      />
      <div className="flex-1 min-w-0 flex flex-col">
        <Header
          title={pageTitle}
          currentUser={portfolio.user}
          currentAccount={portfolio.account}
          showAccountSelector={currentPage === 'portfolio' || currentPage === 'comprehensive-details'}
          onUserChange={portfolio.switchUser}
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
