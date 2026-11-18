from contextlib import aclosing

import json
import re
import time
from typing import Literal
from uuid import uuid4

import math
import asyncio
from lnbits.utils.electrum import ElectrumError, parse_raw_tx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from lnbits.core.models.users import Account
from lnbits.db import Database
from lnbits.decorators import check_account_exists
from lnbits.settings import settings
from pydantic import BaseModel, Field, conint
from starlette.concurrency import run_in_threadpool

from .bitcoin import account, chain, finalize, prepare
from .explorer import explorer, scan_events

db = Database("ext_arguswallet")
router = APIRouter(prefix="/api/v1")


class NewWallet(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    network: Literal["Mainnet", "Testnet4"] = "Mainnet"


class ImportAccount(BaseModel):
    descriptor: str = Field(min_length=1, max_length=256)


class Recipient(BaseModel):
    address: str = Field(min_length=14, max_length=100)
    amount: conint(strict=True, ge=546, le=2100000000000000)


class Payment(BaseModel):
    coins: list[str] = Field(min_items=1, max_items=32)
    recipients: list[Recipient] = Field(min_items=1, max_items=32)
    rate: conint(strict=True, ge=1, le=10000)


class Signature(BaseModel):
    psbt: str = Field(min_length=1, max_length=60000)


async def owned(wallet_id: str, owner: Account = Depends(check_account_exists)):
    row = await db.fetchone(
        "SELECT * FROM arguswallet.wallets WHERE id = :id AND owner = :owner",
        {"id": wallet_id, "owner": owner.id},
    )
    if not row:
        raise HTTPException(404, "Argus wallet not found")
    return row


def allow_send():
    if settings.lnbits_only_allow_incoming_payments:
        raise HTTPException(403, "Only incoming payments allowed")


@router.get("/wallets")
async def wallets(owner: Account = Depends(check_account_exists)):
    return await db.fetchall(
        "SELECT id, name, network FROM arguswallet.wallets WHERE owner = :owner ORDER BY name, id",
        {"owner": owner.id},
    )


@router.post("/wallets", status_code=201)
async def new_wallet(data: NewWallet, owner: Account = Depends(check_account_exists)):
    wallet = {"id": uuid4().hex, "network": data.network, "name": data.name.strip()}
    if not wallet["name"]:
        raise HTTPException(400, "Enter a wallet name")
    await db.execute(
        "INSERT INTO arguswallet.wallets (id, owner, name, network) VALUES (:id, :owner, :name, :network)",
        {**wallet, "owner": owner.id},
    )
    return wallet


@router.get("/wallets/{wallet_id}")
async def state(wallet=Depends(owned)):
    snapshot = json.loads(wallet["snapshot"] or "null")
    receive = snapshot["receive"] if snapshot else None
    if wallet["descriptor"] and not snapshot:
        desc = account(wallet["descriptor"], wallet["network"])
        receive = desc.derive(0, branch_index=0).address(chain(wallet["network"]))
    return {
        "descriptor": wallet["descriptor"],
        "gap_limit": wallet["gap_limit"],
        "labels": {
            r["reference"]: r["label"]
            for r in await db.fetchall(
                "SELECT reference, label FROM arguswallet.labels WHERE wallet = :id",
                {"id": wallet["id"]},
            )
        },
        "notes": {
            row["address"]: row["note"]
            for row in await db.fetchall(
                "SELECT address, note FROM arguswallet.notes WHERE wallet = :wallet",
                {"wallet": wallet["id"]},
            )
        },
        "snapshot": snapshot,
        "receive": receive,
    }


class GapLimit(BaseModel):
    gap_limit: conint(strict=True, ge=1, le=200)


@router.put("/wallets/{wallet_id}/gap-limit")
async def set_gap_limit(data: GapLimit, wallet=Depends(owned)):
    snapshot = json.loads(wallet["snapshot"] or "{}")
    if max(snapshot.get("next", [0, 0])) + data.gap_limit > 1000:
        raise HTTPException(
            400, "This gap would exceed the 1,000-address scan limit per branch."
        )
    await db.execute(
        """UPDATE arguswallet.wallets SET gap_limit = :gap, revision = revision + 1
        WHERE id = :id AND gap_limit != :gap""",
        {"gap": data.gap_limit, "id": wallet["id"]},
    )
    return data


class WalletLabel(BaseModel):
    label: str = Field(max_length=500)


@router.put("/wallets/{wallet_id}/labels/{reference}")
async def save_label(reference: str, data: WalletLabel, wallet=Depends(owned)):
    snapshot = json.loads(wallet["snapshot"] or "{}")
    if not (
        any(r["txid"] == reference for r in snapshot.get("history", []))
        or any(
            f"{c['txid']}:{c['vout']}" == reference for c in snapshot.get("coins", [])
        )
    ):
        raise HTTPException(404, "Transaction or output not found in this wallet")
    await db.execute(
        """INSERT INTO arguswallet.labels (wallet, reference, label)
        VALUES (:wallet, :ref, :label) ON CONFLICT (wallet, reference)
        DO UPDATE SET label = excluded.label""",
        {"wallet": wallet["id"], "ref": reference, "label": data.label.strip()},
    )
    return {"saved": True}


class AddressNote(BaseModel):
    note: str = Field(max_length=500)


@router.put("/wallets/{wallet_id}/addresses/{address}/note")
async def save_note(address: str, data: AddressNote, wallet=Depends(owned)):
    snapshot = json.loads(wallet["snapshot"] or "{}")
    if not any(a["address"] == address for a in snapshot.get("addresses", [])):
        raise HTTPException(404, "Sync this wallet to find the address")
    await db.execute(
        """INSERT INTO arguswallet.notes (wallet, address, note)
        VALUES (:wallet, :address, :note) ON CONFLICT (wallet, address)
        DO UPDATE SET note = excluded.note""",
        {"wallet": wallet["id"], "address": address, "note": data.note},
    )
    return {"saved": True}


@router.delete("/wallets/{wallet_id}")
async def remove_wallet(wallet=Depends(owned)):
    key = {"id": wallet["id"]}
    await db.execute("DELETE FROM arguswallet.drafts WHERE wallet = :id", key)
    await db.execute("DELETE FROM arguswallet.labels WHERE wallet = :id", key)
    await db.execute("DELETE FROM arguswallet.notes WHERE wallet = :id", key)
    await db.execute("DELETE FROM arguswallet.wallets WHERE id = :id", key)
    return {"removed": True}


@router.put("/wallets/{wallet_id}/account")
async def import_account(data: ImportAccount, wallet=Depends(owned)):
    try:
        descriptor = str(account(data.descriptor, wallet["network"])).replace(
            "<0;1>", "{0,1}"
        )
    except Exception as exc:
        raise HTTPException(400, "Invalid public account for this network") from exc
    result = await db.execute(
        """UPDATE arguswallet.wallets SET descriptor = :descriptor WHERE id = :id
        AND (descriptor IS NULL OR descriptor = :descriptor)""",
        {"descriptor": descriptor, "id": wallet["id"]},
    )
    if not result.rowcount:
        raise HTTPException(409, "This wallet belongs to a different signer account")
    return {"imported": True}


# Admission has no await: one event loop atomically reserves a worker slot.
# Limits apply per application worker; deployments should also bound worker count.
MAX_ACTIVE_SCANS = 4
SCAN_TIMEOUT = 120
_active_scans = {}


async def sync_events(wallet):
    if (wallet["id"] in _active_scans or wallet["owner"] in _active_scans.values()
            or len(_active_scans) >= MAX_ACTIVE_SCANS):
        raise HTTPException(429, "A wallet scan is already running. Retry later.")
    _active_scans[wallet["id"]] = wallet["owner"]
    try:
        async with asyncio.timeout(SCAN_TIMEOUT):
            async with aclosing(_sync_events(wallet)) as events:
                async for event in events:
                    yield event
    except TimeoutError as exc:
        raise HTTPException(503, "Wallet scan time limit reached. Previous balance retained.") from exc
    finally:
        _active_scans.pop(wallet["id"], None)


async def _sync_events(wallet):
    yield "progress", "Connecting to Electrum…"
    try:
        async with explorer(wallet["network"]) as client:
            async for event, value in scan_events(
                wallet, json.loads(wallet["snapshot"] or "null"), client
            ):
                if event == "snapshot":
                    snapshot = value
                    break
                yield event, value
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            503,
            "Scan failed. Check the Electrum server connection and network in LNbits block explorer settings.",
        ) from exc
    yield "progress", "Saving the wallet balance and transaction history…"
    result = await db.execute(
        """UPDATE arguswallet.wallets SET snapshot = :snapshot, revision = revision + 1
        WHERE id = :id AND revision = :revision""",
        {
            "snapshot": json.dumps(snapshot),
            "id": wallet["id"],
            "revision": wallet["revision"],
        },
    )
    if not result.rowcount:
        raise HTTPException(409, "A newer scan finished. Refresh the wallet.")
    yield "complete", snapshot


