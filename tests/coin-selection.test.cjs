const test = require("node:test");
const assert = require("node:assert/strict");
global.window = {};
test("coin selection, fee rounding and max with native SegWit inputs", async () => {
  await import("../static/coin-selection.js");
  const { select, id, fee } = window.ArgusCoins;
  const coins = [1000, 1500, 2800].map((value, vout) => ({
    txid: "x",
    vout,
    value,
  }));
  const r = [{ address: "bc1q" + "a".repeat(38), amount: 1950 }];
  assert.equal(fee(1, r, 2), 220);
  assert.equal(fee(1, r, 2, true), 282);
  const run = (mode, manual = []) =>
    select(coins, mode, manual, r, 2, [
      id(coins[1]),
      id(coins[2]),
      id(coins[0]),
    ]).map((c) => c.vout);
  assert.deepEqual(run("Larger Inputs First"), [2]);
  assert.deepEqual(run("Smaller Inputs First"), [0, 1]);
  assert.deepEqual(run("Manual", ["x:1"]), [1]);
  assert.deepEqual(run("Random"), [1, 2]);
  assert.deepEqual(run("Select All"), [0, 1, 2]);
  r[0].amount = 5300 - fee(3, r, 2);
  assert.deepEqual(run("Larger Inputs First"), [2, 1, 0]);
});
