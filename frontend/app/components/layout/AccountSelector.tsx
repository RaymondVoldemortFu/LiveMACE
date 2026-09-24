import { useEffect, useState } from 'react'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useAccounts } from '@/hooks/useAccounts'
import { isBaselineAccountName } from '@/lib/baselineAccounts'

type Account = Pick<import('@/lib/api/generated-types').PortfolioAccount,
  'id' | 'user_id' | 'name' | 'account_type' | 'initial_capital' | 'current_cash' | 'frozen_cash'>
  & Partial<Pick<import('@/lib/api/generated-types').TradingAccount, 'username' | 'model' | 'is_active'>>

interface AccountWithAssets extends Account {
  total_assets: number
  positions_value: number
}

interface AccountSelectorProps {
  currentAccount: Account | null
  onAccountChange: (accountId: number) => void
  username?: string
  refreshTrigger?: number  // Add refresh trigger prop
  accounts?: AccountWithAssets[] | Account[]  // External accounts to use when provided
  loadingExternal?: boolean  // External loading state
}

// Use relative path to work with proxy

export default function AccountSelector({
  currentAccount,
  onAccountChange,
  username = "default",
  refreshTrigger,
  accounts: externalAccounts,
  loadingExternal,
}: AccountSelectorProps) {
  const [accounts, setAccounts] = useState<AccountWithAssets[]>([])
  const [loading, setLoading] = useState(true)
  const loaded = useAccounts(refreshTrigger ?? 0)

  useEffect(() => {
    if (externalAccounts && externalAccounts.length >= 0) {
      const mapped = externalAccounts.map((a: any) => ({
        ...a,
        total_assets: (a as any).total_assets ?? ((a.current_cash || 0) + (a.frozen_cash || 0)),
        positions_value: (a as any).positions_value ?? 0,
      }))
      setAccounts(mapped)
      setLoading(loadingExternal ?? false)
      return
    }
    if (loaded.error) console.error('Error fetching accounts:', loaded.error)
    setAccounts(loaded.accounts.map((account) => ({
      ...account,
      total_assets: account.current_cash + account.frozen_cash,
      positions_value: 0,
    })))
    setLoading(loaded.loading)
  }, [username, refreshTrigger, externalAccounts, loadingExternal, loaded.accounts, loaded.loading, loaded.error])

  if (loading) {
    return (
      <div className="w-48">
        <div className="h-10 bg-gray-200 dark:bg-gray-700 rounded animate-pulse" />
      </div>
    )
  }

  if (accounts.length === 0) {
    return (
      <div className="w-64">
        <div className="text-xs text-muted-foreground p-2 border rounded">
          No accounts found
        </div>
      </div>
    )
  }

  const displayName = (account: AccountWithAssets) => {
    const accountName = account.name || account.username || `${account.account_type} Account`
    return accountName
  }

  const baselineAccounts = accounts.filter((a) => isBaselineAccountName(a.name))
  const otherAccounts = accounts.filter((a) => !isBaselineAccountName(a.name))

  const renderAccountItems = (list: AccountWithAssets[]) =>
    list.map((account) => (
      <SelectItem key={account.id} value={account.id.toString()}>
        {displayName(account)}
      </SelectItem>
    ))

  // Find the current account in our loaded accounts list (which has total_assets)
  const currentAccountWithAssets = currentAccount 
    ? accounts.find(a => a.id === currentAccount.id) 
    : null

  return (
    <div className="w-full">
      <Select
        value={currentAccount?.id.toString() || ''}
        onValueChange={(value) => onAccountChange(parseInt(value))}
      >
        <SelectTrigger className="w-full">
          <SelectValue placeholder="Select Account" className="truncate">
            <span className="truncate block">
              {currentAccountWithAssets 
                ? displayName(currentAccountWithAssets) 
                : currentAccount 
                  ? `${currentAccount.name || 'Unknown Account'}` 
                  : 'Select Account'
              }
            </span>
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {baselineAccounts.length > 0 ? (
            <SelectGroup>
              <SelectLabel>Baseline</SelectLabel>
              {renderAccountItems(baselineAccounts)}
            </SelectGroup>
          ) : null}
          {otherAccounts.length > 0 ? (
            <SelectGroup>
              {baselineAccounts.length > 0 ? <SelectLabel>Agents</SelectLabel> : null}
              {renderAccountItems(otherAccounts)}
            </SelectGroup>
          ) : null}
        </SelectContent>
      </Select>
    </div>
  )
}