@router.post("/wallets/{wallet_id}/sync")
async def sync(progress: bool = False, wallet=Depends(owned)):
    if not wallet["descriptor"]:
        raise HTTPException(409, "Pair your Argus first")
    if not progress:
        async with aclosing(sync_events(wallet)) as events:
            async for event, value in events:
                if event == "complete":
                    return value

    async def stream():
        try:
            async with aclosing(sync_events(wallet)) as events:
                async for event, value in events:
                    yield f"event: {event}\ndata: {json.dumps(value)}\n\n"
        except HTTPException as exc:
            yield f"event: error\ndata: {json.dumps(exc.detail)}\n\n"
        except Exception:
            yield 'event: error\ndata: "Unable to save the wallet sync. Please retry."\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/wallets/{wallet_id}/transactions/{txid}")
async def transaction_details(txid: str, wallet=Depends(owned)):
    snapshot = json.loads(wallet["snapshot"] or "{}")
    if not any(item["txid"] == txid for item in snapshot.get("history", [])):
        raise HTTPException(404, "Transaction not found in this wallet")
    try:
        async with explorer(wallet["network"]) as client:
            raw = await client.get_transaction(txid)
        tx = await run_in_threadpool(
            parse_raw_tx, raw, network=chain(wallet["network"])
        )
        if tx.txid != txid:
            raise ValueError("Unexpected transaction")
        return tx
    except (OSError, ElectrumError, ValueError) as exc:
        raise HTTPException(503, "Transaction unavailable from Electrum") from exc


