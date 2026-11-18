import asyncio
import time
from graphlib import TopologicalSorter
from itertools import groupby

from embit.transaction import Transaction
from fastapi import HTTPException
from lnbits.settings import settings
from lnbits.utils.electrum import (
    ElectrumClient,
    parse_block_header,
    scripthash_from_scriptpubkey,
)
from starlette.concurrency import run_in_threadpool

from .bitcoin import account, chain


def explorer(network):
    if not settings.lnbits_blockexplorer_enabled:
        raise HTTPException(
            503, "Enable the LNbits block explorer and configure its Electrum server."
        )
    if (
        settings.lnbits_blockexplorer_network
        != {"Mainnet": "main", "Testnet4": "test4"}[network]
    ):
        raise HTTPException(
            409,
            f"Configure the LNbits block explorer for {network} to use this wallet.",
        )
    return ElectrumClient(
        settings.lnbits_blockexplorer_electrum_url, network=chain(network)
    )


async def scan_events(wallet, old, client):
    desc = account(wallet["descriptor"], wallet["network"])
    network = chain(wallet["network"])
    coins, addresses, next_indices = [], [], []
    heights, scripts = {}, {}
    gap = wallet["gap_limit"]
    changes = set()
    for branch in (0, 1):
        next_index = old["next"][branch] if old else 0
        index = 0
        while index < next_index + gap:
            end = min(index + 8, next_index + gap)
            if end > 1000:
                raise ValueError("Address scan limit reached")
            branch_name = "change" if branch else "receive"
            yield "progress", f"Checking {branch_name} addresses {index + 1}–{end}…"
            derived = await run_in_threadpool(
                lambda: [
                    desc.derive(i, branch_index=branch).script_pubkey()
                    for i in range(index, end)
                ]
            )
            hashes = [scripthash_from_scriptpubkey(spk.data) for spk in derived]
            histories = await asyncio.gather(*(client.get_history(h) for h in hashes))
            for spk, h, history in zip(derived, hashes, histories, strict=True):
                address = spk.address(network)
                scripts[spk.data] = address
                if branch:
                    changes.add(address)
                record = {
                    "address": address,
                    "branch": branch,
                    "index": index,
                    "transactions": len(history),
                    "balance": 0,
                }
                if history:
                    next_index = max(next_index, index + 1)
                    heights.update({e.tx_hash: e.height for e in history})
                    for coin in await client.listunspent(h):
                        record["balance"] += coin.value
                        coins.append(
                            {
                                "txid": coin.tx_hash,
                                "vout": coin.tx_pos,
                                "value": coin.value,
                                "height": coin.height,
                                "index": index,
                                "branch": branch,
                                "address": address,
                            }
                        )
                addresses.append(record)
                index += 1
        next_indices.append(next_index)

    received, spent, entries, timestamps = {}, {}, {}, {}
    for number, (txid, height) in enumerate(heights.items(), start=1):
        yield "progress", f"Loading transaction {number} of {len(heights)}…"
        tx = await run_in_threadpool(
            Transaction.from_string, await client.get_transaction(txid)
        )
        if tx.txid().hex() != txid:
            raise ValueError("Unexpected transaction ID from Electrum")
        if height > 0 and height not in timestamps:
            header = await client.get_block_header(height)
            timestamps[height] = parse_block_header(header, height).timestamp
        row = {
            "txid": txid,
            "height": height,
            "time": timestamps.get(height),
            "amount": 0,
            "addresses": set(),
            "outputs": [],
            "inputs": [],
        }
        for i, output in enumerate(tx.vout):
            address = scripts.get(output.script_pubkey.data)
            if address:
                row["outputs"].append(
                    {
                        "txid": txid,
                        "vout": i,
                        "address": address,
                        "value": output.value,
                        "change": address in changes,
                    }
                )
                received[(txid, i)] = (address, output.value)
                row["amount"] += output.value
                row["addresses"].add(address)
        spent[txid] = [(inp.txid.hex(), inp.vout) for inp in tx.vin]
        entries[txid] = row
    for txid, row in entries.items():
        for outpoint in spent[txid]:
            if outpoint in received:
                address, value = received[outpoint]
                row["inputs"].append(
                    {
                        "txid": outpoint[0],
                        "vout": outpoint[1],
                        "address": address,
                        "value": value,
                    }
                )
                row["amount"] -= value
                row["addresses"].add(address)
        row["addresses"] = sorted(row["addresses"])

    # Parents precede children in the same block or mempool group.
    def block_order(row):
        return row["height"] if row["height"] > 0 else float("inf")

    history = []
    for _, group in groupby(sorted(entries.values(), key=block_order), key=block_order):
        ids = sorted(r["txid"] for r in group)
        members = set(ids)
        order = TopologicalSorter(
            {
                txid: [parent for parent, _ in spent[txid] if parent in members]
                for txid in ids
            }
        )
        history.extend(entries[txid] for txid in order.static_order())
    history.reverse()
    yield "progress", "Checking the latest block height…"
    tip_height = await client.get_height()
    yield "snapshot", {
        "next": next_indices,
        "gap_limit": gap,
        "tip_height": tip_height,
        "coins": coins,
        "addresses": addresses,
        "history": history,
        "receive": desc.derive(next_indices[0], branch_index=0).address(network),
        "change": desc.derive(next_indices[1], branch_index=1).address(network),
        "balance": sum(c["value"] for c in coins),
        "checked": int(time.time()),
    }
