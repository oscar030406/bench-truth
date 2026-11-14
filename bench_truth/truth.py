"""Summarise what the server actually sent, as the numbers a benchmark reports.

python -m bench_truth.truth --url http://127.0.0.1:8100
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import urllib.request


def summarise(records: list[dict]) -> dict:
    ttft = [r["sent"][0] for r in records]
    itl = [b - a for r in records for a, b in itertools.pairwise(r["sent"])]
    tpot = [
        (r["sent"][-1] - r["sent"][0]) / (len(r["sent"]) - 1) for r in records if len(r["sent"]) > 1
    ]
    return {
        "requests": len(records),
        "mean_ttft_ms": statistics.mean(ttft) * 1000,
        "mean_itl_ms": statistics.mean(itl) * 1000,
        "mean_tpot_ms": statistics.mean(tpot) * 1000,
        "output_tokens": sum(r["tokens"] for r in records),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8100")
    args = p.parse_args()
    with urllib.request.urlopen(f"{args.url}/truth") as resp:
        records = json.load(resp)
    print(json.dumps(summarise(records)))


if __name__ == "__main__":
    main()
