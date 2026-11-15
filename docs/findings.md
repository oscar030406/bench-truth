# What bench-truth found

Each section compares one benchmark client with the bench-truth Rust server. The client and the server ran on one machine, a WSL2 Ubuntu 24.04 guest on Windows 11. Each results file lists the exact arguments and versions.

## aiohttp `trust_env=True`: two lookups for every request

With `trust_env=True`, aiohttp 3.14.5 runs two lookups in the default thread pool for every request ([`client.py` lines 777-817](https://github.com/aio-libs/aiohttp/blob/v3.14.5/aiohttp/client.py#L777-L817)). The `run_in_executor(None, self._get_netrc_auth, ...)` call looks up `~/.netrc`, and the `asyncio.to_thread(get_env_proxy_for_url, url)` call looks up the proxy. aiohttp master skips the netrc lookup when the request already has an `Authorization` header, for example when the user sets `OPENAI_API_KEY`. The runs on this page set no API key. Each request then waits for worker threads and for the event loop before its headers go out. The wait grows with concurrency.

Time from the client sending a request to the server's handler starting, with plain aiohttp 3.14.5 (`bench_truth.reference`), requests after the first burst, 20 ms between tokens:

| Concurrency | `trust_env=False` | `trust_env=True` |
|---|---|---|
| 16 | 0.08 ms | 3.12 ms |
| 64 | 0.07 ms | 7.32 ms |
| 256 | 0.07 ms | 25.89 ms |

The aiohttp report is [aio-libs/aiohttp#14378](https://github.com/aio-libs/aiohttp/issues/14378). [aio-libs/aiohttp#14380](https://github.com/aio-libs/aiohttp/pull/14380) proposes to read the netrc file once per session and to run the proxy lookup on the event loop. The clients in the next four sections open their sessions with `trust_env=True` for proxy support. Their fixes keep it only when a proxy or a netrc entry applies to the target.

## vLLM `bench serve`

`vllm bench serve` (vLLM `191e5d5`) opens its session with `trust_env=True`, and it starts the TTFT clock before `session.post`. We ran it at `--max-concurrency 256` with 1024 requests. The table gives two means of its per-request TTFT. Its summary prints the mean over all requests. The last column is the mean over the 768 requests after the first burst. The fix is [vllm-project/vllm#67624](https://github.com/vllm-project/vllm/pull/67624).

| Version | True TTFT | Mean TTFT, all requests | Mean TTFT after the first burst |
|---|---|---|---|
| main `191e5d5` | 51.0 ms | 87.4 ms | 73.7 ms |
| main with `trust_env=False` | 51.1 ms | 60.2 ms | 51.4 ms |
| main with #67624 | 51.1 ms | 62.8 ms | 51.5 ms |

After the first burst, the fix brings the mean from 73.7 ms down to 51.5 ms, against a true 51.1 ms in that run. Over all requests, the error in the mean falls from 36.3 ms to 11.6 ms with the fix. The rest comes from the first burst, which opens 256 connections at once. Mean ITL is 20.01 ms in all three runs. The raw numbers are in [`results/vllm-2026-11-14.json`](../results/vllm-2026-11-14.json).

## evalscope `perf`

evalscope's `AioHttpClient` also opens its session with `trust_env=True`. evalscope starts its TTFT clock before it calls `client_session.post`, so the two lookups count in its TTFT. The table gives evalscope's own `Avg TTFT` over all requests, in two runs before the fix and two runs with it, all on evalscope 1.15.0. The fix trusts the environment only when a proxy or a netrc entry applies, in [modelscope/evalscope#2088](https://github.com/modelscope/evalscope/pull/2088).

| `--parallel` | True TTFT | Before | With the fix |
|---|---|---|---|
| 16 | 51.1 to 51.4 ms | 57.5, 56.5 ms | 51.9, 51.7 ms |
| 64 | 51.1 ms | 64.4, 64.2 ms | 52.9, 53.4 ms |
| 256 | 51.0 to 51.2 ms | 144.1, 135.7 ms | 66.5, 67.6 ms |

With the fix, the mean over all requests at `--parallel 256` is still 15 to 17 ms too high. After the first burst, the gap is 11 to 13 ms instead. Of the gap over all requests, 7 ms passes before the request reaches the server. The other 8 to 10 ms is what remains after we subtract the true TTFT and that wait. On main, this remainder is 1 to 2 ms. So it grew with the fix, and we have not found the cause. The raw numbers are in [`results/evalscope-2026-11-14.json`](../results/evalscope-2026-11-14.json).

## TensorRT-LLM `benchmark_serving`: `trust_env` and `force_close`

TensorRT-LLM's `tensorrt_llm/serve/scripts/benchmark_serving.py` (`178ec0b`) also opens its session with `trust_env=True`, and it starts the TTFT clock before `session.post`. The session also sets `force_close=True`, so every request opens a connection after the clock starts. We ran a copy of `tensorrt_llm/serve/scripts` without installing TensorRT-LLM, with stubs for three modules that the random dataset does not use. The table gives the benchmark's own `Mean TTFT` over all requests, in two runs per version. The fix keeps `trust_env=True` only when a proxy or a netrc entry applies, in [NVIDIA/TensorRT-LLM#22283](https://github.com/NVIDIA/TensorRT-LLM/pull/22283).

| `--max-concurrency` | True TTFT | Before | With the fix | Fix and `force_close=False` |
|---|---|---|---|---|
| 16 | 51.0 to 51.5 ms | 56.1, 56.1 ms | 52.8, 52.4 ms | 51.8, 52.1 ms |
| 64 | 51.1 to 51.3 ms | 73.6, 68.5 ms | 61.0, 58.7 ms | 56.2, 58.3 ms |
| 256 | 51.0 to 51.2 ms | 114.5, 114.1 ms | 77.9, 75.6 ms | 62.3, 63.4 ms |

The last column shows what `force_close` costs. TensorRT-LLM added `force_close=True` in [NVIDIA/TensorRT-LLM#5815](https://github.com/NVIDIA/TensorRT-LLM/pull/5815), so that the client does not reuse closed connections, and #22283 does not change it. After the first burst at `--max-concurrency 256`, the mean TTFT is 66.1 and 65.6 ms with the fix, and 51.3 and 51.4 ms with `force_close=False`. The raw numbers are in [`results/tensorrt-llm-2026-11-14.json`](../results/tensorrt-llm-2026-11-14.json).

## InferenceX `bench_serving`: `trust_env` and a session for every request

InferenceX's `inferencex-e2e/infx/bench_serving` (`3370376`) opens an aiohttp session with `trust_env=True` for every request, and it starts the TTFT clock before `session.post`. So each request waits for the two lookups and then for a connection of its own. We ran it with the arguments of its own `infx/bench/fixed_seq.py`, which send `2 x concurrency` warmup requests before the measured ones. The table gives its `Mean TTFT` over the measured requests, in two runs per version. The fix keeps `trust_env=True` only when a proxy or a netrc entry applies, in [SemiAnalysisAI/InferenceX#5088](https://github.com/SemiAnalysisAI/InferenceX/pull/5088).

| `--max-concurrency` | True TTFT | Before | With the fix | Fix and one shared session |
|---|---|---|---|---|
| 16 | 51.0 to 51.4 ms | 55.0, 56.0 ms | 52.3, 52.9 ms | 51.4, 51.6 ms |
| 64 | 51.0 to 51.6 ms | 64.8, 63.5 ms | 56.5, 56.7 ms | 51.5, 51.4 ms |
| 256 | 51.1 to 51.2 ms | 96.1, 102.0 ms | 72.9, 70.5 ms | 51.4, 51.4 ms |

The last column shows what the session for every request costs. A shared session reuses keep-alive connections. #5088 does not change the sessions and leaves that choice to the maintainers. Mean ITL is 20.00 to 20.02 ms in every run. The raw numbers are in [`results/inferencex-2026-11-15.json`](../results/inferencex-2026-11-15.json).

## SGLang `bench_serving`: a session for every request

SGLang's `bench_serving` opens an aiohttp session for every request, so each request also opens a connection of its own. The TTFT timer starts before that connection exists. The table gives the mean TTFT of the requests after the first burst, in three runs of SGLang `89386ea`. The fix shares one session across a run, in [sgl-project/sglang#50505](https://github.com/sgl-project/sglang/pull/50505).

| `--max-concurrency` | True TTFT | Before | With the fix |
|---|---|---|---|
| 64 | 51.0 to 51.3 ms | 55.7, 55.6, 55.4 ms | 51.4, 51.4, 51.4 ms |
| 256 | 51.1 to 51.2 ms | 66.2, 66.8, 102.6 ms | 51.5, 51.5, 51.4 ms |

The raw numbers are in [`results/sglang-2026-11-14.json`](../results/sglang-2026-11-14.json). `bench_truth.reference --session-per-request` shows the same cost with plain aiohttp.

## LMDeploy `profile_restful_api`: a small cost under Poisson arrivals

LMDeploy's `benchmark/profile_restful_api.py` (`fa67a6b`) also opens an aiohttp session for every request. It keeps aiohttp's default `trust_env=False`, so the lookups above do not apply. It sends requests as a Poisson process at `--request-rate`. It has no concurrency limit, so the rate sets how many requests are in flight. At 50 requests per second, with about 200 requests in flight, LMDeploy reports a mean TTFT 1.4% to 1.6% above the truth. With one shared session, the error is 0.9%. At 12.4 requests per second, with about 50 in flight, the errors are 1.1% to 1.2% and 0.8%. The gain is less than one percentage point, so we did not open a PR. The raw numbers are in [`results/lmdeploy-2026-11-14.json`](../results/lmdeploy-2026-11-14.json).

## guidellm, aiperf and inference-perf

We ran guidellm, aiperf and inference-perf against the Rust server with `--scenario fast --itl 0.02 --word the`. Each run used 4 x concurrency requests with 256 output tokens. The TTFT columns are means over the requests after the first burst. The true TTFT is the server's time from handler start to the first chunk. ITL error compares the client's mean ITL with the server's mean time between chunks. The raw numbers are in [`results/tools-2026-11-14.json`](../results/tools-2026-11-14.json).

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

Without `--word the`, chunk i carries the text ` t{i}`. aiperf and inference-perf count about 3.6 tokens per ` t{i}` chunk with the Qwen3 tokenizer, so they report ITL 72% too low.
