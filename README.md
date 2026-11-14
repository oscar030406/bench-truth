# bench-truth

A streaming server with known timings, for checking the numbers that LLM serving benchmarks report.

Benchmark clients such as `vllm bench serve`, guidellm, aiperf and inference-perf report time to first token (TTFT), inter-token latency (ITL) and throughput. People pick engines and settings from these numbers. bench-truth checks a client against a server whose timing is fixed in advance: the server sends every chunk on a schedule and records when it wrote each one, so each reported number has a true value to compare with.

## First finding: `trust_env=True` adds up to 26 ms to every request

`vllm bench serve` opens its aiohttp session with `trust_env=True`. With that setting, aiohttp looks up `~/.netrc` for every request in the default thread pool ([`client.py`](https://github.com/aio-libs/aiohttp/blob/master/aiohttp/client.py), the `run_in_executor(None, self._get_netrc_auth, ...)` call). Each request then waits for a worker thread and for the event loop before its headers go out. The wait grows with concurrency.

Time from the client sending a request to the server's handler starting, with plain aiohttp 3.14.5 (`bench_truth.reference`), requests after the first burst, 20 ms between tokens:

| Concurrency | `trust_env=False` | `trust_env=True` |
|---|---|---|
| 16 | 0.08 ms | 3.12 ms |
| 64 | 0.07 ms | 7.32 ms |
| 256 | 0.07 ms | 25.89 ms |

The same delay shows in `vllm bench serve` (vLLM `191e5d5`). At concurrency 256, the server's true TTFT is 51.0 ms. vllm bench reports 73.7 ms, 44% too high. With `trust_env` switched off, it reports 51.4 ms. Mean ITL is right in both cases.

The aiohttp report is [aio-libs/aiohttp#14378](https://github.com/aio-libs/aiohttp/issues/14378). The fix for vLLM is in [vllm-project/vllm#67608](https://github.com/vllm-project/vllm/pull/67608): the benchmark trusts the environment only when a proxy applies to the target.

## How it works

- `server-rs/` is the server, in Rust (tokio and axum). Its own delay to the first token stays at 51 ms up to concurrency 1024, so the server is not what slows a client down. `bench_truth/server.py` is a Python version for low concurrency.
- The server speaks the OpenAI streaming API on `/v1/completions` and `/v1/chat/completions`. A scenario fixes the TTFT, the time between tokens and the token count. `--itl` and `--tokens` change one of them.
- `/truth` returns one record per request: the `x-bt-id` header if the client sent one, the arrival time on `CLOCK_MONOTONIC`, and the time each chunk was written. Python's `time.perf_counter()` reads the same clock on Linux, so a client on the same machine can split its TTFT into time before the server's handler and time inside the server.
- `bench_truth.reference` is a minimal aiohttp client. It sends ids, so each of its requests matches a server record.
- `scripts/trace_vllm_bench.py` runs `vllm bench serve` with an aiohttp trace on every request. `BT_NO_TRUST_ENV=1` switches `trust_env` off.

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

- Version 0.1.0 checks only `vllm bench serve`. guidellm, aiperf and inference-perf are not checked.
- The client and the server run on one machine. Network delay between machines is not part of the measurement.
- The Rust server's timer has millisecond resolution. Each chunk can leave up to about 1 ms late, and `/truth` records when it actually left.

## License

Apache-2.0
