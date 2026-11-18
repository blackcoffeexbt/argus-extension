"""Bitcoin operations for the BIP84 account exported by the Argus v1 client."""

import math
import re

from embit import bip32, ec, script
from embit.descriptor import Descriptor
from embit.descriptor.checksum import checksum
from embit.networks import NETWORKS
from embit.psbt import PSBT, DerivationPath
from embit.transaction import Transaction, TransactionInput, TransactionOutput


def chain(network):
    return NETWORKS[{"Mainnet": "main", "Testnet4": "test"}[network]]


def account(text, network):
    expression, _, check = text.partition("#")
    if check and checksum(expression) != check:
        raise ValueError("Invalid account checksum")
    expression = expression.replace("/{0,1}/", "/<0;1>/")
    if not re.fullmatch(
        r"wpkh\(\[[0-9a-fA-F]{8}/84[h']/[01][h']/0[h']\]"
        r"[1-9A-HJ-NP-Za-km-z]{100,120}/<0;1>/\*\)",
        expression,
    ):
        raise ValueError("Expected an Argus BIP84 public account")
    desc = Descriptor.from_string(expression)
    key = desc.key
    coin = 0 if network == "Mainnet" else 1
    if (
        key.is_private
        or key.key.version != chain(network)["xpub"]
        or key.key.depth != 3
        or key.origin.derivation != bip32.parse_path(f"m/84h/{coin}h/0h")
    ):
        raise ValueError("Account does not match the selected network")
    return desc


def metadata(scope, desc):
    key = desc.key
    scope.bip32_derivations[key.key.get_public_key()] = DerivationPath(
        key.origin.fingerprint, key.origin.derivation
    )


def prepare(wallet, snapshot, coins, recipients, rate, previous):
    desc = account(wallet["descriptor"], wallet["network"])
    network = chain(wallet["network"])
    inputs, outputs = [], []
    for address, amount in recipients:
        spk = script.Script.from_address(address)
        if spk.address(network).lower() != address.lower():
            raise ValueError("Recipient is on another network")
        outputs.append(TransactionOutput(amount, spk))
    for coin, raw in zip(coins, previous, strict=True):
        tx = Transaction.from_string(raw)
        if tx.txid().hex() != coin["txid"] or not 0 <= coin["vout"] < len(tx.vout):
            raise ValueError("Previous transaction does not match this coin")
        output = tx.vout[coin["vout"]]
        child = desc.derive(coin["index"], branch_index=coin["branch"])
        if (
            output.value != coin["value"]
            or output.script_pubkey != child.script_pubkey()
        ):
            raise ValueError("Coin does not match the wallet")
        inputs.append((TransactionInput(tx.txid(), coin["vout"]), tx, child))
    change_index = snapshot["next"][1]
    change = desc.derive(change_index, branch_index=1)
    tx = Transaction(vin=[item[0] for item in inputs], vout=outputs)
    remainder = sum(c["value"] for c in coins) - sum(o.value for o in outputs)

    # Each native SegWit input adds at most 109 witness bytes, plus marker/flag.
    def fee():
        return rate * math.ceil(len(tx.serialize()) + (2 + 109 * len(inputs)) / 4)

    if remainder < fee():
        raise ValueError("Selected coins do not cover the amount and fee")
    outputs.append(TransactionOutput(0, change.script_pubkey()))
    change_value = remainder - fee()
    if change_value >= 546:
        outputs[-1].value = change_value
    else:
        outputs.pop()
    psbt = PSBT(tx)
    for scope, (inp, prev, child) in zip(psbt.inputs, inputs, strict=True):
        scope.non_witness_utxo = prev
        scope.witness_utxo = prev.vout[inp.vout]
        metadata(scope, child)
    if change_value >= 546:
        metadata(psbt.outputs[-1], change)
    if len(psbt.serialize()) > 32768 or len(outputs) > 32:
        raise ValueError(
            "Transaction exceeds Argus limits. Select fewer coins or recipients."
        )
    return psbt.to_base64()


def finalize(unsigned, signed, network):
    expected, result = PSBT.from_base64(unsigned), PSBT.from_base64(signed)
    tx = expected.tx
    if tx.serialize() != result.tx.serialize():
        raise ValueError("Signer returned a different transaction")
    for i, scope in enumerate(expected.inputs):
        (pub,) = scope.bip32_derivations
        signature = result.inputs[i].partial_sigs.get(pub)
        if (
            not signature
            or signature[-1] != 1
            or not pub.verify(ec.Signature.parse(signature[:-1]), expected.sighash(i))
        ):
            raise ValueError("Missing or invalid Argus signature")
        tx.vin[i].witness = script.Witness([signature, pub.sec()])
    return {
        "txid": tx.txid().hex(),
        "hex": tx.serialize().hex(),
        "fee": expected.fee(),
        "outputs": [
            {
                "address": o.script_pubkey.address(chain(network)),
                "amount": o.value,
                "change": bool(scope.bip32_derivations),
            }
            for o, scope in zip(tx.vout, expected.outputs, strict=True)
        ],
    }
