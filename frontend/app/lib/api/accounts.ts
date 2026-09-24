import { apiJson } from './client'
import type {
  AccountSystemPromptResponse,
  DecisionSchedule,
  TradingAccount,
  TradingAccountCreate,
  TradingAccountUpdate,
} from './generated-types'

export async function getAccounts(): Promise<TradingAccount[]> {
  return apiJson('/account/list')
}

export async function getDecisionSchedule(): Promise<DecisionSchedule> {
  return apiJson('/account/decision-schedule')
}

export async function getOverview(): Promise<unknown> {
  return apiJson('/account/overview')
}

export async function createAccount(account: TradingAccountCreate): Promise<TradingAccount> {
  return apiJson('/account/', {
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
      initial_capital: account.initial_capital || 10000,
    }),
  })
}

export async function updateAccount(accountId: number, account: TradingAccountUpdate): Promise<TradingAccount> {
  const body: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(account)) {
    if (value !== undefined) body[key] = value
  }
  return apiJson(`/account/${accountId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export async function testLLMConnection(testData: {
  model?: string | null
  base_url?: string | null
  api_key?: string | null
}): Promise<{ success: boolean; message: string; response?: unknown }> {
  return apiJson('/account/test-llm', {
    method: 'POST',
    body: JSON.stringify(testData),
  })
}

export async function getAccountSystemPrompt(accountId: number): Promise<AccountSystemPromptResponse> {
  return apiJson(`/account/${accountId}/system-prompt`)
}

export async function verifyAuthSession(sessionToken: string): Promise<{ valid?: boolean; user_id?: string | number }> {
  return apiJson('/account/auth/verify', {
    method: 'POST',
    body: JSON.stringify({ session_token: sessionToken }),
  })
}
