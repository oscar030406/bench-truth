"""A minimal streaming client, used to check the server itself.

It sends each request with an id, so every request can be matched with the
server's record. Server and client run on the same machine and read the same
monotonic clock, which splits TTFT into the time before the server's handler
starts and the time the server takes after that.

    python -m bench_truth.reference --url http://127.0.0.1:8100 --concurrency 16
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

import aiohttp


async def one(session: aiohttp.ClientSession, url: str, rid: str) -> dict:
    payload = {"model": "bench-truth", "prompt": "hi", "stream": True}
    sent = time.perf_counter()
    first = None
    async with session.post(
        f"{url}/v1/completions", json=payload, headers={"x-bt-id": rid}
    ) as resp:
        async for line in resp.content:
            if first is None and line.startswith(b"data: {"):
                choices = json.loads(line[6:])["choices"]
                if choices and choices[0]["text"]:
                    first = time.perf_counter()
    return {"id": rid, "sent": sent, "first": first}


async def run(url: str, concurrency: int, requests: int, trust_env: bool = False) -> None:
    limit = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=0), trust_env=trust_env
    ) as session:
        await one(session, url, "warmup")

        async def bounded(i: int) -> dict:
            async with limit:
                return await one(session, url, f"{os.getpid()}-{i}")

        measured = await asyncio.gather(*(bounded(i) for i in range(requests)))
        async with session.get(f"{url}/truth") as resp:
            truth = {r["id"]: r for r in await resp.json()}
    ttft = [m["first"] - m["sent"] for m in measured]
    before = [truth[m["id"]]["arrived"] - m["sent"] for m in measured]
    after = [truth[m["id"]]["sent"][0] for m in measured]
    later = before[concurrency:]  # requests that did not start in the first burst
    if later:
        print(
            f"c={concurrency} before handler: first burst "
            f"{statistics.mean(before[:concurrency]) * 1000:.2f} ms, later "
            f"{statistics.mean(later) * 1000:.2f} ms"
        )
    print(
        f"c={concurrency} client ttft {statistics.mean(ttft) * 1000:.2f} ms = "
        f"before handler {statistics.mean(before) * 1000:.2f} + "
        f"server first token {statistics.mean(after) * 1000:.2f} + rest"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8100")
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--requests", type=int, default=0, help="default: 4 x concurrency")
    p.add_argument("--trust-env", action="store_true", help="aiohttp trust_env=True")
    args = p.parse_args()
    asyncio.run(
        run(args.url, args.concurrency, args.requests or 4 * args.concurrency, args.trust_env)
    )


if __name__ == "__main__":
    main()
