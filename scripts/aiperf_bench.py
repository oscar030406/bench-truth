"""Run aiperf against the bench-truth server and compare its numbers with /truth.

    python scripts/aiperf_bench.py --exe <venv>/bin/aiperf --concurrency 64 \
        --out-dir out/aiperf-64 --summary out/aiperf-64.json
"""

from __future__ import annotations

import json

from bench_truth.tools import Reported, Request, Run, main


def build(run: Run) -> list[str]:
    return [
        str(run.exe),
        "profile",
        "--model",
        "bench-truth",
        "--url",
        run.url,
        "--endpoint-type",
        "completions",
        "--streaming",
        "--concurrency",
        str(run.concurrency),
        "--request-count",
        str(run.requests),
        "--tokenizer",
        run.tokenizer,
        "--prompt-input-tokens-mean",
        "8",
        "--prompt-input-tokens-stddev",
        "0",
        "--prompt-output-tokens-mean",
        "256",
        "--prompt-output-tokens-stddev",
        "0",
        "--ui-type",
        "none",
        "--export-level",
        "records",
        "--artifact-dir",
        str(run.out_dir),
    ]


def parse(run: Run) -> Reported:
    summary = json.loads((run.out_dir / "profile_export_aiperf.json").read_text())
    lines = (run.out_dir / "profile_export.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines if line]
    return Reported(
        requests=[
            Request(
                start=r["metadata"]["request_start_ns"],
                ttft=r["metrics"]["time_to_first_token"]["value"] / 1000,
                latency=r["metrics"]["request_latency"]["value"] / 1000,
                itl=r["metrics"]["inter_token_latency"]["value"] / 1000,
            )
            for r in records
            if r["metadata"]["benchmark_phase"] == "profiling"
        ],
        mean_ttft_ms=summary["time_to_first_token"]["avg"],
        mean_itl_ms=summary["inter_token_latency"]["avg"],
    )


if __name__ == "__main__":
    main("aiperf", "aiperf", build, parse)
