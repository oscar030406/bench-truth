"""Protocol tests that both servers must pass."""

from __future__ import annotations

import json
import subprocess
import sys
import time

from conftest import stream, truth

# No chunk may leave before its planned time, minus the event loop's 1 ms clock
# resolution. All but one must leave at most LATE_S later. On GitHub's runners,
# 99% of chunks left within 14 ms (results/schedule-2026-11-15.json), and a busy
# runner sometimes held back one chunk for longer.
EARLY_S = 0.001
LATE_S = 0.015


def assert_on_schedule(sent: list[float], ttft: float, itl: float) -> None:
    late = sorted(s - (ttft + itl * i) for i, s in enumerate(sent))
    assert late[0] >= -EARLY_S, late
    assert late[-2] <= LATE_S, late


def test_chunks_follow_the_schedule(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "8")
    stream(url, rid="a")
    (rec,) = truth(url)
    assert rec["tokens"] == len(rec["sent"]) == 8
    assert_on_schedule(rec["sent"], ttft=0.05, itl=0.02)  # uniform: TTFT 50 ms, then 20 ms


def test_ttft_and_itl_overrides(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "4", "--ttft", "0.1", "--itl", "0.04")
    stream(url)
    (rec,) = truth(url)
    assert_on_schedule(rec["sent"], ttft=0.1, itl=0.04)


def test_arrival_is_on_the_client_monotonic_clock(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "2")
    got = stream(url, rid="clock")
    (rec,) = truth(url)
    first_seen = got.chunks[0][0]
    # The request arrives after the client sent it, and the first chunk leaves
    # the server before the client reads it.
    assert got.sent_at <= rec["arrived"] <= first_seen
    assert rec["arrived"] + rec["sent"][0] <= first_seen


def test_record_keeps_id_and_max_tokens_caps_the_stream(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "8")
    got = stream(url, {"max_tokens": 3}, rid="capped")
    (rec,) = truth(url)
    assert rec["id"] == "capped"
    assert rec["tokens"] == len(rec["sent"]) == 3
    texts = [c for _, c in got.chunks if c != "[DONE]"]
    assert [c["choices"][0]["text"] for c in texts] == [" t0", " t1", " t2"]


def test_usage_chunk_only_when_requested(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "3")
    plain = [c for _, c in stream(url).chunks]
    assert not any("usage" in c for c in plain if c != "[DONE]")
    asked = [c for _, c in stream(url, {"stream_options": {"include_usage": True}}).chunks]
    assert asked[-1] == "[DONE]"
    assert asked[-2]["choices"] == []
    assert asked[-2]["usage"]["completion_tokens"] == 3


def test_usage_eos_counts_one_token_more(start_server):
    url = start_server("--scenario", "usage_eos", "--tokens", "3")
    chunks = [c for _, c in stream(url, {"stream_options": {"include_usage": True}}).chunks]
    assert chunks[-2]["usage"]["completion_tokens"] == 4


def test_chat_chunks_carry_a_delta(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "2")
    body = {"messages": [{"role": "user", "content": "hi"}]}
    chunks = [c for _, c in stream(url, body, path="/v1/chat/completions").chunks]
    assert [c["choices"][0]["delta"]["content"] for c in chunks[:-1]] == [" t0", " t1"]
    assert chunks[0]["object"] == "chat.completion.chunk"


def test_word_replaces_the_chunk_text(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "2", "--word", "the")
    chunks = [c for _, c in stream(url).chunks]
    assert [c["choices"][0]["text"] for c in chunks[:-1]] == [" the", " the"]


def test_cancelled_stream_leaves_no_record(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "20")
    stream(url, rid="gone", keep=1)
    stream(url, rid="done", keep=None)
    time.sleep(0.1)
    assert [r["id"] for r in truth(url)] == ["done"]


def test_truth_cli_drops_earlier_requests_and_splits_the_first_burst(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "4")
    for rid in ("warmup", "a", "b", "c"):
        stream(url, rid=rid)
    cli = [sys.executable, "-m", "bench_truth", "truth", "--url", url, "--last", "3"]
    run = subprocess.run([*cli, "--concurrency", "1"], capture_output=True, text=True, check=True)
    out = json.loads(run.stdout)
    assert [out[k]["requests"] for k in ("all", "first_burst", "after_first_burst")] == [3, 1, 2]
    assert out["all"]["output_tokens"] == 12
    # A first burst as large as the run leaves nothing after it.
    bad = subprocess.run([*cli, "--concurrency", "3"], capture_output=True, text=True)
    assert bad.returncode == 2
    assert "needs more than 3 requests" in bad.stderr
    # --clear empties /truth for the next run.
    subprocess.run([*cli, "--clear"], capture_output=True, check=True)
    assert truth(url) == []
