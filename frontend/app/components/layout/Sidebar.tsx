import { useState } from 'react'
import { PieChart, Settings, BarChart3, Bot, Brain, Shield, Table2 } from 'lucide-react'
import SettingsDialog from './SettingsDialog'

interface SidebarProps {
  currentPage?: string
  onPageChange?: (page: string) => void
  onAccountUpdated?: () => void  // Add callback to notify when accounts are updated
}

export default function Sidebar({ currentPage = 'comprehensive', onPageChange, onAccountUpdated }: SidebarProps) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  // NOTE: 业务要求临时隐藏 Paper Trading 入口；请保留相关代码，勿删除（DO NOT DELETE）。
  const SHOW_PAPER_TRADING_ENTRY = import.meta.env.VITE_ENABLE_PAPER_TRADING === "true"

  return (
    <>
      <aside className="w-0 md:w-16 shrink-0 md:border-r h-full md:p-2 flex flex-col items-center relative z-50 bg-background">
        {/* Desktop Navigation */}
        <nav className="hidden md:flex md:flex-col md:space-y-4">
          <button
            className={`flex items-center justify-center w-10 h-10 rounded-lg transition-colors ${
              currentPage === 'comprehensive'
                ? 'bg-secondary/80 text-secondary-foreground'
                : 'hover:bg-muted text-muted-foreground'
            }`}
            onClick={() => onPageChange?.('comprehensive')}
            title="LiveMACEBench"
          >
            <BarChart3 className="w-5 h-5" />
          </button>

          <button
            className={`flex items-center justify-center w-10 h-10 rounded-lg transition-colors ${
              currentPage === 'comprehensive-details'
                ? 'bg-secondary/80 text-secondary-foreground'
                : 'hover:bg-muted text-muted-foreground'
            }`}
            onClick={() => onPageChange?.('comprehensive-details')}
            title="Bench Details"
          >
            <Table2 className="w-5 h-5" />
          </button>

          {SHOW_PAPER_TRADING_ENTRY && (
            <button
              className={`flex items-center justify-center w-10 h-10 rounded-lg transition-colors ${
                currentPage === 'portfolio'
                  ? 'bg-secondary/80 text-secondary-foreground'
                  : 'hover:bg-muted text-muted-foreground'
              }`}
              onClick={() => onPageChange?.('portfolio')}
              title="Portfolio"
            >
              <PieChart className="w-5 h-5" />
            </button>
          )}

          <button
            className={`flex items-center justify-center w-10 h-10 rounded-lg transition-colors ${
              currentPage === 'agent-status'
                ? 'bg-secondary/80 text-secondary-foreground'
                : 'hover:bg-muted text-muted-foreground'
            }`}
            onClick={() => onPageChange?.('agent-status')}
            title="Agent Status"
          >
            <Bot className="w-5 h-5" />
          </button>

          <button
            className={`flex items-center justify-center w-10 h-10 rounded-lg transition-colors ${
              currentPage === 'memory'
                ? 'bg-secondary/80 text-secondary-foreground'
                : 'hover:bg-muted text-muted-foreground'
            }`}
            onClick={() => onPageChange?.('memory')}
            title="Memory"
          >
            <Brain className="w-5 h-5" />
          </button>

          <button
            className={`flex items-center justify-center w-10 h-10 rounded-lg transition-colors ${
              currentPage === 'compliance'
                ? 'bg-secondary/80 text-secondary-foreground'
                : 'hover:bg-muted text-muted-foreground'
            }`}
            onClick={() => onPageChange?.('compliance')}
            title="Rule Compliance"
          >
            <Shield className="w-5 h-5" />
          </button>

          <button
            className="flex items-center justify-center w-10 h-10 rounded-lg hover:bg-muted transition-colors text-muted-foreground"
            onClick={() => setSettingsOpen(true)}
            title="Settings"
          >
            <Settings className="w-5 h-5" />
          </button>
        </nav>

        {/* Mobile Navigation */}
        <nav className="md:hidden flex flex-row items-center justify-around fixed bottom-0 left-0 right-0 bg-background border-t h-16 px-4 z-50">
          <button
            className={`flex flex-col items-center justify-center w-12 h-12 rounded-lg transition-colors ${
              currentPage === 'comprehensive'
                ? 'bg-secondary/80 text-secondary-foreground'
                : 'hover:bg-muted text-muted-foreground'
            }`}
            onClick={() => onPageChange?.('comprehensive')}
            title="LiveMACEBench"
          >
            <BarChart3 className="w-5 h-5" />
            <span className="text-xs mt-1">Bench</span>
          </button>
          {SHOW_PAPER_TRADING_ENTRY && (
            <button
              className={`flex flex-col items-center justify-center w-12 h-12 rounded-lg transition-colors ${
                currentPage === 'portfolio'
                  ? 'bg-secondary/80 text-secondary-foreground'
                  : 'hover:bg-muted text-muted-foreground'
              }`}
              onClick={() => onPageChange?.('portfolio')}
              title="Portfolio"
            >
              <PieChart className="w-5 h-5" />
              <span className="text-xs mt-1">Portfolio</span>
            </button>
          )}
           <button
            className={`flex flex-col items-center justify-center w-12 h-12 rounded-lg transition-colors ${
              currentPage === 'agent-status'
                ? 'bg-secondary/80 text-secondary-foreground'
                : 'hover:bg-muted text-muted-foreground'
            }`}
            onClick={() => onPageChange?.('agent-status')}
            title="Agent Status"
          >
            <Bot className="w-5 h-5" />
            <span className="text-xs mt-1">Agent</span>
          </button>
          <button
            className={`flex flex-col items-center justify-center w-12 h-12 rounded-lg transition-colors ${
              currentPage === 'memory'
                ? 'bg-secondary/80 text-secondary-foreground'
                : 'hover:bg-muted text-muted-foreground'
            }`}
            onClick={() => onPageChange?.('memory')}
            title="Memory"
          >
            <Brain className="w-5 h-5" />
            <span className="text-xs mt-1">Memory</span>
          </button>
          <button
            className={`flex flex-col items-center justify-center w-12 h-12 rounded-lg transition-colors ${
              currentPage === 'comprehensive-details'
                ? 'bg-secondary/80 text-secondary-foreground'
                : 'hover:bg-muted text-muted-foreground'
            }`}
            onClick={() => onPageChange?.('comprehensive-details')}
            title="Bench Details"
          >
            <Table2 className="w-5 h-5" />
            <span className="text-xs mt-1">Detail</span>
          </button>

          <button
            className={`flex flex-col items-center justify-center w-12 h-12 rounded-lg transition-colors ${
              currentPage === 'compliance'
                ? 'bg-secondary/80 text-secondary-foreground'
                : 'hover:bg-muted text-muted-foreground'
            }`}
            onClick={() => onPageChange?.('compliance')}
            title="Rule Compliance"
          >
            <Shield className="w-5 h-5" />
            <span className="text-xs mt-1">Rules</span>
          </button>

          <button
            className="flex flex-col items-center justify-center w-12 h-12 rounded-lg hover:bg-muted transition-colors text-muted-foreground"
            onClick={() => setSettingsOpen(true)}
            title="Settings"
          >
            <Settings className="w-5 h-5" />
            <span className="text-xs mt-1">Settings</span>
          </button>
        </nav>
      </aside>

      {/* Settings Dialog */}
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        onAccountUpdated={onAccountUpdated}
      />
    </>
  )
}
