import { useEffect, useState } from 'react'
import { listAgents, listExtensions, listPrompts, listTools, listToolsets } from '@/lib/api/extensions'
import type { CatalogComponent, CatalogTool, ToolsetOut, ExtensionRecord, PromptProfile } from '@/lib/api/generated-types'

export function useExtensionCatalog(enabled: boolean) {
  const [extensions, setExtensions] = useState<ExtensionRecord[]>([])
  const [agents, setAgents] = useState<CatalogComponent[]>([])
  const [tools, setTools] = useState<CatalogTool[]>([])
  const [toolsets, setToolsets] = useState<ToolsetOut[]>([])
  const [prompts, setPrompts] = useState<PromptProfile[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    setLoading(true)
    Promise.all([listExtensions(), listAgents(), listTools(), listToolsets(), listPrompts()])
      .then(([extensionRows, agentRows, toolRows, toolsetRows, promptRows]) => {
        if (cancelled) return
        setExtensions(extensionRows)
        setAgents(agentRows)
        setTools(toolRows)
        setToolsets(toolsetRows)
        setPrompts(promptRows)
        setError(null)
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'Failed to load extension catalog')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [enabled])

  return { extensions, agents, tools, toolsets, prompts, loading, error }
}
