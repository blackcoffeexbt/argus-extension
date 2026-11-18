// Native SegWit Argus inputs. Include the marker/flag and the largest signature
// accepted by the signer, using the same ceiling as bitcoin.prepare.
window.ArgusCoins = (() => {
  const id = (c) => c.txid + ":" + c.vout;
  function outputBytes(address) {
    const a = (address || "").toLowerCase();
    if (/^(bc|tb)1/.test(a)) {
      const programChars = a.length - a.indexOf("1") - 8;
      return 11 + Math.floor((programChars * 5) / 8);
    }
    return /^[32]/.test(a) ? 32 : 34;
  }
  function fee(n, recipients, rate, change = false) {
    const outputs = recipients.reduce(
      (sum, r) => sum + outputBytes(r.address),
      0,
    );
    return (
      rate *
      Math.ceil(10 + n * 41 + outputs + (change ? 31 : 0) + (2 + 109 * n) / 4)
    );
  }
  function select(coins, mode, manual, recipients, rate, randomOrder) {
    if (mode === "Manual") return coins.filter((c) => manual.includes(id(c)));
    if (mode === "Select All") return coins;
    const target = recipients.reduce(
      (sum, r) => sum + (Number(r.amount) || 0),
      0,
    );
    if (!target) return [];
    const ordered = [...coins].sort((a, b) =>
      mode === "Random"
        ? randomOrder.indexOf(id(a)) - randomOrder.indexOf(id(b))
        : mode === "Smaller Inputs First"
          ? a.value - b.value
          : b.value - a.value,
    );
    let total = 0;
    for (let i = 0; i < ordered.length; ++i) {
      total += ordered[i].value;
      if (total >= target + fee(i + 1, recipients, rate))
        return ordered.slice(0, i + 1);
    }
    return ordered;
  }
  function parseBitcoinRequest(value) {
    const text = value.trim();
    if (!/^bitcoin:/i.test(text)) return { address: text };
    const request = text.slice(8);
    const [encodedAddress, query = ""] = request.split("?");
    const params = new URLSearchParams(query);
    if (
      !encodedAddress ||
      encodedAddress.startsWith("//") ||
      request.includes("#")
    )
      throw new Error("Invalid bitcoin payment link");
    for (const key of params.keys()) {
      if (key.startsWith("req-"))
        throw new Error("This payment link requires an unsupported feature");
    }
    if (params.getAll("amount").length > 1)
      throw new Error("Payment link contains more than one amount");
    const result = { address: decodeURIComponent(encodedAddress) };
    if (params.has("amount")) {
      const amount = params.get("amount");
      if (!/^\d+(\.\d{1,8})?$/.test(amount))
        throw new Error("Payment link has an invalid bitcoin amount");
      const [whole, fraction = ""] = amount.split(".");
      const sats = Number(whole) * 100000000 + Number(fraction.padEnd(8, "0"));
      if (!Number.isSafeInteger(sats) || sats <= 0 || sats > 2100000000000000)
        throw new Error("Payment link amount is out of range");
      result.amount = sats;
    }
    return result;
  }

  return { id, fee, select, parseBitcoinRequest };
})();
