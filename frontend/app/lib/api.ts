// API configuration
const API_BASE_URL = process.env.NODE_ENV === 'production' 
  ? '/api' 
  : '/api'  // Use proxy, don't hardcode port

// Hardcoded user for paper trading (matches backend initialization)
const HARDCODED_USERNAME = 'default'

// Helper function for making API requests
export async function apiRequest(
  endpoint: string, 
  options: RequestInit = {}
): Promise<Response> {
  const url = `${API_BASE_URL}${endpoint}`
  
  const defaultOptions: RequestInit = {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  }
  
  const response = await fetch(url, defaultOptions)
  
  if (!response.ok) {
    // Try to extract error message from response body
    try {
      const errorData = await response.json()
      const errorMessage = errorData.detail || errorData.message || `HTTP error! status: ${response.status}`
      throw new Error(errorMessage)
    } catch (e) {
      // If parsing fails, throw generic error
      throw new Error(`HTTP error! status: ${response.status}`)
    }
  }
  
  const contentType = response.headers.get('content-type')
  if (!contentType || !contentType.includes('application/json')) {
    throw new Error('Response is not JSON')
  }
  
  return response
}

// Specific API functions
export async function checkRequiredConfigs() {
  const response = await apiRequest('/config/check-required')
  return response.json()
}

// Crypto-specific API functions
export async function getCryptoSymbols() {
  const response = await apiRequest('/crypto/symbols')
  return response.json()
}

export async function getCryptoPrice(symbol: string) {
  const response = await apiRequest(`/crypto/price/${symbol}`)
  return response.json()
}

export async function getCryptoMarketStatus(symbol: string) {
  const response = await apiRequest(`/crypto/status/${symbol}`)
  return response.json()
}

export async function getPopularCryptos() {
  const response = await apiRequest('/crypto/popular')
  return response.json()
}

// Evaluation (periodic checkpoints) API
export interface EvalLeaderboardItem {
  account_id: number
  agent_name?: string | null
  agent_type?: string | null
  equity_start?: number | null
  equity_end?: number | null
  pnl?: number | null
  return_rate: number
  volatility?: number | null
}

export interface EvalLeaderboardResponse {
  interval_seconds: number
  period_end: string | null
  order_by: 'return' | 'pnl' | 'volatility'
  items: EvalLeaderboardItem[]
}

export interface EvalAccountCheckpointItem {
  period_start: string
  period_end: string
  equity_start?: number | null
  equity_end?: number | null
  pnl?: number | null
  return_rate: number
  volatility?: number | null
  created_at?: string
}

export interface EvalAccountCheckpointsResponse {
  account_id: number
  account_name: string
  interval_seconds: number
  items: EvalAccountCheckpointItem[]
}

export async function getEvalLeaderboard(
  intervalSeconds: number = 3600,
  orderBy: 'return' | 'pnl' | 'volatility' = 'pnl'
): Promise<EvalLeaderboardResponse> {
  const response = await apiRequest(
    `/evaluation/checkpoints/leaderboard?interval_seconds=${intervalSeconds}&order_by=${orderBy}`
  )
  return response.json()
}

export async function getEvalAccountCheckpoints(
  accountId: number,
  intervalSeconds: number = 3600,
  limit: number = 10
): Promise<EvalAccountCheckpointsResponse> {
  const response = await apiRequest(
    `/evaluation/checkpoints/account/${accountId}?interval_seconds=${intervalSeconds}&limit=${limit}`
  )
  return response.json()
}

// Trading Account management functions
export interface TradingAccount {
  id: number
  user_id: number
  name: string  // Display name (e.g., "GPT Trader", "Claude Analyst")
  model?: string  // AI model (e.g., "gpt-4-turbo")
  base_url?: string  // API endpoint
  api_key?: string  // API key (masked in responses)
  agent_type?: string // "react" | "multi_agent" | "advanced_multi_agent" | "buy_hold" | "grid"
  memory_enabled?: string // "true" or "false"
  tool_routing_enabled?: string // "true" or "false"
  enable_rule_aware?: boolean  // Enable Rule-Aware Trading
  initial_capital: number
  current_cash: number
  frozen_cash: number
  account_type: string  // "AI" or "MANUAL"
  is_active: boolean
}

export interface TradingAccountCreate {
  name: string
  model?: string
  base_url?: string
  api_key?: string
  agent_type?: string
  memory_enabled?: string
  tool_routing_enabled?: string
  enable_rule_aware?: boolean
  initial_capital?: number
  account_type?: string
}

export interface TradingAccountUpdate {
  name?: string
  model?: string
  base_url?: string
  api_key?: string
  agent_type?: string
  memory_enabled?: string
  tool_routing_enabled?: string
  enable_rule_aware?: boolean
}

