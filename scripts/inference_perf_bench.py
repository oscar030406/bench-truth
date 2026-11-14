"""Run inference-perf against the bench-truth server and compare its numbers with /truth.

    python scripts/inference_perf_bench.py --exe <venv>/bin/inference-perf --concurrency 64 \
        --out-dir out/inference-perf-64 --summary out/inference-perf-64.json

inference-perf takes a YAML config. JSON is valid YAML, so the adapter writes JSON.
"""

from __future__ import annotations

import json

from bench_truth.tools import Reported, Request, Run, main


def build(run: Run) -> list[str]:
    def fixed(n: int) -> dict:
        return {"min": n, "max": n, "mean": n, "std_dev": 0, "total_count": run.requests}

    config = {
        "api": {"type": "completion", "streaming": True},
        "data": {
            "type": "random",
            "input_distribution": fixed(8),
            "output_distribution": fixed(256),
        },
        "load": {
            "type": "concurrent",
            "stages": [{"num_requests": run.requests, "concurrency_level": run.concurrency}],
        },
        "server": {
            "type": "vllm",
            "model_name": "bench-truth",
            "base_url": run.url,
            "ignore_eos": True,
        },
        "tokenizer": {"pretrained_model_name_or_path": run.tokenizer},
        "report": {
            "request_lifecycle": {
                "per_request": True,
                "per_request_fields": {"computed_metrics": True, "response_chunks": False},
            }
        },
        "storage": {"local_storage": {"path": str(run.out_dir)}},
    }
    path = run.out_dir / "config.json"
    path.write_text(json.dumps(config, indent=1))
    return [str(run.exe), "-c", str(path)]


def parse(run: Run) -> Reported:
    per_request = json.loads((run.out_dir / "per_request_lifecycle_metrics.json").read_text())
    summary = json.loads((run.out_dir / "summary_lifecycle_metrics.json").read_text())
    latency = summary["successes"]["latency"]
    return Reported(
        requests=[
            Request(
                start=r["start_time"],
                ttft=r["computed_metrics"]["time_to_first_token"],
                latency=r["computed_metrics"]["request_latency"],
                itl=r["computed_metrics"]["inter_token_latency"],
            )
            for r in per_request
        ],
        mean_ttft_ms=latency["time_to_first_token"]["mean"] * 1000,
        mean_itl_ms=latency["inter_token_latency"]["mean"] * 1000,
    )


if __name__ == "__main__":
    main("inference-perf", "inference-perf", build, parse)
