import json
import re
import time
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from lnbits.core.models.users import Account
from lnbits.db import Database
from lnbits.decorators import check_account_exists
from lnbits.settings import settings
from pydantic import BaseModel, Field, conint
from starlette.concurrency import run_in_threadpool

from .bitcoin import account, finalize, prepare
from .explorer import explorer, read, scan

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
    return {
        "descriptor": wallet["descriptor"],
        "snapshot": json.loads(wallet["snapshot"] or "null"),
    }


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


@router.post("/wallets/{wallet_id}/sync")
async def sync(wallet=Depends(owned)):
    if not wallet["descriptor"]:
        raise HTTPException(409, "Pair your Argus first")
    try:
        async with explorer(wallet["network"]) as client:
            snapshot = await scan(
                wallet, json.loads(wallet["snapshot"] or "null"), client
            )
    except Exception as exc:
        raise HTTPException(
            503, "Wallet scan failed. Retry to refresh the balance."
        ) from exc
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
    return snapshot


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
                    previous[txid] = (await read(client, f"tx/{txid}/hex")).text.strip()
        unsigned = await run_in_threadpool(
            prepare,
            wallet,
            snapshot,
            coins,
            [(r.address, r.amount) for r in data.recipients],
            data.rate,
            [previous[c["txid"]] for c in coins],
        )
    except httpx.HTTPError as exc:
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
        {"raw": tx.pop("hex"), "id": draft["id"]},
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
            result = await client.post("tx", content=draft["signed_hex"])
            result.raise_for_status()
            if result.text.strip() != expected:
                raise ValueError("Unexpected transaction ID")
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            503, f"Check transaction {expected} before retrying broadcast"
        ) from exc
    return {"txid": expected}