export interface AccountSystemPromptResponse {
  account_id: number
  account_name: string
  agent_type: string
  agent_id?: string
  memory_enabled: boolean
  tool_routing_enabled?: boolean
  decision_protocol: string
  termination_token: string
  system_prompt: string
  prompt_profile_id?: string | null
  prompt_profile_version?: string | null
  prompt_id?: string | null
  prompt_version?: string | null
  prompt_hash?: string
}

// Account functions for paper trading with hardcoded user
// Note: Backend initializes default user on startup, frontend just queries the endpoints
export async function getAccounts(): Promise<TradingAccount[]> {
  const response = await apiRequest('/account/list')
  return response.json()
}

export interface DecisionSchedule {
  job_id: string
  interval_seconds: string
  first_execution_time: string
  next_decision_time_utc: string
  next_decision_time_utc8: string
}

export async function getDecisionSchedule(): Promise<DecisionSchedule> {
  const response = await apiRequest('/account/decision-schedule')
  return response.json()
}

export async function getOverview(): Promise<any> {
  const response = await apiRequest('/account/overview')
  return response.json()
}

export async function createAccount(account: TradingAccountCreate): Promise<TradingAccount> {
  const response = await apiRequest('/account/', {
    method: 'POST',
    body: JSON.stringify({
      name: account.name,
      model: account.model,
      base_url: account.base_url,
      api_key: account.api_key,
      account_type: account.account_type || 'AI',
      agent_type: account.agent_type || 'react',
      memory_enabled: account.memory_enabled || 'false',
      tool_routing_enabled: account.tool_routing_enabled || 'true',
      enable_rule_aware: account.enable_rule_aware || false,
      initial_capital: account.initial_capital || 10000
    })
  })
  return response.json()
}

export async function updateAccount(accountId: number, account: TradingAccountUpdate): Promise<TradingAccount> {
  const response = await apiRequest(`/account/${accountId}`, {
    method: 'PUT',
    body: JSON.stringify({
      name: account.name,
      model: account.model,
      base_url: account.base_url,
      api_key: account.api_key,
      agent_type: account.agent_type,
      memory_enabled: account.memory_enabled,
      tool_routing_enabled: account.tool_routing_enabled,
      enable_rule_aware: account.enable_rule_aware
    })
  })
  return response.json()
}

export async function testLLMConnection(testData: {
  model?: string;
  base_url?: string;
  api_key?: string;
}): Promise<{ success: boolean; message: string; response?: any }> {
  const response = await apiRequest('/account/test-llm', {
    method: 'POST',
    body: JSON.stringify(testData)
  })
  return response.json()
}

export async function getAccountSystemPrompt(accountId: number): Promise<AccountSystemPromptResponse> {
  const response = await apiRequest(`/account/${accountId}/system-prompt`)
  return response.json()
}

// Legacy aliases for backward compatibility
export type AIAccount = TradingAccount
export type AIAccountCreate = TradingAccountCreate

// Updated legacy functions to use default mode for simulation
export const listAIAccounts = () => getAccounts()
export const createAIAccount = (account: any) => {
  console.warn("createAIAccount is deprecated. Use default mode or new trading account APIs.")
  return Promise.resolve({} as TradingAccount)
}
export const updateAIAccount = (id: number, account: any) => {
  console.warn("updateAIAccount is deprecated. Use default mode or new trading account APIs.")
  return Promise.resolve({} as TradingAccount)
}
export const deleteAIAccount = (id: number) => {
  console.warn("deleteAIAccount is deprecated. Use default mode or new trading account APIs.")
  return Promise.resolve()
}

// Agent Trace API
export interface AgentStep {
  step_number: number
  role: string
  content: string | null
  tool_calls: any | null
  tool_output: any | null
  created_at: string
}

export interface AgentTrace {
  trace_id: string
  steps: AgentStep[]
}

export async function getLatestTraceId(accountId: number): Promise<{ trace_id: string | null }> {
  const response = await apiRequest(`/agent/latest/${accountId}`)
  return response.json()
}

export async function getAgentTrace(traceId: string): Promise<AgentTrace> {
  const response = await apiRequest(`/agent/trace/${traceId}`)
  return response.json()
}

export interface TraceSummary {
  trace_id: string
  timestamp: string
  operation: string
  symbol: string | null
  reason: string
}

export async function getTraceHistory(accountId: number): Promise<TraceSummary[]> {
  const response = await apiRequest(`/agent/history/${accountId}`)
  return response.json()
}

// Memory API
export async function getMemories(accountId: number, market?: string) {
  const params = market ? `?market=${market}` : ''
  const response = await apiRequest(`/memory/${accountId}/list${params}`)
  return response.json()
}

export async function getMemoryMetrics(accountId: number, market?: string) {
  const params = market ? `?market=${market}` : ''
  const response = await apiRequest(`/memory/${accountId}/metrics${params}`)
  return response.json()
}

export async function getMemoryGrowthTimeline(accountId: number, market?: string) {
  const params = market ? `?market=${market}` : ''
  const response = await apiRequest(`/memory/${accountId}/growth-timeline${params}`)
  return response.json()
}
