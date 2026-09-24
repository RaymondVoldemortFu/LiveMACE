import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { getAccountSystemPrompt } from '@/lib/api/accounts'
import type { AccountSystemPromptResponse } from '@/lib/api/generated-types'
import { useAccountRuntimeConfig } from '@/hooks/useAccountRuntimeConfig'
import { useExtensionCatalog } from '@/hooks/useExtensionCatalog'
import { SchemaForm, defaultsForSchema } from './SchemaForm'

export function replaceComponentPins(versions: Record<string, string>, ...componentIds: (string | null | undefined)[]) {
  const next = { ...versions }
  for (const id of componentIds) if (id) delete next[id]
  return next
}

const STATUS_TEXT: Record<string, string> = {
  loaded: 'Ready to use',
  disabled: 'This extension is disabled and cannot be selected.',
  load_failed: 'This extension failed to load. Check the extension directory and restart the catalog.',
  incompatible: 'This extension is incompatible with the current runtime.',
  invalid: 'This extension failed validation and cannot be selected.',
}

export type RuntimeConfigHandle = {
  save: () => Promise<void>
  dirty: boolean
  agentId: string | null
}

export const RuntimeConfigPanel = forwardRef<RuntimeConfigHandle, {
  accountId: number
  onDirtyChange: (dirty: boolean) => void
  onAgentChange: (agentId: string | null) => void
}>(function RuntimeConfigPanel({ accountId, onDirtyChange, onAgentChange }, ref) {
  const catalog = useExtensionCatalog(true)
  const runtime = useAccountRuntimeConfig(accountId)
  const [preview, setPreview] = useState<AccountSystemPromptResponse | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [schemaErrors, setSchemaErrors] = useState<Record<string, string>>({})
  const schemaErrorsRef = useRef<Record<string, string>>({})
  const [editorRevision, setEditorRevision] = useState(0)
  const clearSchemaErrors = () => {
    schemaErrorsRef.current = {}
    setSchemaErrors({})
    setEditorRevision((value) => value + 1)
  }
  const dirty = runtime.dirty || Object.keys(schemaErrors).length > 0
  const dirtyCallback = useRef(onDirtyChange)
  const agentCallback = useRef(onAgentChange)
  dirtyCallback.current = onDirtyChange
  agentCallback.current = onAgentChange

  useEffect(() => {
    dirtyCallback.current(dirty)
  }, [dirty])

  useEffect(() => {
    agentCallback.current(runtime.draft?.agent_id ?? null)
  }, [runtime.draft?.agent_id])

  useImperativeHandle(ref, () => ({
    save: async () => {
      if (Object.keys(schemaErrorsRef.current).length) throw new Error('Correct the JSON parameters before saving')
      await runtime.save()
    },
    dirty,
    agentId: runtime.draft?.agent_id ?? null,
  }), [runtime.save, dirty, runtime.draft?.agent_id])

  const draft = runtime.draft
  const selectedAgent = catalog.agents.find((agent) => agent.id === draft?.agent_id)
  const blocked = catalog.extensions.filter((item) => item.status !== 'loaded')

  const showPreview = async () => {
    setPreviewError(null)
    try {
      setPreview(await getAccountSystemPrompt(accountId))
    } catch (cause) {
      setPreview(null)
      setPreviewError(cause instanceof Error ? cause.message : 'Failed to load prompt preview')
    }
  }

  if (catalog.loading || runtime.loading) return <div className="text-sm text-muted-foreground">Loading runtime components...</div>
  if (!draft) return <div className="text-sm text-red-700">{runtime.error || catalog.error || 'Runtime config is unavailable'}</div>

  return (
    <div className="space-y-3 rounded-lg border p-3">
      <div>
        <h4 className="text-sm font-medium">Runtime components</h4>
        <p className="text-xs text-muted-foreground">Choose the agent, tools, and prompt used on the next decision round.</p>
      </div>
      {(catalog.error || runtime.error) && (
        <div className="text-xs text-red-700">{catalog.error || runtime.error}</div>
      )}
      {runtime.conflict && (
        <div role="alert" className="space-y-2 rounded border p-2 text-sm">
          <p>This configuration changed on the server. Your edits are preserved. Choose which version to continue with, then save.</p>
          <button type="button" className="mr-3 underline" onClick={() => runtime.resolveConflict(true)}>Keep my edits</button>
          <button type="button" className="underline" onClick={() => { runtime.resolveConflict(false); clearSchemaErrors() }}>Use server version</button>
        </div>
      )}
      {blocked.map((item) => (
        <div key={`${item.id}-${item.status}`} className="text-xs rounded border border-amber-300 bg-amber-50 p-2 text-amber-900">
          <strong>{item.name || item.id || 'Extension'}</strong> ({item.status}): {STATUS_TEXT[item.status] || item.status}
          {item.errors[0]?.message ? ` ${item.errors[0].message}` : ''}
        </div>
      ))}
      <label className="block space-y-1 text-sm">
        <span>Agent</span>
        <select
          aria-label="Agent"
          className="w-full rounded-md border bg-background p-2"
          value={draft.agent_id}
          onChange={(event) => {
            const agent = catalog.agents.find((item) => item.id === event.target.value)
            clearSchemaErrors()
            runtime.setDraft({
              ...draft,
              component_versions: replaceComponentPins(draft.component_versions, draft.agent_id, event.target.value, draft.prompt_profile_id),
              agent_id: event.target.value,
              agent_version: null,
              agent_config: defaultsForSchema((agent?.config_schema || {}) as { properties?: Record<string, { default?: unknown }> }),
              prompt_profile_id: null,
              prompt_profile_version: null,
            })
          }}
        >
          {catalog.agents.map((agent) => (
            <option key={agent.id} value={agent.id} disabled={agent.status !== 'loaded' && agent.status !== undefined && agent.status !== ''}>
              {agent.name} {agent.version} · {agent.status || 'loaded'}
            </option>
          ))}
        </select>
        {selectedAgent?.description && <span className="block text-xs text-muted-foreground">{selectedAgent.description}</span>}
      </label>
      <div className="space-y-2">
        <div className="text-sm">Tools</div>
        {catalog.toolsets.map((group) => (
          <label key={group.id} className="flex items-center gap-2 text-sm">
            <input type="checkbox" aria-label={`${group.name} tools`}
              checked={(draft.toolset_ids.length ? draft.toolset_ids : ['core.default-tools']).includes(group.id)}
              onChange={(event) => {
                const ids = new Set(draft.toolset_ids)
                if (event.target.checked) ids.add(group.id)
                else ids.delete(group.id)
                runtime.setDraft({ ...draft, toolset_ids: [...ids] })
              }} />
            <span>{group.name} · {group.tool_names.length} tools</span>
          </label>
        ))}
        <p className="text-xs text-muted-foreground">Select named toolsets; an empty selection uses all installed tools. Individual switches exclude tools from those sets.</p>
        {catalog.tools.map((tool) => {
          const included = !draft.toolset_ids.length || catalog.toolsets.some((group) => draft.toolset_ids.includes(group.id) && group.tool_names.includes(tool.name))
          const enabled = included && !draft.disabled_tools.includes(tool.name)
          return (
            <label key={tool.name} className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={enabled}
                disabled={!included}
                onChange={(event) => {
                  const disabled = new Set(draft.disabled_tools)
                  if (event.target.checked) disabled.delete(tool.name)
                  else disabled.add(tool.name)
                  runtime.setDraft({ ...draft, disabled_tools: [...disabled] })
                }}
              />
              <span>
                <span className="font-medium">{tool.name}</span>
                <span className="block text-xs text-muted-foreground">
                  {tool.side_effect} · {(tool.requested_capabilities || []).join(', ') || 'no extra capability'}
                </span>
                {tool.side_effect === 'trading_write' && (
                  <span className="block text-xs text-amber-700">This tool can place trades.</span>
                )}
              </span>
            </label>
          )
        })}
      </div>
      <label className="block space-y-1 text-sm">
        <span>Prompt profile</span>
        <select
          aria-label="Prompt profile"
          className="w-full rounded-md border bg-background p-2"
          value={draft.prompt_profile_id || ''}
          onChange={(event) => {
            const profile = catalog.prompts.find((item) => item.id === event.target.value)
            runtime.setDraft({
              ...draft,
              component_versions: replaceComponentPins(draft.component_versions, draft.prompt_profile_id, profile?.id),
              prompt_profile_id: profile?.id || null,
              prompt_profile_version: profile?.version || null,
            })
          }}
        >
          <option value="">Default for the selected agent</option>
          {catalog.prompts.map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.name} · {profile.version} · {profile.source}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className="text-xs underline" onClick={() => void showPreview()}>
        Preview saved prompt
      </button>
      {previewError && <div className="text-xs text-red-700">{previewError}</div>}
      {preview && (
        <div className="space-y-1 text-xs">
          <div>Profile {preview.prompt_profile_id || 'n/a'} · version {preview.prompt_profile_version || preview.prompt_version || 'n/a'}</div>
          <div className="break-all">Hash {preview.prompt_hash || 'n/a'}</div>
          <textarea readOnly aria-label="Prompt preview" className="w-full min-h-24 rounded-md border bg-muted p-2 font-mono" value={preview.system_prompt} />
        </div>
      )}
      <div className="space-y-1">
        <div className="text-sm">Agent parameters</div>
        {Object.entries(schemaErrors).map(([path, message]) => <div key={path} className="text-xs text-red-700">{path}: {message}</div>)}
        <SchemaForm
          key={`${accountId}-${draft.agent_id}-${editorRevision}`}
          schema={(selectedAgent?.config_schema || { type: 'object', properties: {} }) as { type?: string; properties?: Record<string, { type?: string | string[]; enum?: unknown[]; title?: string }> }}
          value={draft.agent_config}
          onInvalid={(message, path = 'agent_config') => {
            const next = { ...schemaErrorsRef.current }
            if (message) next[path] = message
            else delete next[path]
            schemaErrorsRef.current = next
            setSchemaErrors(next)
          }}
          onChange={(next) => {
            runtime.setDraft({ ...draft, agent_config: next })
          }}
        />
      </div>
      {runtime.fieldErrors.length > 0 && (
        <ul className="space-y-1 text-xs text-red-700">
          {runtime.fieldErrors.map((issue) => (
            <li key={`${issue.path}-${issue.message}`}>{issue.path || 'config'}: {issue.message}</li>
          ))}
        </ul>
      )}
    </div>
  )
})
