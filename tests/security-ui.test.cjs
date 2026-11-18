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

test('confirmation counts explicitly disclose Electrum trust', () => {
  const c = component('static/ledger.js')
  const columns = c.computed.columns.call({sats: true, $q: {screen: {lt: {sm: false}}}, tab: 'Transactions'})
  assert.match(columns.find(c => c[0] === 'confirmations')[1], /server/)
  assert.match(readFileSync('templates/arguswallet/ledger.html', 'utf8'), /block inclusion is not independently verified/)
})

test('failed account import disconnects and propagates rejection', async () => {
  const c = component('static/signer.js')
  let disconnected = false
  const ctx = {verifyAccount: async () => {throw new Error('mismatch')},
    disconnect() {disconnected = true; this.connected = false}, connected: true}
  await assert.rejects(c.methods.importAccount.call(ctx), /mismatch/)
  assert.equal(disconnected, true)
  assert.equal(ctx.connected, false)
})
