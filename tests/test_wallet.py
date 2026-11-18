"""Offline extension route tests with a simulated explorer and signer."""

from types import SimpleNamespace

import httpx
import pytest
from embit import bip32
from embit.descriptor.checksum import add_checksum
from embit.psbt import PSBT
from embit.transaction import Transaction, TransactionInput, TransactionOutput
from fastapi import FastAPI

from arguswallet import api, arguswallet_ext
from arguswallet.bitcoin import account, chain
from arguswallet.migrations import m001_wallets, m002_address_notes
from lnbits.utils.electrum import scripthash_from_address, ElectrumError


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("network,coin_type", [("Mainnet", 0), ("Testnet4", 1)])
async def test_wallet_lifecycle(tmp_path, monkeypatch, network, coin_type):
    monkeypatch.setattr(api.settings, "lnbits_data_folder", str(tmp_path))
    monkeypatch.setattr(api, "db", api.Database("ext_arguswallet"))
    await m001_wallets(api.db)
    await m002_address_notes(api.db)
    root = bip32.HDKey.from_seed(bytes(range(32)), version=chain(network)["xprv"])
    path = f"84h/{coin_type}h/0h"
    key = root.derive("m/" + path).to_public()
    descriptor = add_checksum(
        f"wpkh([{root.my_fingerprint.hex()}/{path}]{key}/<0;1>/*)"
    )
    desc = account(descriptor, network)
    first = desc.derive(0, branch_index=0)
    incoming = Transaction(
        vin=[TransactionInput(bytes(32), 0)],
        vout=[TransactionOutput(100000, first.script_pubkey())],
    )
    txid = incoming.txid().hex()
    published = []
    offline = True
    funded = False

    class Electrum:
        async def __aenter__(self):
            if offline:
                raise ElectrumError("offline")
            return self

        async def __aexit__(self, *args):
            pass

        async def get_history(self, scripthash):
            if funded and scripthash == scripthash_from_address(
                first.address(chain(network))
            ):
                return [SimpleNamespace(tx_hash=txid, height=0)]
            return []

        async def listunspent(self, scripthash):
            return [SimpleNamespace(tx_hash=txid, tx_pos=0, value=100000, height=0)]

        async def get_transaction(self, requested):
            assert requested == txid
            return incoming.serialize().hex()

        async def broadcast(self, raw):
            published.append(Transaction.parse(bytes.fromhex(raw)))
            return published[-1].txid().hex()

        async def estimate_fee(self, blocks):
            return 0.0001 if blocks <= 3 else -1

    from arguswallet import explorer

    monkeypatch.setattr(api.settings, "lnbits_blockexplorer_enabled", True)
    monkeypatch.setattr(
        api.settings,
        "lnbits_blockexplorer_network",
        "main" if network == "Mainnet" else "test4",
    )
    monkeypatch.setattr(explorer, "ElectrumClient", lambda url, network: Electrum())
    app = FastAPI()
    app.include_router(arguswallet_ext)
    owner = SimpleNamespace(id="alice")
    app.dependency_overrides[api.check_account_exists] = lambda: owner
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        follow_redirects=True,
        base_url="http://test/arguswallet/api/v1/wallets/",
    ) as client:
        result = await client.post("", json={"name": "Test signer", "network": network})
        wallet = result.json()["id"]
        assert (
            await client.put(wallet + "/account", json={"descriptor": descriptor})
        ).is_success
        first_state = (await client.get(wallet)).json()
        assert first_state["snapshot"] is None
        assert first_state["receive"] == first.address(chain(network))
        failed = await client.post(wallet + "/sync")
        assert failed.status_code == 503 and "Electrum" in failed.json()["detail"]
        assert (await client.get(wallet)).json() == first_state
        offline = False
        empty = (await client.post(wallet + "/sync")).json()
        assert empty["balance"] == 0 and empty["coins"] == []
        assert empty["receive"] == first_state["receive"]
        assert len(empty["addresses"]) == 40 and empty["history"] == []
        assert (await client.get(wallet + "/fees")).json() == {
            "High": 10,
            "Medium": 10,
            "Low": None,
            "Economy": None,
        }
        assert (
            await client.put(
                wallet + "/addresses/" + empty["receive"] + "/note",
                json={"note": "deposit"},
            )
        ).is_success
        assert (await client.get(wallet)).json()["notes"][empty["receive"]] == "deposit"
        funded = True
        snapshot = (await client.post(wallet + "/sync")).json()
        assert snapshot["history"][0]["amount"] == 100000
        assert (await client.get(wallet + "/transactions/" + txid)).json()["txid"] == txid
        assert snapshot["balance"] == 100000 and snapshot["next"] == [1, 0]
        assert snapshot["receive"] == desc.derive(1, branch_index=0).address(
            chain(network)
        )
        data = {
            "coins": [txid + ":0"],
            "recipients": [{"address": snapshot["receive"], "amount": 31000}],
            "rate": 2,
        }
        payment = (await client.post(wallet + "/payments", json=data)).json()
        psbt = PSBT.from_base64(payment["psbt"])
        assert psbt.inputs[0].non_witness_utxo.txid() == incoming.txid()
        assert psbt.outputs[1].bip32_derivations
        assert psbt.sign_with(root) == 1
        draft = wallet + "/payments/" + payment["id"]
        assert (await client.post(draft + "/broadcast")).status_code == 409
        altered = PSBT.from_base64(psbt.to_base64())
        altered.outputs[0].value += 1
        assert (
            await client.post(draft + "/review", json={"psbt": altered.to_base64()})
        ).status_code == 400
        assert (
            await client.post(draft + "/review", json={"psbt": payment["psbt"]})
        ).status_code == 400
        review = (
            await client.post(draft + "/review", json={"psbt": psbt.to_base64()})
        ).json()
        assert review["fee"] == psbt.fee()
        assert [o["change"] for o in review["outputs"]] == [False, True]
        assert not published
        owner.id = "bob"
        assert (await client.post(draft + "/broadcast")).status_code == 404
        assert (await client.get(wallet)).status_code == 404
        owner.id = "alice"
        monkeypatch.setattr(api.settings, "lnbits_only_allow_incoming_payments", True)
        assert (await client.post(draft + "/broadcast")).status_code == 403
        monkeypatch.setattr(api.settings, "lnbits_only_allow_incoming_payments", False)
        assert (await client.post(draft + "/broadcast")).json()["txid"] == review[
            "txid"
        ]
        assert len(published) == 1
        assert (await client.get(wallet + "/drafts")).json()[0]["id"] == payment["id"]
        assert (await client.delete(wallet)).is_success
        assert (await client.get(wallet)).status_code == 404
    await api.db.engine.dispose()


def test_network_mismatch_and_disabled_explorer(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(api.settings, "lnbits_blockexplorer_enabled", False)
    with pytest.raises(HTTPException) as disabled:
        api.explorer("Mainnet")
    assert disabled.value.status_code == 503
    monkeypatch.setattr(api.settings, "lnbits_blockexplorer_enabled", True)
    monkeypatch.setattr(api.settings, "lnbits_blockexplorer_network", "test")
    with pytest.raises(HTTPException) as mismatch:
        api.explorer("Testnet4")
    assert mismatch.value.status_code == 409
