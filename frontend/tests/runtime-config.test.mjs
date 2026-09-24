import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import vm from 'node:vm'
import React from 'react'
import * as jsxRuntime from 'react/jsx-runtime'
import { act, create } from 'react-test-renderer'
import { transformWithEsbuild } from 'vite'

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../app')
async function load(relative, dependencies) {
  const filename = path.join(app, relative)
  const { code } = await transformWithEsbuild(await fs.readFile(filename, 'utf8'), filename, { loader: filename.endsWith('tsx') ? 'tsx' : 'ts', format: 'cjs' })
  const module = { exports: {} }
  vm.runInNewContext(code, { module, exports: module.exports, console, require(name) {
    if (name === 'react') return React
    if (name === 'react/jsx-runtime') return jsxRuntime
    if (name in dependencies) return dependencies[name]
    throw Error(`Unexpected dependency ${name}`)
  } })
  return module.exports
}
const client = await load('lib/api/client.ts', {})
const forms = await load('components/extensions/SchemaForm.tsx', {
  '@/components/ui/input': { Input: 'input' }, '@/components/ui/label': { Label: 'label' }, '@/components/ui/switch': { Switch: 'switch' },
})
const config = (limit = 2) => ({ agent_id: 'example.agent', agent_config: { limit }, disabled_tools: [], toolset_ids: [], component_versions: {} })
const response = (limit, token) => ({ config: config(limit), updated_at: token, validation_errors: [] })

async function runtimeHarness(api) {
  const { useAccountRuntimeConfig } = await load('hooks/useAccountRuntimeConfig.ts', {
    '@/lib/api/client': client,
    '@/lib/api/extensions': api,
  })
  let current
  function Host() { current = useAccountRuntimeConfig(1); return null }
  let view
  await act(async () => { view = create(React.createElement(Host)) })
  return { current: () => current, close: () => act(() => view.unmount()) }
}

test('409 preserves edits, blocks retry until resolution, then sends the new token', async () => {
  let gets = 0
  const writes = []
  const h = await runtimeHarness({
    getRuntimeConfig: async () => ++gets === 1 ? response(2, 'v1') : response(3, 'v2'),
    validateRuntimeConfig: async (_id, draft) => ({ valid: true, config: draft }),
    saveRuntimeConfig: async (_id, draft, token) => {
      writes.push({ draft, token })
      if (writes.length === 1) throw new client.ApiError('Conflict', 409, 'r')
      return { config: draft, updated_at: 'v3' }
    },
  })
  act(() => h.current().setDraft(config(7)))
  await act(async () => { await assert.rejects(h.current().save(), /Conflict/) })
  assert.equal(h.current().draft.agent_config.limit, 7)
  assert.equal(h.current().conflict.config.agent_config.limit, 3)
  await assert.rejects(h.current().save(), /Resolve/)
  assert.equal(writes.length, 1)
  act(() => h.current().resolveConflict(true))
  await act(async () => { await h.current().save() })
  assert.equal(writes[1].token, 'v2')
  assert.equal(writes[1].draft.agent_config.limit, 7)
  assert.equal(h.current().dirty, false)
  h.close()
})

test('choosing server version explicitly replaces the draft', async () => {
  let gets = 0
  const h = await runtimeHarness({ getRuntimeConfig: async () => ++gets === 1 ? response(2, 'v1') : response(4, 'v2'), validateRuntimeConfig: async (_id, draft) => ({valid:true,config:draft}), saveRuntimeConfig: async () => { throw new client.ApiError('Conflict', 409, 'r') } })
  act(() => h.current().setDraft(config(9)))
  await act(async () => { await assert.rejects(h.current().save()) })
  act(() => h.current().resolveConflict(false))
  assert.equal(h.current().draft.agent_config.limit, 4)
  assert.equal(h.current().dirty, false)
  h.close()
})

