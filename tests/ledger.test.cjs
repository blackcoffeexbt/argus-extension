const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
let component;
vm.runInNewContext(fs.readFileSync("static/ledger.js", "utf8"), {
  window: {
    app: {
      component(_, definition) {
        component = definition;
      },
    },
  },
});
test("running balances and confirmation counts stay attached when filtered or sorted", () => {
  const state = {
    ...component.data(),
    ...component.methods,
    labels: { second: "Lunch" },
    notes: {},
    snapshot: {
      tip_height: 100,
      coins: [],
      addresses: [],
      history: [
        { txid: "second", height: 0, time: null, amount: -120, addresses: [] },
        { txid: "first", height: 98, time: 100, amount: 1000, addresses: [] },
      ],
    },
  };
  state.ledger = component.computed.ledger.call(state);
  const rows = () => component.computed.groups.call(state)[0].rows;
  assert.equal(rows()[0].balance, 880);
  assert.equal(rows()[0].confirmations, 0);
  assert.equal(rows()[1].confirmations, 3);
  state.sort("value");
  assert.equal(rows()[0].key, "first");
  assert.equal(rows()[0].balance, 1000);
  state.query = "Lunch";
  assert.equal(rows().length, 1);
  assert.equal(rows()[0].balance, 880);
});
