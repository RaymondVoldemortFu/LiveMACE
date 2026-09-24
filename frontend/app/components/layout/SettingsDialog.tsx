import { useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Plus, Pencil, Eye } from 'lucide-react'
import {
  createAccount,
  getAccountSystemPrompt,
  getAccounts,
  testLLMConnection,
  updateAccount,
} from '@/lib/api/accounts'
import type { AccountSystemPromptResponse, TradingAccount, TradingAccountCreate } from '@/lib/api/generated-types'
import { RuntimeConfigPanel, type RuntimeConfigHandle } from '@/components/extensions/RuntimeConfigPanel'
import { isBaselineAccountName } from '@/lib/baselineAccounts'

interface SettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onAccountUpdated?: () => void  // Add callback for when account is updated
}

type AIAccount = TradingAccount
type AIAccountCreate = TradingAccountCreate

const AGENT_TYPE_OPTIONS = [
  { value: 'react', label: 'ReAct Agent' },
  { value: 'rule_aware', label: 'Rule-Aware Agent' },
  { value: 'multi_agent', label: 'Multi-Agent System' },
  { value: 'advanced_multi_agent', label: 'Advanced Multi-Agent System' },
  { value: 'buy_hold', label: 'Baseline: Buy & Hold' },
  { value: 'grid', label: 'Baseline: Grid Trading' },
]

const getAgentTypeLabel = (agentType?: string | null) => {
  const normalizedAgentType = (agentType || '').trim().toLowerCase()
  const matched = AGENT_TYPE_OPTIONS.find((option) => option.value === normalizedAgentType)
  return matched?.label || 'ReAct Agent'
}

