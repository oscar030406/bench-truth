"""Request fields, routes and options that both servers must handle the same way."""

from __future__ import annotations

import asyncio
import json
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

import aiohttp
import pytest
from conftest import stream, truth


def post(url: str, body: bytes | dict, path: str = "/v1/completions") -> tuple[int, bytes]:
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    request = urllib.request.Request(f"{url}{path}", data, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.mark.parametrize(
    ("fields", "tokens"),
    [
        ({"max_tokens": 0}, 4),
        ({"max_tokens": -1}, 4),
        ({"max_tokens": "2"}, 4),
        ({"max_tokens": 2.0}, 4),
        ({"max_tokens": True}, 4),
        ({"max_completion_tokens": 2}, 2),
        ({"max_completion_tokens": 3, "max_tokens": 1}, 3),
        ({"stream_options": True}, 4),
    ],
)
def test_only_a_positive_integer_caps_the_output(start_server, fields, tokens):
    url = start_server("--scenario", "uniform", "--tokens", "4", "--itl", "0.001")
    got = stream(url, fields)
    assert got.chunks[-1][1] == "[DONE]"
    (rec,) = truth(url)
    assert rec["tokens"] == len(rec["sent"]) == tokens


def test_request_without_stream_gets_one_body_when_the_last_token_is_due(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "4")
    status, raw = post(url, {"messages": [], "max_tokens": 3}, "/v1/chat/completions")
    assert status == 200
    body = json.loads(raw)
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == " t0 t1 t2"
    assert body["choices"][0]["finish_reason"] == "length"
    assert body["usage"]["completion_tokens"] == 3
    (rec,) = truth(url)
    assert rec["tokens"] == 3
    (sent,) = rec["sent"]
    assert sent >= 0.05 + 2 * 0.02 - 0.001  # TTFT 50 ms, then 2 gaps of 20 ms


def test_long_prompt_and_bad_bodies(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "1")
    prompt = {"prompt": "x" * (3 * 2**20), "stream": True}
    assert post(url, prompt)[0] == 200
    assert post(url, b"{not json")[0] == 400
    assert post(url, b"[1, 2]")[0] == 400
    assert len(truth(url)) == 1


def test_health(start_server):
    url = start_server()
    with urllib.request.urlopen(f"{url}/health") as resp:
        assert resp.status == 200


def test_oldest_records_are_dropped_and_counted(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "1", "--max-records", "2")
    for rid in ("a", "b", "c"):
        stream(url, rid=rid)
    with urllib.request.urlopen(f"{url}/truth") as resp:
        assert resp.headers["x-bt-dropped"] == "1"
        assert [r["id"] for r in json.load(resp)] == ["b", "c"]


@pytest.mark.parametrize("option", [["--ttft=-1"], ["--itl", "inf"], ["--tokens", "0"]])
def test_bad_option_stops_the_server(start_server, option):
    run = subprocess.run(start_server.command(*option), capture_output=True, timeout=30)
    assert run.returncode != 0
    assert b"panicked" not in run.stderr


def test_cancelled_stream_logs_nothing(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "20")
    stream(url, keep=1)
    time.sleep(0.5)
    assert "Traceback" not in start_server.stop()


def test_truth_cli_without_records_or_with_one_token(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "1")
    cli = [sys.executable, "-m", "bench_truth", "truth", "--url", url]
    empty = subprocess.run(cli, capture_output=True, text=True)
    assert empty.returncode == 1
    assert "no records" in empty.stderr
    stream(url)
    out = json.loads(subprocess.run(cli, capture_output=True, text=True, check=True).stdout)
    assert out["requests"] == 1
    assert out["mean_itl_ms"] is None


async def _receive_all(url: str, n: int) -> dict[str, list[float]]:
    async def one(session: aiohttp.ClientSession, rid: str) -> tuple[str, list[float]]:
        body = {"prompt": "hi", "stream": True}
        seen = []
        async with session.post(f"{url}/v1/completions", json=body, headers={"x-bt-id": rid}) as r:
            async for line in r.content:
                if line.startswith(b"data: {"):
                    seen.append(time.perf_counter())
        return rid, seen

    connector = aiohttp.TCPConnector(limit=n)
    async with aiohttp.ClientSession(connector=connector) as session:
        return dict(await asyncio.gather(*(one(session, str(i)) for i in range(n))))


def test_chunks_reach_concurrent_clients_soon_after_their_record(start_server):
    # The schedule tests compare /truth with the plan, so a server that holds
    # chunks after their record passes them. This test fails it. Nagle's
    # algorithm held the chunks of 0.1.0 only at about 1024 clients
    # (docs/accuracy.md), which is too many for CI, so this test does not catch it.
    url = start_server("--scenario", "uniform", "--tokens", "16")
    seen = asyncio.run(_receive_all(url, 32))
    delays = sorted(
        t - (rec["arrived"] + s)
        for rec in truth(url)
        for s, t in zip(rec["sent"], seen[rec["id"]], strict=True)
    )
    assert len(delays) == 32 * 16
    assert delays[0] >= 0  # no client reads a chunk before its record
    assert statistics.median(delays) < 0.005, delays[len(delays) // 2]
    assert delays[int(len(delays) * 0.95)] < 0.020, delays[-20:]


def test_request_without_stream_that_the_client_leaves_has_no_record(start_server):
    url = start_server("--scenario", "uniform", "--tokens", "4", "--ttft", "0.3")
    port = int(url.rsplit(":", 1)[1])
    body = b'{"prompt": "hi"}'
    head = "POST /v1/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.sendall(f"{head}Content-Length: {len(body)}\r\n\r\n".encode() + body)
        time.sleep(0.1)  # leave before the response is due
    time.sleep(0.6)
    assert truth(url) == []
