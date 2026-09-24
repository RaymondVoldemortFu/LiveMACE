import { apiJson } from './client'
import type {
  CatalogComponent,
  CatalogTool,
  ToolsetOut,
  ExtensionRecord,
  PromptProfile,
  RuntimeConfig,
  RuntimeConfigBody,
  RuntimeConfigValidation,
} from './generated-types'

export async function listExtensions(): Promise<ExtensionRecord[]> {
  return apiJson('/extensions')
}

export async function listAgents(): Promise<CatalogComponent[]> {
  return apiJson('/extensions/agents')
}

export async function listTools(): Promise<CatalogTool[]> {
  return apiJson('/extensions/tools')
}

export async function listToolsets(): Promise<ToolsetOut[]> {
  return apiJson('/extensions/toolsets')
}

export async function listPrompts(): Promise<PromptProfile[]> {
  return apiJson('/extensions/prompts')
}

export async function getRuntimeConfig(accountId: number): Promise<RuntimeConfig> {
  return apiJson(`/account/${accountId}/runtime-config`)
}

export async function validateRuntimeConfig(
  accountId: number,
  config: RuntimeConfigBody,
): Promise<RuntimeConfigValidation> {
  return apiJson(`/account/${accountId}/runtime-config/validate`, {
    method: 'POST',
    body: JSON.stringify({ config }),
  })
}

export async function saveRuntimeConfig(
  accountId: number,
  config: RuntimeConfigBody,
  expectedUpdatedAt: string | null,
): Promise<RuntimeConfig> {
  return apiJson(`/account/${accountId}/runtime-config`, {
    method: 'PUT',
    body: JSON.stringify({ config, expected_updated_at: expectedUpdatedAt }),
  })
}
