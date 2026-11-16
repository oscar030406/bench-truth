"""Start the Python or the Rust server for a test.

The Rust tests need the built binary: set BT_SERVER_BIN, as check.sh does.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(params=["python", "rust"])
def start_server(request):
    """Return a function that starts the server with extra arguments and returns its URL.

    `start.command(*args)` gives the command line, and `start.stop()` stops the
    last server and returns what it wrote to stderr.
    """
    procs: list[subprocess.Popen] = []

    def command(*args: str) -> list[str]:
        if request.param == "python":
            return [sys.executable, "-m", "bench_truth.server", *args]
        exe = os.environ.get("BT_SERVER_BIN")
        if not exe:
            pytest.skip("BT_SERVER_BIN is not set to the Rust server binary")
        # Windows cannot start a relative path with forward slashes, as check.sh passes.
        return [str(Path(exe).resolve()), *args]

    def stop() -> str:
        procs[-1].terminate()
        return procs[-1].communicate(timeout=10)[1].decode()

    def start(*args: str) -> str:
        port = _free_port()
        cmd = command("--port", str(port), *args)
        procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while True:
            try:
                urllib.request.urlopen(f"{url}/v1/models", timeout=1).close()
                return url
            except OSError:
                if procs[-1].poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(procs[-1].stderr.read().decode()) from None
                time.sleep(0.05)

    start.command = command
    start.stop = stop
    yield start
    for p in procs:
        p.terminate()
        p.wait(timeout=10)


@dataclass
class Streamed:
    sent_at: float  # client perf_counter just before the request went out
    chunks: list[tuple[float, dict | str]] = field(default_factory=list)  # (perf_counter, data)


async def _stream(url: str, path: str, body: dict, rid: str | None, keep: int | None) -> Streamed:
    headers = {"x-bt-id": rid} if rid else {}
    async with aiohttp.ClientSession() as session:
        out = Streamed(time.perf_counter())
        async with session.post(f"{url}{path}", json=body, headers=headers) as resp:
            assert resp.status == 200
            async for line in resp.content:
                if not line.startswith(b"data: "):
                    continue
                payload = line[6:].strip().decode()
                data = payload if payload == "[DONE]" else json.loads(payload)
                out.chunks.append((time.perf_counter(), data))
                if keep is not None and len(out.chunks) >= keep:
                    break  # leaving the context closes the connection mid-stream
    return out


def stream(
    url: str,
    body: dict | None = None,
    path: str = "/v1/completions",
    rid: str | None = None,
    keep: int | None = None,
) -> Streamed:
    """Send one streaming request. With `keep`, stop reading after that many chunks."""
    body = {"model": "bench-truth", "prompt": "hi", "stream": True, **(body or {})}
    return asyncio.run(_stream(url, path, body, rid, keep))


def truth(url: str) -> list[dict]:
    with urllib.request.urlopen(f"{url}/truth") as resp:
        return json.load(resp)
