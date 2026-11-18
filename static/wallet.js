function argusRequest(method, path, data) {
  const usr = new URLSearchParams(location.search).get("usr");
  return LNbits.api.request(
    method,
    "/arguswallet/api/v1/wallets" + path,
    undefined,
    data,
    { params: usr ? { usr } : {} },
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
    name: "",
    network: "Mainnet",
    working: false,
  }),
  async created() {
    try {
      this.wallets = (await argusRequest("GET", "")).data;
      this.selected = this.wallets[0] || null;
    } catch (error) {
      argusError(error);
    }
  },
  methods: {
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
        this.name = "";
      } catch (error) {
        argusError(error);
      } finally {
        this.working = false;
      }
    },
  },
});

window.app.component("argus-wallet-panel", {
  template: "#argus-wallet-panel",
  props: ["wallet", "userId"],
  emits: ["working"],
  data: () => ({
    configured: false,
    snapshot: null,
    syncing: false,
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
    locked() {
      return this.busy || this.syncing || this.reviewOpen;
    },
    coinOptions() {
      return (this.snapshot?.coins || []).map((c) => ({
        label: c.value + " sats · " + c.txid + ":" + c.vout,
        value: c.txid + ":" + c.vout,
      }));
    },
    valid() {
      return (
        this.coins.length &&
        this.coins.length <= 32 &&
        Number.isInteger(this.rate) &&
        this.rate > 0 &&
        this.recipients.every(
          (r) => r.address && Number.isSafeInteger(r.amount) && r.amount >= 546,
        )
      );
    },
  },
  watch: {
    locked(value) {
      this.$emit("working", value);
    },
  },
  mounted() {
    this.refresh(false);
  },
  methods: {
    request(method, path, data) {
      return argusRequest(method, "/" + this.wallet.id + path, data);
    },
    async refresh(sync) {
      if (this.syncing) return;
      this.syncing = true;
      try {
        const { data } = await this.request("GET", "");
        this.configured = !!data.descriptor;
        this.snapshot = data.snapshot;
        if (sync && this.configured)
          this.snapshot = (await this.request("POST", "/sync")).data;
        this.coins = this.coins.filter((id) =>
          this.coinOptions.some((c) => c.value === id),
        );
      } catch (error) {
        argusError(error);
      } finally {
        this.syncing = false;
      }
    },
    async sign() {
      if (this.locked || !this.valid) return;
      this.busy = true;
      try {
        const { data } = await this.request("POST", "/payments", {
          coins: this.coins,
          recipients: this.recipients,
          rate: this.rate,
        });
        this.draft = data.id;
        await this.$refs.signer.signPsbt(data.psbt, async (signed) => {
          this.review = (
            await this.request("POST", "/payments/" + data.id + "/review", {
              psbt: signed,
            })
          ).data;
        });
        this.reviewOpen = true;
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
