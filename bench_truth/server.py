"""An OpenAI-compatible streaming server whose timings are known.

Each scenario fixes when every chunk is sent. The server records the time just
before it writes each chunk, so a benchmark client's TTFT and inter-token
latency can be compared with the truth at /truth.

    python -m bench_truth.server --scenario uniform --port 8100
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import ctypes
import dataclasses
import json
import math
import sys
import time
from dataclasses import dataclass

from aiohttp import web


@dataclass(frozen=True)
class Scenario:
    ttft: float  # seconds from request arrival to the first token
    itl: float  # seconds between later tokens
    tokens: int
    usage_counts_eos: bool = False  # usage.completion_tokens = tokens + 1


SCENARIOS = {
    "uniform": Scenario(ttft=0.05, itl=0.02, tokens=64),
    "slow_first": Scenario(ttft=0.5, itl=0.01, tokens=64),
    "usage_eos": Scenario(ttft=0.05, itl=0.02, tokens=64, usage_counts_eos=True),
    "fast": Scenario(ttft=0.05, itl=0.005, tokens=256),
}

MAX_BODY = 256 * 2**20  # long prompts and base64 images fit
MAX_RECORDS = 100_000


def output_tokens(body: dict, tokens: int) -> int:
    """The scenario length, capped by max_completion_tokens or max_tokens.

    A value that is not a positive integer counts as unset, as in the Rust server.
    """
    for key in ("max_completion_tokens", "max_tokens"):
        value = body.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return min(tokens, value)
    return tokens


def include_usage(body: dict) -> bool:
    options = body.get("stream_options")
    return isinstance(options, dict) and options.get("include_usage") is True


def _usage(n: int, scenario: Scenario) -> dict:
    completion = n + int(scenario.usage_counts_eos)
    return {"prompt_tokens": 8, "completion_tokens": completion, "total_tokens": 8 + completion}


def _chunk(kind: str, text: str | None, usage: dict | None = None) -> bytes:
    if kind == "chat":
        choice = {"index": 0, "delta": {"content": text} if text else {}, "finish_reason": None}
        obj = "chat.completion.chunk"
    else:
        choice = {"index": 0, "text": text or "", "finish_reason": None}
        obj = "text_completion"
    body = {"id": "bt", "object": obj, "model": "bench-truth", "choices": [] if usage else [choice]}
    if usage:
        body["usage"] = usage
    return f"data: {json.dumps(body)}\n\n".encode()


class Server:
    def __init__(self, scenario: Scenario, word: str | None = None, max_records=MAX_RECORDS):
        self.scenario = scenario
        # With a word, every chunk carries it, so a client that re-tokenizes the text
        # counts one token per chunk. Without it, chunk i carries `t{i}`.
        self.word = word
        # Without a limit, a long run that never clears /truth fills the memory.
        self.records: collections.deque[dict] = collections.deque(maxlen=max_records)
        self.dropped = 0

    def _record(self, record: dict) -> None:
        if len(self.records) == self.records.maxlen:
            self.dropped += 1
        self.records.append(record)

    def _text(self, i: int) -> str:
        return f" {self.word}" if self.word else f" t{i}"

    async def _wait(self, arrived: float, i: int) -> None:
        s = self.scenario
        # Before Python 3.13, asyncio on Windows reads a clock that ticks every
        # 15.6 ms and can wake up to a tick early. Then sleep for the rest.
        while (wait := arrived + s.ttft + i * s.itl - time.perf_counter()) > 0:
            await asyncio.sleep(wait)

    async def stream(self, request: web.Request) -> web.StreamResponse:
        arrived = time.perf_counter()
        rid = request.headers.get("x-bt-id")
        kind = "chat" if request.path.endswith("chat/completions") else "completions"
        try:
            body = json.loads(await request.read())
        except ValueError as e:  # invalid JSON or UTF-8 from the client
            raise web.HTTPBadRequest(text=f"the body is not JSON: {e}") from None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="the body is not a JSON object")
        n = output_tokens(body, self.scenario.tokens)
        if body.get("stream") is not True:
            return await self._whole(kind, arrived, rid, n)
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        sent = []
        try:
            for i in range(n):
                await self._wait(arrived, i)
                # Record the time before the write, so no client can read a chunk
                # before its record. The write wakes a client on the same machine,
                # and under CPU load the client ran first.
                sent.append(time.perf_counter() - arrived)
                await resp.write(_chunk(kind, self._text(i)))
            # Like the OpenAI API, send the usage chunk only when the client asks for it.
            if include_usage(body):
                await resp.write(_chunk(kind, None, _usage(n, self.scenario)))
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
        except ConnectionResetError:  # the client went away; no record
            return resp
        self._record({"id": rid, "arrived": arrived, "tokens": n, "sent": sent})
        return resp

    async def _whole(self, kind: str, arrived: float, rid: str | None, n: int) -> web.Response:
        """Without stream: true, the whole response leaves when its last token is due."""
        await self._wait(arrived, n - 1)
        text = "".join(self._text(i) for i in range(n))
        finish = "length" if n < self.scenario.tokens else "stop"
        if kind == "chat":
            choice, obj = {"message": {"role": "assistant", "content": text}}, "chat.completion"
        else:
            choice, obj = {"text": text}, "text_completion"
        out = {
            "id": "bt",
            "object": obj,
            "created": int(time.time()),
            "model": "bench-truth",
            "choices": [{"index": 0, **choice, "finish_reason": finish}],
            "usage": _usage(n, self.scenario),
        }
        sent = [time.perf_counter() - arrived]
        self._record({"id": rid, "arrived": arrived, "tokens": n, "sent": sent})
        return web.json_response(out)

    async def truth(self, request: web.Request) -> web.Response:
        headers = {"x-bt-dropped": str(self.dropped)}
        return web.json_response(list(self.records), headers=headers)

    async def clear(self, request: web.Request) -> web.Response:
        """Forget the records, so that the next run starts with an empty /truth."""
        self.records.clear()
        self.dropped = 0
        return web.Response(status=204)

    async def models(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"object": "list", "data": [{"id": "bench-truth", "object": "model"}]}
        )

    async def health(self, request: web.Request) -> web.Response:
        return web.Response()

    def app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_post("/v1/chat/completions", self.stream)
        app.router.add_post("/v1/completions", self.stream)
        app.router.add_get("/v1/models", self.models)
        app.router.add_get("/health", self.health)
        app.router.add_get("/truth", self.truth)
        app.router.add_delete("/truth", self.clear)
        return app


def _seconds(text: str) -> float:
    value = float(text)
    if not (math.isfinite(value) and value >= 0):
        raise argparse.ArgumentTypeError(f"{text} is not a time of 0 seconds or more")
    return value


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text} is not 1 or more")
    return value


def main(prog: str | None = None) -> None:
    p = argparse.ArgumentParser(prog=prog)
    p.add_argument("--scenario", choices=sorted(SCENARIOS), default="uniform")
    p.add_argument(
        "--ttft", type=_seconds, help="seconds to the first token, overrides the scenario"
    )
    p.add_argument("--itl", type=_seconds, help="seconds between tokens, overrides scenario")
    p.add_argument("--tokens", type=_positive, help="tokens per response, overrides scenario")
    p.add_argument("--word", help="send this word in every chunk instead of t{i}")
    p.add_argument(
        "--max-records",
        type=_positive,
        default=MAX_RECORDS,
        help="keep only the newest records in /truth; the header x-bt-dropped counts the rest",
    )
    p.add_argument("--host", default="127.0.0.1", help="0.0.0.0 accepts clients on other machines")
    p.add_argument("--port", type=int, default=8100)
    args = p.parse_args()
    given = {k: v for k in ("ttft", "itl", "tokens") if (v := getattr(args, k)) is not None}
    scenario = dataclasses.replace(SCENARIOS[args.scenario], **given)
    server = Server(scenario, args.word, args.max_records)
    if sys.platform == "win32":
        # Windows wakes timers every 15.6 ms by default, so chunks would leave up to
        # 16 ms late. Ask for 1 ms timers for this process.
        ctypes.WinDLL("winmm").timeBeginPeriod(1)
    web.run_app(server.app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
