"""Hand-computed examples for the comparison code."""

from __future__ import annotations

import pytest

from bench_truth.compare import split_ttft
from bench_truth.tools import Request, late_requests
from bench_truth.truth import measured, summarise


def _rec(arrived: float, sent: list[float]) -> dict:
    return {"id": None, "arrived": arrived, "tokens": len(sent), "sent": sent}


def test_split_ttft_drops_earlier_records_and_splits_the_first_burst():
    records = [
        _rec(5.0, [0.30]),  # arrived third
        _rec(0.0, [0.90]),  # a warmup request before the run: dropped
        _rec(4.0, [0.20]),  # arrived second
        _rec(3.0, [0.10]),  # arrived first
    ]
    bench = [0.15, 0.25, 0.35]  # in submission order
    out = split_ttft(bench, records, concurrency=2)
    assert out["first"] == {
        "bench_ms": pytest.approx(200.0),
        "server_ms": pytest.approx(150.0),
        "n": 2,
    }
    assert out["later"] == {
        "bench_ms": pytest.approx(350.0),
        "server_ms": pytest.approx(300.0),
        "n": 1,
    }


def test_late_requests_finds_a_clock_step_and_keeps_the_rest():
    records = [_rec(0.0, [0.05, 0.07, 0.09]), _rec(1.0, [0.05, 0.07, 0.09])]
    ordered = [
        Request(start=0.0, ttft=0.05, latency=0.10, itl=0.021),
        Request(start=1.0, ttft=0.05, latency=3.10, itl=1.5),  # 3 s too long: late
    ]
    out = late_requests(ordered, records)
    assert out["late_requests"] == 1
    # On time: reported 21 ms against a true (0.09 - 0.05) / 2 = 20 ms.
    assert out["itl_error_pct_on_time"] == pytest.approx(5.0)


def test_summarise():
    out = summarise([_rec(0.0, [0.05, 0.07, 0.10]), _rec(1.0, [0.03, 0.04])])
    assert out["requests"] == 2
    assert out["mean_ttft_ms"] == pytest.approx(40.0)  # (50 + 30) / 2
    assert out["mean_itl_ms"] == pytest.approx(20.0)  # gaps 20, 30, 10
    assert out["mean_tpot_ms"] == pytest.approx(17.5)  # (50 / 2 + 10 / 1) / 2
    assert out["output_tokens"] == 5


def test_measured_keeps_the_last_records_by_arrival():
    records = [_rec(5.0, [0.3]), _rec(0.0, [0.9]), _rec(4.0, [0.2]), _rec(3.0, [0.1])]
    assert [r["arrived"] for r in measured(records, last=2)] == [4.0, 5.0]
    assert [r["arrived"] for r in measured(records)] == [0.0, 3.0, 4.0, 5.0]