test('invalid JSON survives blur and all invalid fields must be fixed before saving', async () => {
  let saves = 0
  const draft = { ...config(), agent_config: { first: [], second: [] } }
  const runtime = { draft, dirty: false, loading: false, fieldErrors: [], save: async () => { saves++ }, setDraft(next) { this.draft = next } }
  const { RuntimeConfigPanel } = await load('components/extensions/RuntimeConfigPanel.tsx', {
    '@/lib/api/accounts': {},
    '@/hooks/useAccountRuntimeConfig': { useAccountRuntimeConfig: () => runtime },
    '@/hooks/useExtensionCatalog': { useExtensionCatalog: () => ({ agents: [{ id: 'example.agent', config_schema: {type:'object', properties:{first:{type:'array'},second:{type:'array'}}} }], tools:[], toolsets:[], prompts:[], extensions:[] }) },
    './SchemaForm': forms,
  })
  const ref = React.createRef()
  let dirty
  let view
  act(() => { view = create(React.createElement(RuntimeConfigPanel, { ref, accountId:1, onDirtyChange: value => {dirty=value}, onAgentChange() {} })) })
  const first = () => view.root.findByProps({ 'aria-label': 'first JSON' })
  const second = () => view.root.findByProps({ 'aria-label': 'second JSON' })
  act(() => { first().props.onChange({target:{value:'['}}); second().props.onChange({target:{value:'['}}) })
  assert.equal(dirty, true)
  act(() => first().props.onBlur?.())
  assert.equal(first().props.value, '[')
  await assert.rejects(ref.current.save(), /Correct the JSON/)
  act(() => first().props.onChange({target:{value:'[1]'}}))
  await assert.rejects(ref.current.save(), /Correct the JSON/)
  assert.equal(saves, 0)
  act(() => second().props.onChange({target:{value:'[2]'}}))
  await act(async () => ref.current.save())
  assert.equal(saves, 1)
  act(() => view.unmount())
})

test('changing an unavailable agent or prompt drops replaced pins and keeps tool pins', async () => {
  const draft = {...config(), agent_id:'retired.agent', prompt_profile_id:'retired.prompt', component_versions:{'retired.agent':'1.0.0','retired.prompt':'1.0.0','example.agent':'0.5.0','example.prompt':'0.5.0','example.tool':'1.0.0'}}
  const runtime = {draft,dirty:false,loading:false,fieldErrors:[],save:async()=>{},setDraft(next){this.draft=next}}
  const { RuntimeConfigPanel } = await load('components/extensions/RuntimeConfigPanel.tsx', {
    '@/lib/api/accounts': {}, '@/hooks/useAccountRuntimeConfig': {useAccountRuntimeConfig:()=>runtime},
    '@/hooks/useExtensionCatalog': {useExtensionCatalog:()=>({agents:[{id:'example.agent',config_schema:{type:'object',properties:{}}}],prompts:[{id:'example.prompt',version:'1.0.0'}],tools:[],toolsets:[],extensions:[]})}, './SchemaForm':forms,
  })
  let view
  const element = () => React.createElement(RuntimeConfigPanel,{accountId:1,onDirtyChange(){},onAgentChange(){}})
  act(()=>{view=create(element())})
  act(()=>view.root.findByProps({'aria-label':'Agent'}).props.onChange({target:{value:'example.agent'}}))
  assert.equal(runtime.draft.component_versions['retired.agent'],undefined)
  assert.equal(runtime.draft.component_versions['retired.prompt'],undefined)
  assert.equal(runtime.draft.component_versions['example.agent'],undefined)
  assert.equal(runtime.draft.component_versions['example.tool'],'1.0.0')
  act(()=>view.update(element()))
  act(()=>view.root.findByProps({'aria-label':'Prompt profile'}).props.onChange({target:{value:'example.prompt'}}))
  assert.equal(runtime.draft.component_versions['example.prompt'],undefined)
  assert.equal(runtime.draft.prompt_profile_version,'1.0.0')
  assert.equal(runtime.draft.component_versions['example.tool'],'1.0.0')
  act(()=>view.unmount())
})
