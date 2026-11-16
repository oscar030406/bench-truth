"""Run guidellm against the bench-truth server and compare its numbers with /truth.

    python scripts/guidellm_bench.py --exe <venv>/bin/guidellm --concurrency 64 \
        --out-dir out/guidellm-64 --summary out/guidellm-64.json

Backend validation is off, as in the runs in results/. Before 0.4.0 the server had no
/health route for it.
"""

from __future__ import annotations

import json

from bench_truth.tools import Reported, Request, Run, main


def build(run: Run) -> list[str]:
    return [
        str(run.exe),
        "run",
        "--backend",
        f"kind=openai_http,target={run.url},model=bench-truth,"
        "request_format=/v1/completions,validate_backend=false",
        "--profile",
        f"kind=concurrent,streams={run.concurrency}",
        "--constraint",
        f"kind=max_requests,count={run.requests}",
        "--data",
        "kind=synthetic_text,prompt_tokens=8,output_tokens=256",
        "--tokenizer",
        f"kind=hf_auto,model={run.tokenizer}",
        "--output",
        f"kind=json,path={run.out_dir / 'benchmarks.json'}",
        "--disable-progress",
    ]


def parse(run: Run) -> Reported:
    bench = json.loads((run.out_dir / "benchmarks.json").read_text())["benchmarks"][0]
    metrics = bench["metrics"]
    return Reported(
        requests=[
            Request(
                start=r["request_start_time"],
                ttft=r["time_to_first_token_ms"] / 1000,
                latency=r["request_latency"],
                itl=r["inter_token_latency_ms"] / 1000,
            )
            for r in bench["requests"]["successful"]
        ],
        mean_ttft_ms=metrics["time_to_first_token_ms"]["successful"]["mean"],
        mean_itl_ms=metrics["inter_token_latency_ms"]["successful"]["mean"],
    )


if __name__ == "__main__":
    main("guidellm", "guidellm", build, parse)
