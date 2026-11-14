"""An OpenAI-compatible streaming server whose timings are known.

Each scenario fixes when every chunk is sent. The server records the time it
actually wrote each chunk, so a benchmark client's TTFT and inter-token
latency can be compared with the truth at /truth.

    python -m bench_truth.server --scenario uniform --port 8100
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
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
    def __init__(self, scenario: Scenario, word: str | None = None):
        self.scenario = scenario
        # With a word, every chunk carries it, so a client that re-tokenizes the text
        # counts one token per chunk. Without it, chunk i carries `t{i}`.
        self.word = word
        self.records: list[dict] = []

    async def stream(self, request: web.Request) -> web.StreamResponse:
        arrived = time.perf_counter()
        rid = request.headers.get("x-bt-id")
        kind = "chat" if request.path.endswith("chat/completions") else "completions"
        body = await request.json()
        s = self.scenario
        n = min(s.tokens, body.get("max_tokens") or s.tokens)
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        sent = []
        for i in range(n):
            target = arrived + s.ttft + i * s.itl
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
            await resp.write(_chunk(kind, f" {self.word}" if self.word else f" t{i}"))
            sent.append(time.perf_counter() - arrived)
        # Like the OpenAI API, send the usage chunk only when the client asks for it.
        if (body.get("stream_options") or {}).get("include_usage"):
            usage = {"prompt_tokens": 8, "completion_tokens": n + int(s.usage_counts_eos)}
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            await resp.write(_chunk(kind, None, usage))
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        self.records.append({"id": rid, "arrived": arrived, "tokens": n, "sent": sent})
        return resp

    async def truth(self, request: web.Request) -> web.Response:
        return web.json_response(self.records)

    async def models(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"object": "list", "data": [{"id": "bench-truth", "object": "model"}]}
        )

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.stream)
        app.router.add_post("/v1/completions", self.stream)
        app.router.add_get("/v1/models", self.models)
        app.router.add_get("/truth", self.truth)
        return app


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", choices=sorted(SCENARIOS), default="uniform")
    p.add_argument("--port", type=int, default=8100)
    p.add_argument("--itl", type=float, help="seconds between tokens, overrides the scenario")
    p.add_argument("--tokens", type=int, help="tokens per response, overrides the scenario")
    p.add_argument("--word", help="send this word in every chunk instead of t{i}")
    args = p.parse_args()
    scenario = SCENARIOS[args.scenario]
    if args.itl is not None:
        scenario = dataclasses.replace(scenario, itl=args.itl)
    if args.tokens is not None:
        scenario = dataclasses.replace(scenario, tokens=args.tokens)
    server = Server(scenario, args.word)
    web.run_app(server.app(), host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
