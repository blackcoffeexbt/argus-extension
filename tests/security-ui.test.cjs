const {test} = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const {readFileSync} = require('node:fs')
function component(file, extra = {}) {
  let result
  vm.runInNewContext(readFileSync(file, 'utf8'), {window: {app: {component: (_, c) => result = c}}, ...extra})
  return result
}
test('legacy pairing migration removes login identifiers and supports clearing all credentials', () => {
  const storage = { 'arguswallet:pair:v1:login-secret:wallet': 'credential',
    'arguswallet:pair:v1:other-login:other-wallet': 'other', unrelated: 'keep' }
  Object.defineProperties(storage, {
    getItem: {value: k => storage[k]}, setItem: {value: (k,v) => storage[k] = v},
    removeItem: {value: k => delete storage[k]}
  })
  const c = component('static/signer.js', {localStorage: storage})
  const ctx = {userId: 'login-secret', walletId: 'wallet', disconnect() {}, refreshPairing() {}}
  ctx.storageKey = c.computed.storageKey.call(ctx)
  c.methods.migratePairings.call(ctx)
  assert.equal(storage[ctx.storageKey], 'credential')
  assert.ok(!Object.keys(storage).some(k => k.includes('login')))
  c.methods.forgetAll.call(ctx)
  assert.deepEqual(Object.keys(storage), ['unrelated'])
})