@router.get("/wallets/{wallet_id}/drafts")
async def drafts(wallet=Depends(owned)):
    return await db.fetchall(
        """SELECT id, expires, unsigned FROM arguswallet.drafts
        WHERE wallet = :id AND expires > :now ORDER BY expires DESC""",
        {"id": wallet["id"], "now": int(time.time())},
    )


@router.get("/wallets/{wallet_id}/fees")
async def fees(wallet=Depends(owned)):
    try:
        async with explorer(wallet["network"]) as client:
            rates = await asyncio.gather(
                *(client.estimate_fee(n) for n in (1, 3, 6, 144))
            )
    except (OSError, ElectrumError) as exc:
        raise HTTPException(503, "Electrum fee estimates are unavailable") from exc
    return {
        name: max(1, math.ceil(rate * 100000)) if rate > 0 else None
        for name, rate in zip(("High", "Medium", "Low", "Economy"), rates, strict=True)
    }


@router.post("/wallets/{wallet_id}/payments", dependencies=[Depends(allow_send)])
async def payment(data: Payment, wallet=Depends(owned)):
    snapshot = json.loads(wallet["snapshot"] or "null")
    if not snapshot:
        raise HTTPException(409, "Sync the wallet before spending")
    available = {f"{c['txid']}:{c['vout']}": c for c in snapshot["coins"]}
    if (
        len(set(data.coins)) != len(data.coins)
        or not set(data.coins) <= available.keys()
    ):
        raise HTTPException(400, "Select available coins from this wallet")
    coins = [available[k] for k in data.coins]
    try:
        async with explorer(wallet["network"]) as client:
            previous = {}
            for c in coins:
                txid = c["txid"]
                if not re.fullmatch("[a-f0-9]{64}", txid):
                    raise ValueError("Invalid transaction ID")
                if txid not in previous:
                    previous[txid] = await client.get_transaction(txid)
        unsigned = await run_in_threadpool(
            prepare,
            wallet,
            snapshot,
            coins,
            [(r.address, r.amount) for r in data.recipients],
            data.rate,
            [previous[c["txid"]] for c in coins],
        )
    except HTTPException:
        raise
    except (OSError, ElectrumError) as exc:
        raise HTTPException(503, "Previous transaction unavailable") from exc
    except Exception as exc:
        raise HTTPException(
            400, "Check the selected coins, recipient network, amount and fee"
        ) from exc
    draft = uuid4().hex
    now = int(time.time())
    await db.execute(
        "DELETE FROM arguswallet.drafts WHERE expires <= :now", {"now": now}
    )
    await db.execute(
        "INSERT INTO arguswallet.drafts (id, wallet, expires, unsigned) VALUES (:id, :wallet, :expires, :unsigned)",
        {
            "id": draft,
            "wallet": wallet["id"],
            "expires": now + 1800,
            "unsigned": unsigned,
        },
    )
    return {"id": draft, "psbt": unsigned}


async def owned_draft(draft_id: str, wallet=Depends(owned)):
    row = await db.fetchone(
        "SELECT * FROM arguswallet.drafts WHERE id = :id AND wallet = :wallet AND expires > :now",
        {"id": draft_id, "wallet": wallet["id"], "now": int(time.time())},
    )
    if not row:
        raise HTTPException(404, "Payment not found or expired")
    return row


@router.post(
    "/wallets/{wallet_id}/payments/{draft_id}/review",
    dependencies=[Depends(allow_send)],
)
async def review(data: Signature, wallet=Depends(owned), draft=Depends(owned_draft)):
    try:
        tx = await run_in_threadpool(
            finalize, draft["unsigned"], data.psbt, wallet["network"]
        )
    except Exception as exc:
        raise HTTPException(
            400, "Signed transaction does not match the prepared payment"
        ) from exc
    await db.execute(
        "UPDATE arguswallet.drafts SET signed_hex = :raw WHERE id = :id",
        {"raw": tx["hex"], "id": draft["id"]},
    )
    return tx


@router.post(
    "/wallets/{wallet_id}/payments/{draft_id}/broadcast",
    dependencies=[Depends(allow_send)],
)
async def broadcast(wallet=Depends(owned), draft=Depends(owned_draft)):
    if not draft["signed_hex"]:
        raise HTTPException(409, "Review the signed payment first")
    from embit.transaction import Transaction

    expected = Transaction.from_string(draft["signed_hex"]).txid().hex()
    try:
        async with explorer(wallet["network"]) as client:
            result = await client.broadcast(draft["signed_hex"])
            if result != expected:
                raise ValueError("Unexpected transaction ID")
    except (OSError, ElectrumError, ValueError) as exc:
        raise HTTPException(
            503, f"Check transaction {expected} before retrying broadcast"
        ) from exc
    return {"txid": expected}
