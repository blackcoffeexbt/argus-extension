import asyncio
import time

import httpx
from starlette.concurrency import run_in_threadpool

from .bitcoin import account, chain


def explorer(network):
    base = {
        "Mainnet": "https://mempool.space/api/",
        "Testnet4": "https://mempool.space/testnet4/api/",
    }[network]
    return httpx.AsyncClient(base_url=base, timeout=20, follow_redirects=False)


async def read(client, path):
    response = await client.get(path)
    response.raise_for_status()
    return response


async def scan(wallet, old, client):
    desc = account(wallet["descriptor"], wallet["network"])
    network = chain(wallet["network"])
    coins, activity, next_indices = [], [], []
    for branch in (0, 1):
        # Retain previously observed activity across reorgs.
        next_index = old["next"][branch] if old else 0
        index = 0
        while index < next_index + 20:
            end = min(index + 4, next_index + 20)
            if end > 1000:
                raise ValueError("Address scan limit reached")
            addresses = await run_in_threadpool(
                lambda: [
                    desc.derive(i, branch_index=branch).address(network)
                    for i in range(index, end)
                ]
            )
            summaries = await asyncio.gather(
                *(read(client, f"address/{a}") for a in addresses)
            )
            for address, response in zip(addresses, summaries, strict=True):
                summary = response.json()
                if summary["address"] != address:
                    raise ValueError("Explorer returned a different address")
                count = sum(
                    summary[k]["tx_count"] for k in ("chain_stats", "mempool_stats")
                )
                if count:
                    next_index = max(next_index, index + 1)
                    activity.append(
                        {
                            "address": address,
                            "transactions": count,
                            "branch": branch,
                            "index": index,
                        }
                    )
                    for coin in (await read(client, f"address/{address}/utxo")).json():
                        coins.append(
                            {
                                **coin,
                                "index": index,
                                "branch": branch,
                                "address": address,
                            }
                        )
                index += 1
        next_indices.append(next_index)
    return {
        "next": next_indices,
        "coins": coins,
        "activity": activity,
        "receive": desc.derive(next_indices[0], branch_index=0).address(network),
        "balance": sum(c["value"] for c in coins),
        "checked": int(time.time()),
    }
