"""Summarise what the server actually sent, as the numbers a benchmark reports.

python -m bench_truth.truth --url http://127.0.0.1:8100 --last 1024 --concurrency 256
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


def measured(records: list[dict], last: int | None = None) -> list[dict]:
    """The records in arrival order, and with `last`, only the last `last` of them.

    Clients often send warmup or test requests before the measured ones. These arrive first.
    """
    records = sorted(records, key=lambda r: r["arrived"])
    return records[-last:] if last else records


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", default="http://127.0.0.1:8100")
    p.add_argument(
        "--last",
        type=int,
        help="keep only the last LAST requests by arrival time, to drop warmup and test requests",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        help="also summarise the first CONCURRENCY requests apart from the rest",
    )
    args = p.parse_args()
    with urllib.request.urlopen(f"{args.url}/truth") as resp:
        records = measured(json.load(resp), args.last)
    out = summarise(records)
    if args.concurrency:
        if len(records) <= args.concurrency:
            p.error(f"--concurrency {args.concurrency} needs more than {len(records)} requests")
        out = {
            "all": out,
            "first_burst": summarise(records[: args.concurrency]),
            "after_first_burst": summarise(records[args.concurrency :]),
        }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
