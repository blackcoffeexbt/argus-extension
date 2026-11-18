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
from arguswallet.migrations import m001_wallets


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("network,coin_type", [("Mainnet", 0), ("Testnet4", 1)])
async def test_wallet_lifecycle(tmp_path, monkeypatch, network, coin_type):
    monkeypatch.setattr(api.settings, "lnbits_data_folder", str(tmp_path))
    monkeypatch.setattr(api, "db", api.Database("ext_arguswallet"))
    await m001_wallets(api.db)
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

    def mock_chain(request):
        url = request.url.path
        if url.endswith("/utxo"):
            data = [{"txid": txid, "vout": 0, "value": 100000}]
        elif url.endswith("/hex"):
            return httpx.Response(200, text=incoming.serialize().hex())
        elif request.method == "POST":
            published.append(Transaction.parse(bytes.fromhex(request.content.decode())))
            return httpx.Response(200, text=published[-1].txid().hex())
        else:
            address = url.split("/")[-1]
            data = {
                "address": address,
                "chain_stats": {
                    "tx_count": int(address == first.address(chain(network)))
                },
                "mempool_stats": {"tx_count": 0},
            }
        return httpx.Response(200, json=data)

    monkeypatch.setattr(
        api,
        "explorer",
        lambda n: httpx.AsyncClient(
            transport=httpx.MockTransport(mock_chain),
            base_url="https://example.invalid/",
        ),
    )
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
        snapshot = (await client.post(wallet + "/sync")).json()
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
    await api.db.engine.dispose()
