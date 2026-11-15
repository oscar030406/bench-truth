"""Protocol tests that both servers must pass."""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest
from conftest import stream, truth

# Slack for a loaded CI machine. Every chunk must leave after its planned time
# (minus the event loop's 1 ms clock resolution) and at most this much later.
LATE_S = 0.015
EARLY_S = 0.001


def test_chunks_follow_the_schedule(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "8")
    stream(url, rid="a")
    (rec,) = truth(url)
    assert rec["tokens"] == len(rec["sent"]) == 8
    for i, sent in enumerate(rec["sent"]):
        planned = 0.05 + 0.02 * i  # uniform: TTFT 50 ms, then 20 ms between tokens
        assert planned - EARLY_S <= sent <= planned + LATE_S, (i, sent)


def test_itl_override(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "4", "--itl", "0.04")
    stream(url)
    (rec,) = truth(url)
    for i, sent in enumerate(rec["sent"]):
        assert 0.05 + 0.04 * i - EARLY_S <= sent <= 0.05 + 0.04 * i + LATE_S, (i, sent)


@pytest.mark.skipif(sys.platform != "linux", reason="the clock pairing holds on Linux only")
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
    cli = [sys.executable, "-m", "bench_truth.truth", "--url", url, "--last", "3"]
    run = subprocess.run([*cli, "--concurrency", "1"], capture_output=True, text=True, check=True)
    out = json.loads(run.stdout)
    assert [out[k]["requests"] for k in ("all", "first_burst", "after_first_burst")] == [3, 1, 2]
    assert out["all"]["output_tokens"] == 12
    # A first burst as large as the run leaves nothing after it.
    bad = subprocess.run([*cli, "--concurrency", "3"], capture_output=True, text=True)
    assert bad.returncode == 2
    assert "needs more than 3 requests" in bad.stderr
