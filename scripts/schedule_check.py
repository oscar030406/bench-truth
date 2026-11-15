"""Measure how late a server sends each chunk against its planned time.

Start a server with `--scenario uniform --tokens 32`, then run:

    python scripts/schedule_check.py --url http://127.0.0.1:8100

It sends the requests one after another, reads /truth, and prints the lateness
of all chunks. results/schedule-2026-11-15.json holds its output on six runners.
"""

from __future__ import annotations

import argparse
import json
import statistics
import urllib.request

# uniform: the first chunk 50 ms after arrival, then one every 20 ms
TTFT_S, ITL_S = 0.05, 0.02


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", default="http://127.0.0.1:8100")
    p.add_argument("--requests", type=int, default=10)
    args = p.parse_args()
    body = json.dumps({"prompt": "x", "stream": True}).encode()
    for _ in range(args.requests):
        req = urllib.request.Request(
            f"{args.url}/v1/completions", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            resp.read()
    with urllib.request.urlopen(f"{args.url}/truth") as resp:
        records = json.load(resp)
    late = [(s - (TTFT_S + ITL_S * i)) * 1000 for r in records for i, s in enumerate(r["sent"])]
    first = [r["sent"][0] * 1000 - TTFT_S * 1000 for r in records]
    q = statistics.quantiles(late, n=100)
    print(
        f"chunks={len(late)} min={min(late):.2f} median={statistics.median(late):.2f} "
        f"p90={q[89]:.2f} p99={q[98]:.2f} max={max(late):.2f} ms | first chunk median "
        f"{statistics.median(first):.2f} max {max(first):.2f} ms"
    )


if __name__ == "__main__":
    main()
