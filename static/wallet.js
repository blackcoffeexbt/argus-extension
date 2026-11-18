function argusRequest(method, path, data, options = {}) {
  const usr = new URLSearchParams(location.search).get("usr");
  return LNbits.api.request(
    method,
    "/arguswallet/api/v1/wallets" + path,
    undefined,
    data,
    { ...options, params: usr ? { usr } : {} },
  );
}

function argusError(error) {
  const detail = error.response?.data?.detail;
  Quasar.Notify.create({
    type: "warning",
    message: typeof detail === "string" ? detail : error.message,
  });
}

window.app = Vue.createApp({
  mixins: [windowMixin],
  data: () => ({
    wallets: [],
    selected: null,
    creating: false,
    loaded: false,
    name: "",
    network: "Mainnet",
    working: false,
  }),
  async created() {
    try {
      this.wallets = (await argusRequest("GET", "")).data;
      this.selected = this.wallets[0] || null;
      this.loaded = true;
    } catch (error) {
      argusError(error);
    }
  },
  methods: {
    openCreate() {
      this.name = "";
      this.network = this.selected?.network || "Mainnet";
      this.creating = true;
    },
    removedWallet() {
      this.wallets = this.wallets.filter((w) => w.id !== this.selected.id);
      this.selected = this.wallets[0] || null;
      this.working = false;
    },
    async createWallet() {
      if (this.working) return;
      this.working = true;
      try {
        const { data } = await argusRequest("POST", "", {
          name: this.name,
          network: this.network,
        });
        this.wallets.push(data);
        this.selected = data;
        this.creating = false;
        this.name = "";
      } catch (error) {
        argusError(error);
      } finally {
        this.working = false;
      }
    },
  },
});

window.app.component("argus-explorer-link", {
  props: ["network", "txid", "address"],
  computed: {
    href() {
      const prefix = {
        Mainnet: "http://mempool.space/",
        Testnet4: "http://mempool.space/testnet4/",
      }[this.network];
      if (!prefix || !(this.txid || this.address)) return null;
      return (
        prefix +
        (this.txid ? "tx/" : "address/") +
        encodeURIComponent(this.txid || this.address)
      );
    },
    description() {
      return (
        "Open " +
        (this.txid ? "transaction" : "address") +
        " on mempool.space (opens in a new tab)"
      );
    },
  },
  template: `<a v-if="href" class="argus-explorer-link" :href="href" target="_blank" rel="noopener noreferrer" :title="description" :aria-label="description" @click.stop><q-icon name="open_in_new" size="14px"></q-icon></a>`,
});

