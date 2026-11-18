window.app.component("argus-ledger", {
  template: "#argus-ledger",
  props: ["network", "snapshot", "notes", "labels", "sats", "locked"],
  emits: ["label", "details", "qr", "copy", "export", "spend"],
  data: () => ({
    tab: "Transactions",
    query: "",
    expanded: "",
    collapsed: {},
    picked: new Set(),
    sorts: {
      Transactions: { key: "date", direction: -1 },
      Addresses: { key: "index", direction: 1 },
      UTXOs: { key: "value", direction: -1 },
    },
  }),
  computed: {
    columns() {
      const unit = this.sats ? "sats" : "BTC";
      const value = ["value", "Value (" + unit + ")"];
      const amountFirst = this.$q.screen.lt.sm && this.tab !== "Addresses";
      const cols = {
        Transactions: [
          ["date", "Date"],
          ["label", "Label"],
          ["confirmations", "Confs"],
          value,
          ["balance", "Balance (" + unit + ")"],
        ],
        Addresses: [
          ["address", "Address / Outpoints"],
          ["label", "Label"],
          value,
        ],
        UTXOs: [
          ["date", "Date"],
          ["outpoint", "Output"],
          ["address", "Address"],
          ["label", "Label"],
          value,
        ],
      }[this.tab];
      return amountFirst
        ? [cols[0], value, ...cols.slice(1).filter((c) => c !== value)]
        : cols;
    },
    ledger() {
      const s = this.snapshot;
      const transactions = [],
        utxos = [],
        addresses = [];
      const history = new Map(),
        byAddress = new Map();
      // A running balance belongs to the transaction regardless of the display order.
      let balance = (s.history || []).reduce((total, t) => total + t.amount, 0);
      const append = (address, leg) => {
        if (!byAddress.has(address)) byAddress.set(address, []);
        byAddress.get(address).push(leg);
      };
      const unspent = new Set(s.coins.map((c) => c.txid + ":" + c.vout));
      for (const [i, t] of (s.history || []).entries()) {
        const row = {
          ...t,
          kind: "tx",
          key: t.txid,
          label: this.labels[t.txid] || "",
          confirmations:
            t.height <= 0
              ? 0
              : Number.isInteger(s.tip_height)
                ? Math.max(0, s.tip_height - t.height + 1)
                : null,
          value: t.amount,
          balance,
          date: t.height > 0 ? t.time : Infinity,
          order: i,
        };
        transactions.push(row);
        history.set(t.txid, row);
        balance -= t.amount;
        for (const o of t.outputs || []) {
          append(o.address, {
            ...o,
            status: unspent.has(o.txid + ":" + o.vout) ? "Unspent" : "Spent",
          });
        }
      }
      for (const c of s.coins) {
        const key = c.txid + ":" + c.vout;
        const tx = history.get(c.txid);
        utxos.push({
          ...c,
          key,
          kind: "utxo",
          outpoint: key,
          date: c.height <= 0 ? Infinity : tx?.date,
          label: ArgusCoins.label(c, this.labels, this.notes),
        });
        if (!tx?.outputs) append(c.address, { ...c, status: "Unspent" });
      }
      for (const a of s.addresses || []) {
        addresses.push({
          ...a,
          key: a.address,
          kind: "address",
          label: this.notes[a.address] || "",
          value: a.transactions ? a.balance : null,
          outputs: byAddress.get(a.address) || [],
        });
      }
      return { Transactions: transactions, Addresses: addresses, UTXOs: utxos };
    },
    pickedAmount() {
      return this.ledger.UTXOs.reduce(
        (sum, coin) => sum + (this.picked.has(coin.key) ? coin.value : 0), 0,
      );
    },
    groups() {
      const q = (this.query || "").trim().toLowerCase();
      const { key, direction } = this.sorts[this.tab];
      const rows = this.ledger[this.tab].filter(
        (r) =>
          !q ||
          [
            r.key,
            r.address,
            r.addresses?.join(" "),
            r.label,
            r.value,
            r.balance,
            this.dateText(r),
            ...(r.outputs || []).map((o) => o.txid + ":" + o.vout),
          ]
            .join(" ")
            .toLowerCase()
            .includes(q),
      );
      rows.sort((a, b) => {
        if (key === "outpoint" && a.txid === b.txid)
          return direction * (a.vout - b.vout);
        const x = a[key],
          y = b[key];
        if (x === y)
          return (a.order ?? a.index ?? 0) - (b.order ?? b.index ?? 0);
        if (x == null) return 1;
        if (y == null) return -1;
        return (
          direction *
          (typeof x === "string" ? x.localeCompare(y) : x < y ? -1 : 1)
        );
      });
      return this.tab === "Addresses"
        ? ["Receive Addresses", "Change Addresses"].map((name, i) => ({
            name,
            rows: rows.filter((r) => r.branch === i),
          }))
        : [{ name: this.tab, rows }];
    },
  },
  methods: {
    pick(key) {
      if (this.locked) return;
      if (this.picked.has(key)) this.picked.delete(key);
      else this.picked.add(key);
    },
    selectVisible() {
      for (const row of this.groups[0].rows) this.picked.add(row.key);
    },
    sort(key) {
      const s = this.sorts[this.tab];
      this.sorts[this.tab] = {
        key,
        direction:
          s.key === key
            ? -s.direction
            : ["date", "confirmations", "value", "balance"].includes(key)
              ? -1
              : 1,
      };
    },
    ariaSort(key) {
      const s = this.sorts[this.tab];
      return s.key !== key
        ? "none"
        : s.direction === 1
          ? "ascending"
          : "descending";
    },
    dateText(r) {
      if (r.date === Infinity) return "Unconfirmed";
      if (!Number.isFinite(r.date))
        return r.height > 0 ? "Block " + r.height : "";
      const d = new Date(r.date * 1000);
      const pad = (n) => String(n).padStart(2, "0");
      return (
        d.getFullYear() +
        "-" +
        pad(d.getMonth() + 1) +
        "-" +
        pad(d.getDate()) +
        " " +
        pad(d.getHours()) +
        ":" +
        pad(d.getMinutes())
      );
    },
    number(value) {
      return value == null
        ? ""
        : this.sats
          ? value.toLocaleString()
          : (value / 1e8).toFixed(8);
    },
    outpoint(o) {
      return o.txid.slice(0, 8) + "…:" + o.vout;
    },
    toggle(key) {
      this.expanded = this.expanded === key ? "" : key;
    },
    related(tab, address) {
      this.tab = tab;
      this.query = address;
      this.expanded = "";
    },
  },
  watch: {
    snapshot(snapshot) {
      const current = new Set(snapshot.coins.map(ArgusCoins.id));
      for (const key of this.picked) {
        if (!current.has(key)) this.picked.delete(key);
      }
    },
    tab() {
      this.expanded = "";
    },
  },
});