export default function SettingsDialog({ open, onOpenChange, onAccountUpdated }: SettingsDialogProps) {
  const [accounts, setAccounts] = useState<AIAccount[]>([])
  const [loading, setLoading] = useState(false)
  const [showAddForm, setShowAddForm] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [viewingPromptAccountId, setViewingPromptAccountId] = useState<number | null>(null)
  const [promptLoadingAccountId, setPromptLoadingAccountId] = useState<number | null>(null)
  const [accountPrompts, setAccountPrompts] = useState<Record<number, AccountSystemPromptResponse>>({})
  const runtimeRef = useRef<RuntimeConfigHandle | null>(null)
  const [runtimeDirty, setRuntimeDirty] = useState(false)
  const [selectedRuntimeAgentId, setSelectedRuntimeAgentId] = useState<string | null>(null)
  const [newAccount, setNewAccount] = useState<AIAccountCreate>({
    name: '',
    model: '',
    base_url: '',
    api_key: '',
    enable_rule_aware: false,
    agent_type: 'react',
    memory_enabled: 'false',
    tool_routing_enabled: 'true',
  })
  const [editAccount, setEditAccount] = useState<AIAccountCreate>({
    name: '',
    model: '',
    base_url: '',
    api_key: '',
    agent_type: 'react',
    memory_enabled: 'false',
    tool_routing_enabled: 'true',
  })

  const loadAccounts = async () => {
    try {
      setLoading(true)
      const data = await getAccounts()
      setAccounts(data)
    } catch (error) {
      console.error('Failed to load accounts:', error)
      toast.error('Failed to load accounts')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) {
      loadAccounts()
      setError(null)
      setTestResult(null)
      setShowAddForm(false)
      setEditingId(null)
    }
  }, [open])

  const handleCreateAccount = async () => {
    try {
      setLoading(true)
      setTesting(true)
      setError(null)
      setTestResult(null)

      if (!newAccount.name || !newAccount.name.trim()) {
        setError('Account name is required')
        setLoading(false)
        setTesting(false)
        return
      }

      const hasAnyLLMField = Boolean(newAccount.model || newAccount.base_url || newAccount.api_key)
      const hasAllLLMFields = Boolean(newAccount.model && newAccount.base_url && newAccount.api_key)

      if (hasAnyLLMField && !hasAllLLMFields) {
        setError('Model、Base URL 和 API Key 必须同时填写')
        setLoading(false)
        setTesting(false)
        return
      }

      // If AI fields are provided, test LLM connection first
      if (hasAllLLMFields) {
        setTestResult('Testing LLM connection...')

        console.log('[SettingsDialog] Starting LLM test')
        console.log('[SettingsDialog] enable_rule_aware:', newAccount.enable_rule_aware, typeof newAccount.enable_rule_aware)

        try {
          const testResponse = await testLLMConnection({
            model: newAccount.model,
            base_url: newAccount.base_url,
            api_key: newAccount.api_key,
          })

          console.log('[SettingsDialog] LLM test response:', testResponse)

          if (!testResponse.success) {
            const message = testResponse.message || 'LLM connection test failed'
            setError(`LLM Test Failed: ${message}`)
            setTestResult(`❌ Test failed: ${message}`)
            setLoading(false)
            setTesting(false)
            return
          }
          setTestResult('✅ LLM connection test passed! Creating account...')
        } catch (testError) {
          console.error('[SettingsDialog] LLM test error:', testError)
          const message = testError instanceof Error ? testError.message : 'LLM connection test failed'
          setError(`LLM Test Failed: ${message}`)
          setTestResult(`❌ Test failed: ${message}`)
          setLoading(false)
          setTesting(false)
          return
        }
      }

      console.log('[SettingsDialog] Creating account with data:', newAccount)
      await createAccount(newAccount)
      setNewAccount({
        name: '',
        model: '',
        base_url: '',
        api_key: '',
        agent_type: 'react',
        memory_enabled: 'false',
        tool_routing_enabled: 'true',
        enable_rule_aware: false,
      })
      setShowAddForm(false)
      await loadAccounts()

      toast.success('Account created successfully!')

      // Notify parent component that account was created
      onAccountUpdated?.()
    } catch (error) {
      console.error('Failed to create account:', error)
      const errorMessage = error instanceof Error ? error.message : 'Failed to create account'
      setError(errorMessage)
      toast.error(`Failed to create account: ${errorMessage}`)
    } finally {
      setLoading(false)
      setTesting(false)
      setTestResult(null)
    }
  }

  const handleUpdateAccount = async () => {
    if (!editingId) return
    try {
      setLoading(true)
      setTesting(true)
      setError(null)
      setTestResult(null)
      
      if (!editAccount.name || !editAccount.name.trim()) {
        setError('Account name is required')
        setLoading(false)
        setTesting(false)
        return
      }
      
      const hasNewKey = Boolean(editAccount.api_key?.trim())
      const hasModelAndUrl = Boolean(editAccount.model?.trim() && editAccount.base_url?.trim())
      const agentId = runtimeRef.current?.agentId ?? null
      if (!agentId) {
        setError('Runtime components are still loading')
        setLoading(false)
        setTesting(false)
        return
      }
      const isBaseline = agentId === 'baseline.buy-hold'
        || agentId === 'baseline.grid'
        || isBaselineAccountName(editAccount.name)
      const requiresModel = accounts.find(account => account.id === editingId)?.account_type === 'AI' && !isBaseline
      if (requiresModel && !hasModelAndUrl) {
        setError('AI account requires Model and Base URL. Leave API Key blank to keep the current key.')
        return
      }
      const hasAllLLMFields = hasModelAndUrl && hasNewKey
      if (hasNewKey && !hasAllLLMFields) {
        setError('Model、Base URL 和 API Key 必须同时填写')
        return
      }

      // Test LLM connection first if AI model data is provided
      if (hasAllLLMFields) {
        setTestResult('Testing LLM connection...')
        
        try {
          const testResponse = await testLLMConnection({
            model: editAccount.model,
            base_url: editAccount.base_url,
            api_key: editAccount.api_key
          })
          
          if (!testResponse.success) {
            setError(`LLM Test Failed: ${testResponse.message}`)
            setTestResult(`❌ Test failed: ${testResponse.message}`)
            setLoading(false)
            setTesting(false)
            return
          }
          
          setTestResult('✅ LLM connection test passed!')
        } catch (testError) {
          const errorMessage = testError instanceof Error ? testError.message : 'LLM connection test failed'
          setError(`LLM Test Failed: ${errorMessage}`)
          setTestResult(`❌ Test failed: ${errorMessage}`)
          setLoading(false)
          setTesting(false)
          return
        }
      }
      
      setTesting(false)
      setTestResult('Saving account...')

      await runtimeRef.current?.save()
      await updateAccount(editingId, {
        name: editAccount.name,
        model: editAccount.model,
        base_url: editAccount.base_url,
        api_key: hasNewKey ? editAccount.api_key : undefined,
      })
      setEditingId(null)
      setEditAccount({
        name: '',
        model: '',
        base_url: '',
        api_key: '',
        agent_type: 'react',
        memory_enabled: 'false',
        tool_routing_enabled: 'true',
      })
      setTestResult(null)
      await loadAccounts()
      
      toast.success('Account updated successfully!')
      
      // Notify parent component that account was updated
      onAccountUpdated?.()
    } catch (error) {
      console.error('Failed to update account:', error)
      const errorMessage = error instanceof Error ? error.message : 'Failed to update account'
      setError(errorMessage)
      setTestResult(null)
      toast.error(`Failed to update account: ${errorMessage}`)
    } finally {
      setLoading(false)
      setTesting(false)
    }
  }

  const startEdit = (account: AIAccount) => {
    setSelectedRuntimeAgentId(null)
    setRuntimeDirty(false)
    setEditingId(account.id)
    setEditAccount({
      name: account.name,
      model: account.model || '',
      base_url: account.base_url || '',
      api_key: '',
      agent_type: account.agent_type || 'react',
      memory_enabled: account.memory_enabled || 'false',
      tool_routing_enabled: account.tool_routing_enabled || 'true',
      enable_rule_aware: account.enable_rule_aware || false,
    })
  }

  const cancelEdit = () => {
    setSelectedRuntimeAgentId(null)
    setRuntimeDirty(false)
    setEditingId(null)
    setEditAccount({
      name: '',
      model: '',
      base_url: '',
      api_key: '',
      agent_type: 'react',
      memory_enabled: 'false',
      tool_routing_enabled: 'true',
      enable_rule_aware: false,
    })
    setTestResult(null)
    setError(null)
  }

  const handleViewPrompt = async (account: AIAccount) => {
    if (viewingPromptAccountId === account.id) {
      setViewingPromptAccountId(null)
      return
    }

    setViewingPromptAccountId(account.id)
    setError(null)

    if (accountPrompts[account.id]) {
      return
    }

    try {
      setPromptLoadingAccountId(account.id)
      const promptData = await getAccountSystemPrompt(account.id)
      setAccountPrompts((prev) => ({ ...prev, [account.id]: promptData }))
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to load system prompt'
      toast.error(errorMessage)
      setViewingPromptAccountId(null)
    } finally {
      setPromptLoadingAccountId(null)
    }
  }

  const requestClose = (nextOpen: boolean) => {
    if (!nextOpen && (runtimeDirty || editingId !== null)) {
      const discard = window.confirm('Discard unsaved account changes?')
      if (!discard) return
      setRuntimeDirty(false)
      cancelEdit()
    }
    onOpenChange(nextOpen)
  }

  return (
    <Dialog open={open} onOpenChange={requestClose}>
      <DialogContent className="sm:max-w-3xl max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>Account Management</DialogTitle>
          <DialogDescription>
            Manage your trading accounts and AI configurations
          </DialogDescription>
        </DialogHeader>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded">
            {error}
          </div>
        )}

        <div className="space-y-6 overflow-y-auto pr-2">
          {/* Existing Accounts */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-medium">Trading Accounts</h3>
              <Button
                onClick={() => setShowAddForm(!showAddForm)}
                size="sm"
                className="flex items-center gap-2"
              >
                <Plus className="h-4 w-4" />
                Add Account
              </Button>
            </div>

            {loading && accounts.length === 0 ? (
              <div>Loading accounts...</div>
            ) : (
              <div className="space-y-3">
                {accounts.map((account) => (
                  <div key={account.id} className="border rounded-lg p-4">
                    {editingId === account.id ? (
                      <div className="space-y-3">
                        <Input
                          placeholder="Account name"
                          value={editAccount.name || ''}
                          onChange={(e) => setEditAccount({ ...editAccount, name: e.target.value })}
                        />
                        <RuntimeConfigPanel
                          ref={runtimeRef}
                          accountId={account.id}
                          onDirtyChange={setRuntimeDirty}
                          onAgentChange={setSelectedRuntimeAgentId}
                        />

                        <Input
                            placeholder="Model"
                            value={editAccount.model || ''}
                            onChange={(e) => setEditAccount({ ...editAccount, model: e.target.value })}
                          />
                        <Input
                          placeholder="Base URL"
                          value={editAccount.base_url || ''}
                          onChange={(e) => setEditAccount({ ...editAccount, base_url: e.target.value })}
                        />
                        <Input
                          placeholder="API Key (leave blank to keep current)"
                          type="password"
                          value={editAccount.api_key || ''}
                          onChange={(e) => setEditAccount({ ...editAccount, api_key: e.target.value })}
                        />
                        {testResult && (
                          <div className={`text-xs p-2 rounded ${
                            testResult.includes('❌') 
                              ? 'bg-red-50 text-red-700 border border-red-200' 
                              : 'bg-green-50 text-green-700 border border-green-200'
                          }`}>
                            {testResult}
                          </div>
                        )}
                        <div className="flex gap-2">
                          <Button onClick={handleUpdateAccount} disabled={loading || testing || !selectedRuntimeAgentId} size="sm">
                            {testing ? 'Saving...' : editAccount.api_key ? 'Test and Save' : 'Save'}
                          </Button>
                          <Button onClick={cancelEdit} variant="outline" size="sm" disabled={loading || testing}>
                            Cancel
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-center justify-between">
                        <div className="space-y-1 flex-1">
                          <div className="font-medium flex items-center gap-2 flex-wrap">
                            {account.name}
                            {isBaselineAccountName(account.name) ? (
                              <span className="text-[10px] font-normal uppercase tracking-wide px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                                Baseline
                              </span>
                            ) : null}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {account.model ? `Model: ${account.model}` : 'No model configured'} • {getAgentTypeLabel(account.agent_type)}
                            {account.memory_enabled === 'true' && ' • 🧠 Memory'}
                          </div>
                          {account.base_url && (
                            <div className="text-xs text-muted-foreground truncate">
                              Base URL: {account.base_url}
                            </div>
                          )}
                          {account.api_key && (
                            <div className="text-xs text-muted-foreground">
                              API Key: Configured
                            </div>
                          )}
                          <div className="text-xs text-muted-foreground">
                            Cash: ${account.current_cash?.toLocaleString() || '0'}
                          </div>
                        </div>
                        <div className="flex gap-2">
                          <Button
                            aria-label={`View prompt for ${account.name}`}
                            onClick={() => handleViewPrompt(account)}
                            variant="outline"
                            size="sm"
                          >
                            <Eye className="h-4 w-4" />
                          </Button>
                          <Button
                            aria-label={`Edit ${account.name}`}
                            onClick={() => startEdit(account)}
                            variant="outline"
                            size="sm"
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                    )}
                    {viewingPromptAccountId === account.id && (
                      <div className="mt-3 rounded-md border bg-muted/30 p-3 space-y-2">
                        <div className="text-xs text-muted-foreground">
                          {promptLoadingAccountId === account.id
                            ? 'Loading system prompt...'
                            : accountPrompts[account.id]
                              ? `Agent: ${accountPrompts[account.id].agent_type} • Protocol: ${accountPrompts[account.id].decision_protocol} • End Token: ${accountPrompts[account.id].termination_token}`
                              : 'No prompt loaded'}
                        </div>
                        {accountPrompts[account.id]?.system_prompt && (
                          <pre className="max-h-80 overflow-auto rounded bg-background p-3 text-xs whitespace-pre-wrap">
                            {accountPrompts[account.id].system_prompt}
                          </pre>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Add New Account Form */}
          {showAddForm && (
            <div className="space-y-4 border-t pt-4">
              <h3 className="text-lg font-medium">Add New Account</h3>
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <Input
                    placeholder="Account name"
                    value={newAccount.name || ''}
                    onChange={(e) => setNewAccount({ ...newAccount, name: e.target.value })}
                  />
                  <Select
                    value={newAccount.agent_type || 'react'}
                    onValueChange={(value) => setNewAccount({ ...newAccount, agent_type: value })}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Agent Type" />
                    </SelectTrigger>
                    <SelectContent>
                      {AGENT_TYPE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {/* Rule-Aware Toggle */}
                <div className="flex items-center justify-between p-3 bg-muted/50 rounded-lg">
                  <div className="flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary">
                      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/>
                      <path d="m9 12 2 2 4-4"/>
                    </svg>
                    <div>
                      <p className="text-sm font-medium">Enable Rule-Aware Trading</p>
                      <p className="text-xs text-muted-foreground">Monitor compliance with trading rules (R0/R1/R2)</p>
                    </div>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={newAccount.enable_rule_aware || false}
                      onChange={(e) => setNewAccount({ ...newAccount, enable_rule_aware: e.target.checked })}
                    />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-primary/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                  </label>
                </div>

                <Input
                    placeholder="Model (e.g., gpt-4)"
                    value={newAccount.model || ''}
                    onChange={(e) => setNewAccount({ ...newAccount, model: e.target.value })}
                  />
                <Input
                  placeholder="Base URL (e.g., https://api.openai.com/v1)"
                  value={newAccount.base_url || ''}
                  onChange={(e) => setNewAccount({ ...newAccount, base_url: e.target.value })}
                />
                <Input
                  placeholder="API Key"
                  type="password"
                  value={newAccount.api_key || ''}
                  onChange={(e) => setNewAccount({ ...newAccount, api_key: e.target.value })}
                />
                <div className="flex items-center space-x-2">
                  <Switch
                    id="memory-enabled-new"
                    checked={newAccount.memory_enabled === 'true'}
                    onCheckedChange={(checked) => setNewAccount({ ...newAccount, memory_enabled: checked ? 'true' : 'false' })}
                  />
                  <Label htmlFor="memory-enabled-new">Enable Memory System</Label>
                </div>
                <div className="flex items-center space-x-2">
                  <Switch
                    id="tool-routing-enabled-new"
                    checked={newAccount.tool_routing_enabled === 'true'}
                    onCheckedChange={(checked) => setNewAccount({ ...newAccount, tool_routing_enabled: checked ? 'true' : 'false' })}
                  />
                  <Label htmlFor="tool-routing-enabled-new">Enable Tool Routing</Label>
                </div>
                <div className="flex gap-2">
                  <Button onClick={handleCreateAccount} disabled={loading}>
                    Test and Create
                  </Button>
                  <Button 
                    onClick={() => setShowAddForm(false)} 
                    variant="outline"
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
