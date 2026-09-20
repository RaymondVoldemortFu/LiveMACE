import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'
import { transformWithEsbuild } from 'vite'

const filename = new URL('../app/main.tsx', import.meta.url)
const source = await readFile(filename, 'utf8')
const { code } = await transformWithEsbuild(`${source}\nexport { App };`, filename.pathname, { loader: 'tsx', format: 'cjs', jsx: 'transform' })

// Run the production effect with deterministic hooks and browser events. No
// network, browser state, production API or additional test dependency is used.
function harness() {
  const states = [], refs = [], effects = [], sockets = [], timers = new Map()
  let stateIndex = 0, refIndex = 0, timerId = 0
  const React = {
    StrictMode: 'StrictMode',
    createElement: (type, props, ...children) => ({ type, props, children }),
    useState: initial => {
      const index = stateIndex++
      states[index] = initial
      return [initial, value => { states[index] = typeof value === 'function' ? value(states[index]) : value }]
    },
    useRef: initial => { const ref = { current: initial }; refs[refIndex++] = ref; return ref },
    useEffect: effect => effects.push(effect),
  }
  class Socket {
    static OPEN = 1
    handlers = new Map()
    sent = []
    readyState = 0
    constructor(url) { this.url = url; sockets.push(this) }
    addEventListener(type, handler) { this.handlers.set(type, handler) }
    removeEventListener(type, handler) { if (this.handlers.get(type) === handler) this.handlers.delete(type) }
    send(value) { this.sent.push(JSON.parse(value)) }
    close(code = 1000, reason = '') { this.readyState = 3; this.emit('close', { code, reason }) }
    emit(type, event = {}) { this.handlers.get(type)?.(event) }
    open() { this.readyState = 1; this.emit('open') }
    message(value) { this.emit('message', { data: JSON.stringify(value) }) }
  }
  const module = { exports: {} }
  const toast = Object.assign(() => {}, { success() {}, error() {} })
  vm.runInNewContext(code, {
    module, exports: module.exports,
    require(name) {
      if (name === 'react') return React
      if (name === 'react-dom/client') return { createRoot: () => ({ render() {} }) }
      if (name === 'react-hot-toast') return { toast, Toaster: () => null }
      if (name === '@/lib/api') return { getAccounts: async () => [] }
      return {}
    },
    WebSocket: Socket,
    window: { location: { protocol: 'http:', host: 'localhost' } },
    document: { getElementById: () => ({}) },
    console: { log() {}, warn() {}, error() {} },
    setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id },
    clearTimeout(id) { timers.delete(id) },
  })
  module.exports.App()
  return {
    states, refs, sockets, timers, mount: effects[0],
    tick() { const pending = [...timers.values()]; timers.clear(); pending.forEach(callback => callback()) },
  }
}

const user = { id: 1, username: 'default' }
const account = id => ({ id, name: `Account ${id}`, user_id: 1 })
const snapshot = (selected, marker = selected.id) => ({ type: 'snapshot_full', overview: { account: selected }, positions: [], orders: [], trades: [], ai_decisions: [{ id: marker }] })
const bootstrap = socket => { socket.open(); socket.message({ type: 'bootstrap_ok', user, account: account(1) }); socket.message(snapshot(account(1))) }

test('StrictMode cleanup detaches the first socket and leaves one live subscription', () => {
  const app = harness()
  const firstCleanup = app.mount()
  const first = app.sockets[0]
  firstCleanup()
  const finalCleanup = app.mount()
  first.open()
  assert.equal(first.sent.length, 0)
  assert.equal(first.handlers.size, 0)
  assert.equal(app.timers.size, 0)
  bootstrap(app.sockets[1])
  assert.equal(app.states[2].account.id, 1)
  finalCleanup()
  assert.equal(app.sockets[1].handlers.size, 0)
})

for (const closeCode of [1000, 1001, 1006]) {
  test(`server close ${closeCode} reconnects and restores selection while rejecting default snapshots`, () => {
    const app = harness()
    const cleanup = app.mount()
    const first = app.sockets[0]
    bootstrap(first)
    first.message({ type: 'account_switched', account: account(4) })
    assert.equal(app.states[2], null)
    first.message(snapshot(account(4), 44))
    first.close(closeCode)
    assert.equal(app.states[2], null)
    assert.equal(app.timers.size, 1)
    app.tick()
    const replacement = app.sockets[1]
    replacement.open()
    replacement.message({ type: 'bootstrap_ok', user, account: account(1) })
    assert.equal(replacement.sent.at(-1).account_id, 4)
    replacement.message(snapshot(account(1), 11))
    assert.equal(app.states[2], null)
    replacement.message({ type: 'account_switched', account: account(4) })
    replacement.message(snapshot(account(4), 45))
    first.message(snapshot(account(1), 99))
    assert.equal(app.states[2].account.id, 4)
    assert.equal(app.states[6][0].id, 45)
    cleanup()
  })
}

test('cleanup cancels a pending reconnect and stale messages cannot update data', () => {
  const app = harness()
  const cleanup = app.mount()
  bootstrap(app.sockets[0])
  app.sockets[0].close(1001)
  cleanup()
  app.tick()
  app.sockets[0].message(snapshot(account(9)))
  assert.equal(app.sockets.length, 1)
  assert.equal(app.states[2], null)
})

test('switch_user accepts its new account and reconnects to that user', () => {
  const app = harness()
  const cleanup = app.mount()
  const socket = app.sockets[0]
  bootstrap(socket)
  socket.message({ type: 'user_switched', user: { id: 2, username: 'second' } })
  socket.message(snapshot(account(1)))
  assert.equal(app.states[2], null)
  socket.message(snapshot({ id: 8, user_id: 2, name: 'Second account' }))
  assert.equal(app.states[1].id, 8)
  assert.equal(app.states[2].account.id, 8)
  socket.close(1001)
  app.tick()
  app.sockets[1].open()
  assert.equal(app.sockets[1].sent[0].username, 'second')
  cleanup()
})
