"""Run `vllm bench serve` with an aiohttp trace on every request.

    python scripts/trace_vllm_bench.py <out.json> <vllm bench serve args...>

Each request gets the time it started, waited for a pooled connection,
had a connection, sent its headers, and received the response headers.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import time

import aiohttp

RECORDS: list[dict] = []


def _mark(name: str):
    async def cb(session, ctx, params):
        ctx.t[name] = time.perf_counter()

    return cb


async def _start(session, ctx, params):
    ctx.t = {"start": time.perf_counter()}
    RECORDS.append(ctx.t)


trace = aiohttp.TraceConfig()
trace.on_request_start.append(_start)
trace.on_connection_queued_start.append(_mark("queued_start"))
trace.on_connection_queued_end.append(_mark("queued_end"))
trace.on_connection_create_end.append(_mark("created"))
trace.on_connection_reuseconn.append(_mark("reused"))
trace.on_request_headers_sent.append(_mark("headers_sent"))
trace.on_request_end.append(_mark("response_headers"))

_init = aiohttp.ClientSession.__init__


def _patched(self, *args, **kwargs):
    kwargs["trace_configs"] = [*kwargs.get("trace_configs", []), trace]
    if "BT_NO_TRUST_ENV" in os.environ:  # experiment: skip the per-request netrc and proxy lookup
        kwargs["trust_env"] = False
    _init(self, *args, **kwargs)


aiohttp.ClientSession.__init__ = _patched
out = sys.argv[1]


def _dump() -> None:
    with open(out, "w") as f:
        json.dump(RECORDS, f)


atexit.register(_dump)
sys.argv = ["vllm", "bench", "serve", *sys.argv[2:]]
from vllm.entrypoints.cli.main import main  # noqa: E402  imported after the patch

main()
