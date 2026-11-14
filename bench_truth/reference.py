"""A minimal streaming client, used to check the server itself.

It measures TTFT and inter-token latency the plain way and compares them
with what the server says it sent. Both clocks start at different moments
(client send vs server arrival), so agreement within a millisecond shows
that the server keeps its schedule.

    python -m bench_truth.reference --url http://127.0.0.1:8100 --requests 5
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import statistics
import time

import aiohttp


async def one(session: aiohttp.ClientSession, url: str) -> dict:
    start = time.perf_counter()
    arrivals = []
    payload = {
        "model": "bench-truth",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    async with session.post(f"{url}/v1/chat/completions", json=payload) as resp:
        async for line in resp.content:
            if line.startswith(b"data: {") and b'"content"' in line:
                arrivals.append(time.perf_counter() - start)
    return {"ttft": arrivals[0], "itl": [b - a for a, b in itertools.pairwise(arrivals)]}


async def run(url: str, requests: int) -> None:
    async with aiohttp.ClientSession() as session:
        await one(session, url)  # warm-up: the first request also opens the connection
        measured = [await one(session, url) for _ in range(requests)]
        async with session.get(f"{url}/truth") as resp:
            truth = (await resp.json())[-requests:]
    ttft_err = [m["ttft"] - t["sent"][0] for m, t in zip(measured, truth, strict=True)]
    itl_m = [x for m in measured for x in m["itl"]]
    itl_t = [b - a for t in truth for a, b in zip(t["sent"], t["sent"][1:])]
    print(f"ttft error ms: max {max(abs(e) for e in ttft_err) * 1000:.2f}")
    print(
        f"itl mean ms: measured {statistics.mean(itl_m) * 1000:.2f} truth {statistics.mean(itl_t) * 1000:.2f}"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8100")
    p.add_argument("--requests", type=int, default=5)
    args = p.parse_args()
    asyncio.run(run(args.url, args.requests))


if __name__ == "__main__":
    main()
