"""Compare a benchmark's per-request TTFT with the server's records.

Requests are matched by start order: the benchmark's list is in submission
order and the server's records are sorted by arrival. The first `concurrency`
requests start together, so they are reported apart from the rest.
"""

from __future__ import annotations

import statistics


def split_ttft(bench_ttfts: list[float], records: list[dict], concurrency: int) -> dict:
    """bench_ttfts in seconds, in submission order."""
    records = sorted(records, key=lambda r: r["arrived"])[-len(bench_ttfts) :]
    server = [r["sent"][0] for r in records]
    out = {}
    for name, lo, hi in (("first", 0, concurrency), ("later", concurrency, len(server))):
        if hi > lo:
            b, s = bench_ttfts[lo:hi], server[lo:hi]
            out[name] = {
                "bench_ms": statistics.mean(b) * 1000,
                "server_ms": statistics.mean(s) * 1000,
                "n": hi - lo,
            }
    return out
