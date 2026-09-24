import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import vm from 'node:vm'
import { transformWithEsbuild } from 'vite'

const filename = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../app/components/extensions/SchemaForm.tsx')
const source = await readFile(filename, 'utf8')
const { code } = await transformWithEsbuild(source, filename, { loader: 'tsx', format: 'cjs' })
const module = { exports: {} }
vm.runInNewContext(code, {
  module,
  exports: module.exports,
  require() { return {} },
})

test('enum selection keeps the schema value type', () => {
  const { matchEnumValue } = module.exports
  assert.equal(matchEnumValue([1, 2], '2'), 2)
  assert.equal(typeof matchEnumValue([1, 2], '2'), 'number')
  assert.equal(matchEnumValue([false, true], 'true'), true)
  assert.equal(matchEnumValue(['long', 'short'], 'short'), 'short')
})
