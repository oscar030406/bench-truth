# bench-truth

A streaming server with known timings, for checking the numbers that LLM serving benchmarks report.

Benchmark clients such as `vllm bench serve`, guidellm, aiperf and inference-perf report time to first token (TTFT), inter-token latency (ITL) and throughput. People pick engines and settings from these numbers. bench-truth checks a client against a server with a fixed schedule. The server sends every chunk at a planned time and records when it wrote each one. Each number that the client reports then has a true value to compare with.

## First finding: `trust_env=True` adds up to 26 ms to every request

`vllm bench serve` opens its aiohttp session with `trust_env=True`. With that setting, aiohttp looks up `~/.netrc` for every request in the default thread pool ([`client.py`](https://github.com/aio-libs/aiohttp/blob/master/aiohttp/client.py), the `run_in_executor(None, self._get_netrc_auth, ...)` call). Each request then waits for a worker thread and for the event loop before its headers go out. The wait grows with concurrency.

Time from the client sending a request to the server's handler starting, with plain aiohttp 3.14.5 (`bench_truth.reference`), requests after the first burst, 20 ms between tokens:

| Concurrency | `trust_env=False` | `trust_env=True` |
|---|---|---|
| 16 | 0.08 ms | 3.12 ms |
| 64 | 0.07 ms | 7.32 ms |
| 256 | 0.07 ms | 25.89 ms |

The same delay shows in `vllm bench serve` (vLLM `191e5d5`). At concurrency 256, the server's true TTFT is 51.0 ms. vllm bench reports 73.7 ms, 44% too high. With `trust_env` switched off, it reports 51.4 ms. Mean ITL is right in both cases.

The aiohttp report is [aio-libs/aiohttp#14378](https://github.com/aio-libs/aiohttp/issues/14378). The fix for vLLM is in [vllm-project/vllm#67624](https://github.com/vllm-project/vllm/pull/67624): the benchmark trusts the environment only when a proxy applies to the target.

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

## How it works

- `server-rs/` is the server, in Rust (tokio and axum). Its own delay to the first token stays at 51 ms up to concurrency 1024, so the server is not what slows a client down. `bench_truth/server.py` is a Python version for low concurrency.
- The server speaks the OpenAI streaming API on `/v1/completions` and `/v1/chat/completions`. A scenario fixes the TTFT, the time between tokens and the token count. `--itl` and `--tokens` change one of them. By default chunk i carries the text ` t{i}`. With `--word the`, every chunk carries ` the`, which the Qwen3 tokenizer counts as one token. Use it for clients that count tokens by tokenizing the text. aiperf and inference-perf count about 3.6 tokens per ` t{i}` chunk, so they report ITL 72% too low.
- `/truth` returns one record per request: the `x-bt-id` header if the client sent one, the arrival time on `CLOCK_MONOTONIC`, and the time when the server wrote each chunk. Python's `time.perf_counter()` reads the same clock on Linux. A client on the same machine can therefore split its TTFT into two parts. The first part ends when the server's handler starts, and the second part is the time inside the server.
- `bench_truth.reference` is a minimal aiohttp client. It sends ids, so each of its requests matches a server record. `--session-per-request` opens a new session for every request, as SGLang's `bench_serving` does.
- The server sends the final usage chunk only when the request sets `stream_options.include_usage`, as the OpenAI API does.
- `scripts/trace_vllm_bench.py` runs `vllm bench serve` with an aiohttp trace on every request. `BT_NO_TRUST_ENV=1` switches `trust_env` off.
- `scripts/guidellm_bench.py`, `scripts/aiperf_bench.py` and `scripts/inference_perf_bench.py` run one client, read its per-request output, and compare it with `/truth` (`bench_truth/tools.py`).

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

## Usage

Start the server on four cores, and run the client on others, so the two do not compete:

```bash
taskset -c 0-3 server-rs/target/release/bench-truth-server --scenario fast --itl 0.02 --port 8100 &
taskset -c 4-11 python -m bench_truth.reference --concurrency 256
taskset -c 4-11 python -m bench_truth.reference --concurrency 256 --trust-env
```

Each run prints the client's TTFT, split into the time before the server's handler and the server's own time to the first token. The first line reports the first burst of requests apart from the rest. The first burst opens all its connections at once, and that cost belongs to start-up, not to the steady state.

To check `vllm bench serve`, run it against the server with `--save-detailed`, save `/truth`, and compare with `bench_truth.compare.split_ttft`.

## Scenarios

| Name | TTFT | Time between tokens | Tokens | Note |
|---|---|---|---|---|
| `uniform` | 50 ms | 20 ms | 64 | |
| `slow_first` | 500 ms | 10 ms | 64 | |
| `usage_eos` | 50 ms | 20 ms | 64 | `usage.completion_tokens` counts one token more than the stream holds |
| `fast` | 50 ms | 5 ms | 256 | |

With `usage_eos`, `vllm bench serve` reports 1.6% more output tokens and 1.6% lower time per output token, because it takes the count from `usage`. guidellm reports the same kind of error in [guidellm#1565](https://github.com/vllm-project/guidellm/issues/1565).

## Limitations

- The client and the server run on one machine. Network delay between machines is not part of the measurement.
- The results for other clients come from a WSL2 guest. Its monotonic clock ran about 8% slower than the host's (27.6 s against 30.1 s). The server and the clients read the same guest clock, so the relative errors hold. The absolute times are in guest seconds.
- The Rust server's timer has millisecond resolution. Each chunk can leave up to about 1 ms late, and `/truth` records when it actually left.

## License

Apache-2.0
