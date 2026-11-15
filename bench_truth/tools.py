"""Run a benchmark client against the server and compare what it reports with /truth.

Each adapter in `scripts/` builds the client's command line and reads its output files.
This module runs the command, takes the server records that arrived during the run,
and writes one JSON summary.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bench_truth.compare import split_ttft
from bench_truth.truth import summarise


@dataclass
class Request:
    start: float  # the client's own clock; used only to put requests in start order
    ttft: float  # seconds, as the client reports it for this request
    latency: float  # seconds from request start to the last token, as the client reports it
    itl: float | None = None  # seconds; the client's per-request mean, if it reports one


@dataclass
class Reported:
    requests: list[Request]
    mean_ttft_ms: float  # the client's own summary
    mean_itl_ms: float


@dataclass
class Run:
    exe: Path  # the client's executable in its venv
    url: str
    concurrency: int
    requests: int
    tokenizer: str
    out_dir: Path  # where the client writes its files


Build = Callable[[Run], list[str]]
Parse = Callable[[Run], Reported]


def fetch_truth(url: str) -> list[dict]:
    with urllib.request.urlopen(f"{url}/truth") as resp:
        return json.load(resp)


def dist_version(exe: Path, dist: str) -> str:
    code = f"import importlib.metadata as m; print(m.version({dist!r}))"
    out = subprocess.run(
        [str(exe.parent / "python"), "-c", code], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def pct(reported: float, true: float) -> float:
    return (reported - true) / true * 100


LATE_S = 0.5


def late_requests(ordered: list[Request], records: list[dict]) -> dict:
    """Requests whose reported latency exceeds the server's by more than LATE_S.

    A client that times requests with the wall clock (time.time()) adds any step of that
    clock to the requests in flight. The per-request ITL error without them shows what
    the client measures between steps.
    """
    server = sorted(records, key=lambda r: r["arrived"])[-len(ordered) :]
    late = [r.latency - s["sent"][-1] > LATE_S for r, s in zip(ordered, server, strict=True)]
    out: dict = {"late_requests": sum(late)}
    on_time = [(r, s) for r, s, x in zip(ordered, server, late, strict=True) if not x]
    if on_time and all(r.itl is not None for r, _ in on_time):
        rep = statistics.mean(r.itl for r, _ in on_time)
        true = statistics.mean(
            (s["sent"][-1] - s["sent"][0]) / (len(s["sent"]) - 1) for _, s in on_time
        )
        out["itl_error_pct_on_time"] = pct(rep, true)
    return out


def compare(reported: Reported, records: list[dict], concurrency: int) -> dict:
    ordered = sorted(reported.requests, key=lambda r: r.start)
    split = split_ttft([r.ttft for r in ordered], records, concurrency)
    truth = summarise(records)
    later = split["later"]
    return {
        **late_requests(ordered, records),
        "reported_ttft_ms": reported.mean_ttft_ms,
        "true_ttft_ms": truth["mean_ttft_ms"],
        "ttft_split": split,
        "reported_ttft_later_ms": later["bench_ms"],
        "true_ttft_later_ms": later["server_ms"],
        "ttft_later_error_pct": pct(later["bench_ms"], later["server_ms"]),
        "reported_itl_ms": reported.mean_itl_ms,
        "true_itl_ms": truth["mean_itl_ms"],
        "itl_error_pct": pct(reported.mean_itl_ms, truth["mean_itl_ms"]),
        "server_records": len(records),
        "client_requests": len(reported.requests),
    }


def main(tool: str, dist: str, build: Build, parse: Parse) -> None:
    p = argparse.ArgumentParser(description=f"Run {tool} against the bench-truth server.")
    p.add_argument("--exe", type=Path, required=True, help=f"{tool} executable in its venv")
    p.add_argument("--url", default="http://127.0.0.1:8100")
    p.add_argument("--concurrency", type=int, required=True)
    p.add_argument("--requests", type=int, default=0, help="default: 4 x concurrency")
    p.add_argument("--tokenizer", default="Qwen/Qwen3-32B")
    p.add_argument("--out-dir", type=Path, required=True, help=f"directory for {tool}'s files")
    p.add_argument("--summary", type=Path, required=True, help="JSON summary to write")
    a = p.parse_args()
    run = Run(a.exe, a.url, a.concurrency, a.requests or 4 * a.concurrency, a.tokenizer, a.out_dir)
    if run.requests <= run.concurrency:
        p.error(f"--requests {run.requests} leaves no request after the first burst")
    run.out_dir.mkdir(parents=True, exist_ok=True)
    cmd = build(run)
    started = time.perf_counter()  # the server's `arrived` is on the same clock
    with open(run.out_dir / "run.log", "w") as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True, env=os.environ)
    records = [r for r in fetch_truth(run.url) if r["arrived"] >= started]
    result = {
        "tool": tool,
        "version": dist_version(run.exe, dist),
        "concurrency": run.concurrency,
        "requests": run.requests,
        "command": cmd,
        **compare(parse(run), records, run.concurrency),
    }
    run_summary = json.dumps(result, indent=1)
    a.summary.write_text(run_summary + "\n")
    print(run_summary)