window.app.component("argus-wallet-panel", {
  template: "#argus-wallet-panel",
  props: ["wallet", "userId"],
  emits: ["working", "removed"],
  data: () => ({
    configured: false,
    detailsOpen: false,
    initialized: false,
    descriptor: "",
    showReceive: false,
    showSend: false,
    receiveAmount: null,
    sats: true,
    notes: {},
    labels: {},
    gap: 20,
    savedGap: 20,
    editReference: "",
    qrAddress: "",
    editingAddress: "",
    note: "",
    mode: "Larger Inputs First",
    modes: [
      "Manual",
      "Random",
      "Select All",
      "Smaller Inputs First",
      "Larger Inputs First",
    ],
    randomOrder: [],
    estimates: {},
    feeLoading: false,
    exported: "",
    qrIndex: 0,
    qrTimer: null,
    drafts: [],
    draftPicker: false,
    txDetail: null,
    imported: "",
    confirmRemove: false,
    snapshot: null,
    receive: null,
    syncing: false,
    syncStatus: "",
    busy: false,
    coins: [],
    recipients: [{ address: "", amount: null }],
    rate: 1,
    draft: null,
    review: null,
    reviewOpen: false,
    sent: "",
  }),
  computed: {
    psbtFrame() {
      const parts = this.exported.match(/.{1,200}/g) || [];
      if (parts.length < 2) return this.exported;
      const i = this.qrIndex % parts.length;
      return "p" + (i + 1) + "of" + parts.length + " " + parts[i];
    },
    paymentUri() {
      const address = this.snapshot?.receive || this.receive || "";
      const sats = this.receiveAmount;
      return (
        "bitcoin:" +
        address +
        (Number.isSafeInteger(sats) && sats > 0
          ? "?amount=" + (sats / 1e8).toFixed(8)
          : "")
      );
    },
    selectedCoins() {
      return ArgusCoins.select(
        this.snapshot?.coins || [],
        this.mode,
        this.coins,
        this.recipients,
        this.rate,
        this.randomOrder,
      );
    },
    selectedAmount() {
      return this.selectedCoins.reduce((a, c) => a + c.value, 0);
    },
    totalAmount() {
      return this.recipients.reduce((a, r) => a + (Number(r.amount) || 0), 0);
    },
    changeAmount() {
      const change =
        this.selectedAmount -
        this.totalAmount -
        ArgusCoins.fee(
          this.selectedCoins.length,
          this.recipients,
          this.rate,
          true,
        );
      return change >= 546 ? change : 0;
    },
    estimatedFee() {
      const needed = ArgusCoins.fee(
        this.selectedCoins.length,
        this.recipients,
        this.rate,
      );
      return Math.max(
        needed,
        this.selectedAmount - this.totalAmount - this.changeAmount,
      );
    },
    locked() {
      return this.busy || this.syncing || this.reviewOpen;
    },
    coinOptions() {
      return [...(this.snapshot?.coins || [])]
        .sort((a, b) => b.value - a.value || ArgusCoins.id(a).localeCompare(ArgusCoins.id(b)))
        .map((coin) => {
          const value = ArgusCoins.id(coin);
          const name = ArgusCoins.label(coin, this.labels, this.notes);
          const amount = coin.value.toLocaleString() + " sats";
          return { value, label: [amount, name, value].filter(Boolean).join(" · ") };
        });
    },
    valid() {
      return (
        this.selectedCoins.length &&
        this.selectedCoins.length <= 32 &&
        this.selectedAmount >= this.totalAmount + this.estimatedFee &&
        Number.isInteger(this.rate) &&
        this.rate > 0 &&
        this.recipients.every(
          (r) =>
            r.address &&
            !r.error &&
            Number.isSafeInteger(r.amount) &&
            r.amount >= 546,
        )
      );
    },
  },
  beforeUnmount() {
    clearInterval(this.qrTimer);
  },
  watch: {
    exported(value) {
      clearInterval(this.qrTimer);
      this.qrIndex = 0;
      if (value.length > 200)
        this.qrTimer = setInterval(() => this.qrIndex++, 250);
    },
    locked(value) {
      this.$emit("working", value);
    },
  },
  mounted() {
    this.refresh(false);
  },
  methods: {
    async openTransaction(txid) {
      try {
        this.txDetail = (
          await this.request("GET", "/transactions/" + txid)
        ).data;
      } catch (error) {
        argusError(error);
      }
    },
    async chooseDraft() {
      try {
        this.drafts = (await this.request("GET", "/drafts")).data;
        this.draftPicker = true;
      } catch (error) {
        argusError(error);
      }
    },
    display(amount) {
      return this.sats
        ? amount.toLocaleString() + " sats"
        : (amount / 1e8).toFixed(8) + " BTC";
    },
    async copy(value) {
      try {
        await Quasar.copyToClipboard(value);
        this.$q.notify("Copied");
      } catch (error) {
        argusError(error);
      }
    },
    exportHistory() {
      const rows = [
        ["Transaction ID", "Block height", "Net sats", "Addresses"],
        ...(this.snapshot.history || []).map((r) => [
          r.txid,
          r.height,
          r.amount,
          r.addresses.join(" "),
        ]),
      ];
      Quasar.exportFile(
        "argus-transactions.csv",
        rows.map((row) => row.join(",")).join("\n"),
        "text/csv",
      );
    },
    async saveGap() {
      if (
        this.locked ||
        !Number.isInteger(this.gap) ||
        this.gap < 1 ||
        this.gap > 200
      )
        return;
      this.busy = true;
      try {
        this.savedGap = (
          await this.request("PUT", "/gap-limit", { gap_limit: this.gap })
        ).data.gap_limit;
        await this.refresh(true);
      } catch (error) {
        argusError(error);
      } finally {
        this.busy = false;
      }
    },
    startLabel(row) {
      if (row.kind === "address") this.editingAddress = row.key;
      else this.editReference = row.key;
      this.note =
        row.kind === "address"
          ? this.notes[row.key] || ""
          : this.labels[row.key] || "";
    },
    async saveReference() {
      const key = this.editReference;
      try {
        await this.request("PUT", "/labels/" + encodeURIComponent(key), {
          label: this.note,
        });
        this.labels[key] = this.note.trim();
        this.editReference = "";
      } catch (error) {
        argusError(error);
      }
    },
    async editNote() {
      try {
        await this.request(
          "PUT",
          "/addresses/" + this.editingAddress + "/note",
          { note: this.note },
        );
        this.notes[this.editingAddress] = this.note;
        this.editingAddress = "";
      } catch (error) {
        argusError(error);
      }
    },
    async removeWallet() {
      try {
        await this.request("DELETE", "");
        this.$refs.signer.forget();
        this.$emit("removed");
      } catch (error) {
        argusError(error);
      }
    },
    parseRecipient(r, value) {
      try {
        Object.assign(r, ArgusCoins.parseBitcoinRequest(value), { error: "" });
      } catch (error) {
        r.address = value;
        r.error = error.message;
      }
    },
    scanRecipient(r) {
      this.g.scanner = (value) => this.parseRecipient(r, value);
    },
    sendMaximum(index) {
      if (this.mode !== "Manual") this.mode = "Select All";
      const others = this.recipients.reduce(
        (sum, r, i) => sum + (i === index ? 0 : Number(r.amount) || 0),
        0,
      );
      this.recipients[index].amount = Math.max(
        0,
        this.selectedAmount -
          others -
          ArgusCoins.fee(this.selectedCoins.length, this.recipients, this.rate),
      );
    },
    async loadFees() {
      this.feeLoading = true;
      try {
        this.estimates = (await this.request("GET", "/fees")).data;
      } catch (error) {
        argusError(error);
      } finally {
        this.feeLoading = false;
      }
    },
    spendUtxos(ids) {
      const available = new Set(this.snapshot.coins.map(ArgusCoins.id));
      if (this.locked || !ids.length || ids.length > 32 || !ids.every(id => available.has(id))) return;
      this.coins = [...ids];
      this.mode = "Manual";
      this.startPayment();
      this.$nextTick(() => this.$refs.sendHeading?.focus());
    },
    async startPayment() {
      this.showSend = true;
      if (!Object.keys(this.estimates).length) {
        await this.loadFees();
        if (this.estimates.Medium && this.rate === 1)
          this.rate = this.estimates.Medium;
      }
    },
    async reviewSigned(signed) {
      this.review = (
        await this.request("POST", "/payments/" + this.draft + "/review", {
          psbt: signed.trim(),
        })
      ).data;
      this.exported = "";
      this.imported = "";
      this.reviewOpen = true;
    },
    async importSigned() {
      this.busy = true;
      try {
        await this.reviewSigned(this.imported);
      } catch (error) {
        argusError(error);
      } finally {
        this.busy = false;
      }
    },
    request(method, path, data, options) {
      return argusRequest(method, "/" + this.wallet.id + path, data, options);
    },
    async syncWithProgress() {
      let offset = 0;
      let snapshot = null;
      let failure = "";
      const consume = (text) => {
        if (typeof text !== "string") return;
        let end;
        while ((end = text.indexOf("\n\n", offset)) !== -1) {
          const frame = text.slice(offset, end);
          offset = end + 2;
          const split = frame.indexOf("\ndata: ");
          if (split < 0) continue;
          const event = frame.slice(7, split);
          const value = JSON.parse(frame.slice(split + 7));
          if (event === "progress") this.syncStatus = value;
          if (event === "complete") snapshot = value;
          if (event === "error") failure = value;
        }
      };
      const { data } = await this.request(
        "POST",
        "/sync?progress=true",
        undefined,
        {
          responseType: "text",
          onDownloadProgress: (update) =>
            consume(update.event?.target?.responseText),
        },
      );
      consume(data);
      if (failure) throw new Error(failure);
      // During local development the previous server may still return plain JSON.
      if (!offset && data.startsWith("{")) {
        const previous = JSON.parse(data);
        if (Array.isArray(previous.coins)) return previous;
      }
      if (!snapshot)
        throw new Error(
          "Sync ended before the wallet balance was saved. Please retry.",
        );
      return snapshot;
    },
    async refresh(sync) {
      if (this.syncing) return;
      this.syncing = true;
      this.syncStatus = sync ? "Starting wallet sync…" : "";
      try {
        const { data } = await this.request("GET", "");
        this.configured = !!data.descriptor;
        if (!this.initialized) {
          this.detailsOpen = !this.configured;
          this.initialized = true;
        }
        this.descriptor = (data.descriptor || "").replace("/{0,1}/", "/<0;1>/");
        this.notes = data.notes || {};
        this.labels = data.labels || {};
        this.gap = this.savedGap = data.gap_limit ?? 20;
        this.snapshot = data.snapshot;
        this.receive = data.receive;
        if (sync && this.configured)
          this.snapshot = await this.syncWithProgress();
        this.randomOrder = _.shuffle(
          (this.snapshot?.coins || []).map(ArgusCoins.id),
        );
        this.coins = this.coins.filter((id) =>
          this.coinOptions.some((c) => c.value === id),
        );
      } catch (error) {
        argusError(error);
      } finally {
        this.syncing = false;
        this.syncStatus = "";
      }
    },
    async sign(exportOnly = false) {
      if (this.locked || !this.valid) return;
      this.busy = true;
      try {
        const { data } = await this.request("POST", "/payments", {
          coins: this.selectedCoins.map(ArgusCoins.id),
          recipients: this.recipients,
          rate: this.rate,
        });
        this.draft = data.id;
        if (exportOnly) {
          this.exported = data.psbt;
          return;
        }
        await this.$refs.signer.signPsbt(data.psbt, (signed) =>
          this.reviewSigned(signed),
        );
      } catch (error) {
        argusError(error);
      } finally {
        this.busy = false;
      }
    },
    discard() {
      this.reviewOpen = false;
      this.review = null;
      this.draft = null;
    },
    async broadcast() {
      if (this.busy || !this.reviewOpen) return;
      this.busy = true;
      try {
        this.sent = (
          await this.request("POST", "/payments/" + this.draft + "/broadcast")
        ).data.txid;
        this.discard();
        this.showSend = false;
        this.recipients = [{ address: "", amount: null }];
        this.coins = [];
        await this.refresh(true);
      } catch (error) {
        argusError(error);
      } finally {
        this.busy = false;
      }
    },
  },
});
