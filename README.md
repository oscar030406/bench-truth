# bench-truth

[![check](https://github.com/oscar030406/bench-truth/actions/workflows/check.yml/badge.svg)](https://github.com/oscar030406/bench-truth/actions/workflows/check.yml)

A streaming server with known timings, for checking the numbers that LLM serving benchmarks report.

Benchmark clients such as `vllm bench serve`, guidellm, aiperf and inference-perf report time to first token (TTFT), inter-token latency (ITL) and throughput. People pick engines and settings from these numbers. bench-truth checks a client against a server with a fixed schedule. The server sends every chunk at a planned time and records when it wrote each one. Each number that the client reports then has a true value to compare with.

## First finding: `trust_env=True` adds up to 26 ms to every request

`vllm bench serve` opens its aiohttp session with `trust_env=True`. With that setting, aiohttp 3.14.5 runs two lookups in the default thread pool for every request ([`client.py` lines 777-817](https://github.com/aio-libs/aiohttp/blob/v3.14.5/aiohttp/client.py#L777-L817)). The `run_in_executor(None, self._get_netrc_auth, ...)` call looks up `~/.netrc`, and the `asyncio.to_thread(get_env_proxy_for_url, url)` call looks up the proxy. aiohttp master skips the netrc lookup when the request already has an `Authorization` header, for example when the user sets `OPENAI_API_KEY`. The runs in this README set no API key. Each request then waits for worker threads and for the event loop before its headers go out. The wait grows with concurrency.

Time from the client sending a request to the server's handler starting, with plain aiohttp 3.14.5 (`bench_truth.reference`), requests after the first burst, 20 ms between tokens:

| Concurrency | `trust_env=False` | `trust_env=True` |
|---|---|---|
| 16 | 0.08 ms | 3.12 ms |
| 64 | 0.07 ms | 7.32 ms |
| 256 | 0.07 ms | 25.89 ms |

The same delay shows in `vllm bench serve` (vLLM `191e5d5`). At concurrency 256, the server's true TTFT is 51.0 ms. vllm bench reports 73.7 ms, 44% too high. With `trust_env` switched off, it reports 51.4 ms. Mean ITL is right in both cases.

The aiohttp report is [aio-libs/aiohttp#14378](https://github.com/aio-libs/aiohttp/issues/14378). [aio-libs/aiohttp#14380](https://github.com/aio-libs/aiohttp/pull/14380) proposes to read the netrc file once per session and to run the proxy lookup on the event loop. The fix for vLLM is in [vllm-project/vllm#67624](https://github.com/vllm-project/vllm/pull/67624): the benchmark trusts the environment only when a proxy or a netrc entry applies to the target.

## evalscope `perf`: the same `trust_env` delay

evalscope's `AioHttpClient` also opens its session with `trust_env=True`. evalscope starts its TTFT clock before it calls `client_session.post`, so the two lookups count in its TTFT. The table gives evalscope's own `Avg TTFT` over all requests, in two runs before the fix and two runs with it, all on evalscope 1.15.0. The fix trusts the environment only when a proxy or a netrc entry applies, in [modelscope/evalscope#2088](https://github.com/modelscope/evalscope/pull/2088).

| `--parallel` | True TTFT | Before | With the fix |
|---|---|---|---|
| 16 | 51.1 to 51.4 ms | 57.5, 56.5 ms | 51.9, 51.7 ms |
| 64 | 51.1 ms | 64.4, 64.2 ms | 52.9, 53.4 ms |
| 256 | 51.0 to 51.2 ms | 144.1, 135.7 ms | 66.5, 67.6 ms |

With the fix, the mean over all requests at `--parallel 256` is still 15 to 17 ms too high. After the first burst, the gap is 11 to 13 ms instead. Of the gap over all requests, 7 ms passes before the request reaches the server. The other 8 to 10 ms is what remains after we subtract the true TTFT and that wait. On main, this remainder is 1 to 2 ms. So it grew with the fix, and we have not found the cause yet. The raw numbers are in [`results/evalscope-2026-11-14.json`](results/evalscope-2026-11-14.json).

## TensorRT-LLM `benchmark_serving`: `trust_env` and `force_close`

TensorRT-LLM's `tensorrt_llm/serve/scripts/benchmark_serving.py` (`178ec0b`) also opens its session with `trust_env=True`, and it starts the TTFT clock before `session.post`. The session also sets `force_close=True`, so every request opens a new connection after the clock starts. We ran a copy of `tensorrt_llm/serve/scripts` without installing TensorRT-LLM, with stubs for three modules that the random dataset does not use. The table gives the benchmark's own `Mean TTFT` over all requests, in two runs per version. The fix keeps `trust_env=True` only when a proxy or a netrc entry applies, in [NVIDIA/TensorRT-LLM#22283](https://github.com/NVIDIA/TensorRT-LLM/pull/22283).

| `--max-concurrency` | True TTFT | Before | With the fix | Fix and `force_close=False` |
|---|---|---|---|---|
| 16 | 51.0 to 51.5 ms | 56.1, 56.1 ms | 52.8, 52.4 ms | 51.8, 52.1 ms |
| 64 | 51.1 to 51.3 ms | 73.6, 68.5 ms | 61.0, 58.7 ms | 56.2, 58.3 ms |
| 256 | 51.0 to 51.2 ms | 114.5, 114.1 ms | 77.9, 75.6 ms | 62.3, 63.4 ms |

The last column shows what `force_close` costs. TensorRT-LLM added `force_close=True` in [NVIDIA/TensorRT-LLM#5815](https://github.com/NVIDIA/TensorRT-LLM/pull/5815), so that the client does not reuse closed connections, and #22283 does not change it. After the first burst at `--max-concurrency 256`, the mean TTFT is 66.1 and 65.6 ms with the fix, and 51.3 and 51.4 ms with `force_close=False`. The raw numbers are in [`results/tensorrt-llm-2026-11-14.json`](results/tensorrt-llm-2026-11-14.json).

## Other benchmark clients

We ran guidellm, aiperf and inference-perf against the Rust server with `--scenario fast --itl 0.02 --word the`. Each run used 4 x concurrency requests with 256 output tokens. The TTFT columns are means over the requests after the first burst. The true TTFT is the server's time from handler start to the first chunk. ITL error compares the client's mean ITL with the server's mean time between chunks. The raw numbers are in [`results/tools-2026-11-14.json`](results/tools-2026-11-14.json).

| Tool | Version | Concurrency | Reported TTFT after first burst (ms) | True TTFT after first burst (ms) | TTFT error | ITL error, all requests | Cause of errors above 5% |
|---|---|---|---|---|---|---|---|
| guidellm | 0.8.1 | 16 | 51.48 | 51.19 | 0.56% | 0.00% | |
| guidellm | 0.8.1 | 64 | 51.18 | 50.89 | 0.56% | 0.00% | |
| guidellm | 0.8.1 | 256 | 51.34 | 50.84 | 0.98% | 14.59% | guidellm times requests with `time.time()`, and the wall clock stepped 3 s forward during 256 requests |
| aiperf | 0.13.0 | 16 | 51.37 | 50.96 | 0.81% | 0.01% | |
| aiperf | 0.13.0 | 64 | 51.44 | 51.07 | 0.74% | 0.02% | |
| aiperf | 0.13.0 | 256 | 51.62 | 50.99 | 1.23% | 0.10% | |
| inference-perf | 0.7.0 | 16 | 51.48 | 51.11 | 0.73% | 0.00% | |
| inference-perf | 0.7.0 | 64 | 51.25 | 51.00 | 0.50% | 0.00% | |
| inference-perf | 0.7.0 | 256 | 51.19 | 50.97 | 0.44% | 0.00% | |

After the first burst, all three clients report TTFT within 1.3% of the truth at every concurrency. None of them has the `trust_env` delay. aiperf and inference-perf use aiohttp 3.14.5 with `trust_env=False`. guidellm uses httpx 0.28.1. httpx reads the proxy variables once, when guidellm builds the client, and does not look up `~/.netrc`.

The guidellm ITL error comes from a step of the wall clock. guidellm stamps every chunk with `time.time()`. In the WSL2 guest of the test machine, the wall clock steps forward every 32.6 s to catch up with the host. Each step is up to about 3 s. A request in flight during a step reports a latency 3 s too long. Its ITL is then about 3 s / 255 = 12 ms too high. At concurrency 256, one wave of 256 requests spanned a step. Without those requests, the guidellm ITL error is 0.001%. Whether a run catches a step depends on its timing. Trial runs at concurrency 16 showed the same error in some runs and not in others. aiperf and inference-perf use `time.perf_counter()`, which does not step. The steps come from the WSL2 guest. On a machine whose wall clock does not step, this error does not appear.

The first burst costs more in every client. At concurrency 256, the true TTFT of the first burst is 50.9 to 51.1 ms in each run. In that burst, guidellm reports 60.9 ms, aiperf 63.4 ms and inference-perf 52.5 ms for the same requests. The summaries of the tools include the first burst. Their mean TTFT at concurrency 256 is 5.7% (guidellm), 7.0% (aiperf) and 1.1% (inference-perf) above the truth.

## SGLang `bench_serving`: a new connection for every request

SGLang's `bench_serving` opens a new aiohttp session for every request, so each request also opens a new connection. The TTFT timer starts before that connection exists. The table gives the mean TTFT of the requests after the first burst, in three runs of SGLang `89386ea`. The fix shares one session across a run, in [sgl-project/sglang#50505](https://github.com/sgl-project/sglang/pull/50505).

| `--max-concurrency` | True TTFT | Before | With the fix |
|---|---|---|---|
| 64 | 51.0 to 51.3 ms | 55.7, 55.6, 55.4 ms | 51.4, 51.4, 51.4 ms |
| 256 | 51.1 to 51.2 ms | 66.2, 66.8, 102.6 ms | 51.5, 51.5, 51.4 ms |

The raw numbers are in [`results/sglang-2026-11-14.json`](results/sglang-2026-11-14.json). `bench_truth.reference --session-per-request` shows the same cost with plain aiohttp.

## LMDeploy `profile_restful_api`: a small cost under Poisson arrivals

LMDeploy's `benchmark/profile_restful_api.py` (`fa67a6b`) also opens a new aiohttp session for every request. It keeps aiohttp's default `trust_env=False`, so the lookups above do not apply. It sends requests as a Poisson process at `--request-rate`. It has no concurrency limit, so the rate sets how many requests are in flight. At 50 requests per second, with about 200 requests in flight, LMDeploy reports a mean TTFT 1.4% to 1.6% above the truth. With one shared session, the error is 0.9%. At 12.4 requests per second, with about 50 in flight, the errors are 1.1% to 1.2% and 0.8%. The gain is less than one percentage point, so we did not open a PR. The raw numbers are in [`results/lmdeploy-2026-11-14.json`](results/lmdeploy-2026-11-14.json).

## How it works

- `server-rs/` is the server, in Rust (tokio and axum). Its own delay to the first token stays at 51 ms up to concurrency 1024, so the server is not what slows a client down. A packet capture checks its records, as the next section shows. `bench_truth/server.py` is a Python version for low concurrency.
- The server speaks the OpenAI streaming API on `/v1/completions` and `/v1/chat/completions`. A scenario fixes the TTFT, the time between tokens and the token count. `--itl` and `--tokens` change one of them. By default chunk i carries the text ` t{i}`. With `--word the`, every chunk carries ` the`, which the Qwen3 tokenizer counts as one token. Use it for clients that count tokens by tokenizing the text. aiperf and inference-perf count about 3.6 tokens per ` t{i}` chunk, so they report ITL 72% too low.
- `/truth` returns one record per request: the `x-bt-id` header if the client sent one, the arrival time on `CLOCK_MONOTONIC`, and the time when the server wrote each chunk. Python's `time.perf_counter()` reads the same clock on Linux. A client on the same machine can therefore split its TTFT into two parts. The first part ends when the server's handler starts, and the second part is the time inside the server.
- `bench_truth.reference` is a minimal aiohttp client. It sends ids, so each of its requests matches a server record. `--session-per-request` opens a new session for every request, as SGLang's `bench_serving` does.
- The server sends the final usage chunk only when the request sets `stream_options.include_usage`, as the OpenAI API does.
- `scripts/trace_vllm_bench.py` runs `vllm bench serve` with an aiohttp trace on every request. `BT_NO_TRUST_ENV=1` switches `trust_env` off.
- `scripts/guidellm_bench.py`, `scripts/aiperf_bench.py` and `scripts/inference_perf_bench.py` run one client, read its per-request output, and compare it with `/truth` (`bench_truth/tools.py`).

## How we checked the server

The comparisons above are only as good as the server's records. `scripts/wire_check.py` compares the records of one run with a tcpdump capture of the same run. It measures two delays:

- From the last packet of a request to the start of the server's handler.
- From the recorded send time of a chunk to the first packet that carries it.

The table shows the Rust server on 4 cores with `bench_truth.reference`, 64 tokens per response and 4 x concurrency requests. The raw numbers are in [`results/wire-2026-11-14.json`](results/wire-2026-11-14.json).

| Concurrency | Request to handler, median / p99 | Recorded send to packet, median / p99 / max |
|---|---|---|
| 16 | 0.026 / 0.105 ms | 0.002 / 0.039 / 0.412 ms |
| 64 | 0.024 / 0.091 ms | 0.002 / 0.028 / 0.285 ms |
| 256 | 0.023 / 11.031 ms | 0.002 / 0.018 / 0.275 ms |
| 1024 | 0.023 / 0.208 ms | 0.002 / 0.015 / 0.315 ms |

A few requests of the first burst wait longer for the handler. At concurrency 256, the longest wait was 14 ms. The TTFT split in this README counts that wait as time before the handler. It is a small part of the first burst's cost. In the same run, the first burst waited 0.64 ms in the server on average. Before the handler, the client measured 14.8 ms for the same requests.

Before version 0.2.0, the Rust server did not set `TCP_NODELAY`. At concurrency 1024, Nagle's algorithm then held chunks until the client acknowledged the previous chunk. Most chunks (65%) left more than 1 ms after their record, and the worst chunk left 44 ms late.

The first chunk of a response was never held: it left at most 0.09 ms after its record. So the TTFT numbers above do not change. Apart from the guidellm run that the clock step explains, the ITL errors above are 0.10% or less. So the held chunks did not change them either.

Version 0.2.0 sets `TCP_NODELAY`. Python's asyncio sets it on every TCP socket, so servers on uvicorn, such as vLLM and SGLang, also send each chunk at once.

## Requirements

- Linux (the clock pairing needs `CLOCK_MONOTONIC` on one machine).
- Python 3.10 or later with `aiohttp`.
- Rust 1.85 or later, for the server.
- `vllm` in the same environment, to check `vllm bench serve`.

## Install

```bash
git clone https://github.com/oscar030406/bench-truth
cd bench-truth
pip install -e .
(cd server-rs && cargo build --release)
```

Instead of the Rust build, you can download the static server binary for x86-64 Linux from a release:

```bash
curl -sL https://github.com/oscar030406/bench-truth/releases/download/v0.2.0/bench-truth-server-x86_64-linux.tar.gz | tar xz
./bench-truth-server --scenario fast --port 8100
```

## Usage

Start the server on four cores, and run the client on others, so the two do not compete:

```bash
taskset -c 0-3 server-rs/target/release/bench-truth-server --scenario fast --itl 0.02 --port 8100 &
taskset -c 4-11 python -m bench_truth.reference --concurrency 256
taskset -c 4-11 python -m bench_truth.reference --concurrency 256 --trust-env
```

Each run prints the client's TTFT, split into the time before the server's handler and the server's own time to the first token. The first line reports the first burst of requests apart from the rest. The first burst opens all its connections at once, and that cost belongs to start-up, not to the steady state.

To check another client, start a new server, run the client against it, and then print what the server sent:

```bash
python -m bench_truth.truth --last 1024 --concurrency 256
```

`--last` keeps the last 1024 requests by arrival time. This drops the warmup and test requests that many clients send first. `--concurrency` summarises the first burst apart from the rest. Compare `mean_ttft_ms` and `mean_itl_ms` with the numbers that the client reports.

## Scenarios

| Name | TTFT | Time between tokens | Tokens | Note |
|---|---|---|---|---|
| `uniform` | 50 ms | 20 ms | 64 | |
| `slow_first` | 500 ms | 10 ms | 64 | |
| `usage_eos` | 50 ms | 20 ms | 64 | `usage.completion_tokens` counts one token more than the stream holds |
| `fast` | 50 ms | 5 ms | 256 | |

With `usage_eos`, `vllm bench serve` reports 1.6% more output tokens and 1.6% lower time per output token, because it takes the count from `usage`. guidellm reports the same kind of error in [guidellm#1565](https://github.com/vllm-project/guidellm/issues/1565).

## Tests

Run `bash check.sh` on Linux, with `uv` and `cargo` installed. It runs ruff, builds the Rust server, and runs the same protocol tests against both servers. The tests check the chunk schedule, the `/truth` records, the clock that the arrival time uses, and the usage chunk.

## Limitations

- The client and the server run on one machine. Network delay between machines is not part of the measurement.
- The results for other clients come from a WSL2 guest. Its monotonic clock ran about 8% slower than the host's (27.6 s against 30.1 s). The server and the clients read the same guest clock, so the relative errors hold. The absolute times are in guest seconds.
- The Rust server's timer has millisecond resolution. Each chunk can leave up to about 1 ms late, and `/truth` records when it actually left.

## License

Apache-2.0
