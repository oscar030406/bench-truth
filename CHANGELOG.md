# Changelog

This file lists the changes in each release. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-11-16

### Added

- A request without `"stream": true` gets one JSON body when its last token is due, as the OpenAI API answers. Before, every request got a stream.
- Both servers read `max_completion_tokens`, which chat clients send in place of `max_tokens`.
- `GET /health` returns 200, as guidellm's backend check expects.
- `--max-records` (default 100,000) limits the records that `/truth` keeps. The `x-bt-dropped` header counts the dropped ones, and `bench-truth truth` warns about them. Before, the records grew without limit.
- Release files have build provenance attestations, which `gh attestation verify` checks.
- `results/wire-2026-11-16.json` repeats the packet capture check on 0.4.0. The median chunk leaves 0.005 ms after its record at every concurrency from 16 to 1024.

### Changed

- The Rust server reads the arrival time before the request body, as the Python server does. Before, it counted the upload and parse time of the body as client time.
- Both servers take request bodies up to 256 MiB. Before, the Python server refused bodies over 1 MiB and the Rust server bodies over 2 MiB.
- The Rust server copies the records and writes `/truth` outside the lock, so streams that finish meanwhile do not wait.
- The release workflow gives write access only to the job that publishes the release. Every action is pinned to a commit.

### Fixed

- Both servers treat a `max_tokens` that is not a positive integer as unset. Before, `0`, `-1`, `"2"` and `2.0` gave different results on the two servers, and some made a record with no send times.
- `"stream_options": true` no longer cuts the Python stream before `[DONE]`.
- A body that is not JSON, or not a JSON object, gets 400 on both servers. Before, invalid JSON gave 500 on the Python server.
- The Rust server accepts a body sent with any `Content-Type`, as the Python server does. Before, it answered `text/plain` with 415.
- The Python server keeps no record of a request without `"stream": true` when the client leaves before the response, as the Rust server does.
- `bench-truth truth` exits with "no records" when `/truth` is empty, and prints `null` for ITL and time per output token when no response has two tokens. Before, both cases raised an exception.
- Both servers refuse a negative or infinite `--ttft` or `--itl` and a `--tokens` of 0 at start. The Rust server prints a plain error when its port is in use. Before, it panicked.
- The Python server no longer logs a traceback for each stream that the client closes early.

## [0.3.0] - 2026-11-15

### Added

- Each release has a server binary for Linux, macOS and Windows, on x86-64 and arm64. The release workflow tests each binary on its own platform first.
- CI runs every check on Linux, macOS and Windows, on x86-64 and arm64, with Python 3.14 and the oldest allowed Python, 3.10.
- The `bench-truth` command. `bench-truth serve`, `bench-truth truth` and `bench-truth reference` run the three modules.
- `bench-truth truth --last N` keeps the last N requests by arrival time, to drop warmup and test requests. `--concurrency C` summarises the first C requests apart from the rest.
- `bench-truth truth --clear` and `DELETE /truth` delete the records, so that the next run starts with an empty `/truth`.
- Both servers take `--ttft` and `--host`.
- `scripts/schedule_check.py` measures how late each chunk leaves. `results/schedule-2026-11-15.json` holds its numbers on six GitHub runners.
- `scripts/hero_chart.py` draws the README chart from `results/*.json`. `check.sh` fails when the chart and the results differ.
- Results for evalscope, LMDeploy, TensorRT-LLM, vLLM and InferenceX in `results/`. `results/wire-2026-11-14.json` also gives the server's own mean TTFT in each run.

### Changed

- The README leads with a chart of the TTFT that eight clients report. The report for each client moved to `docs/findings.md`, and the packet capture check moved to `docs/accuracy.md`.
- Both servers record each chunk just before they hand it over for writing. The Rust server recorded it just after the hand-off, and the Python server after the write returned. Under CPU load, a client on the same machine could then read a chunk before its record.
- The Rust server reads its options with clap, so it has `--help` and `--version`.
- `bench-truth truth` prints indented JSON, with times rounded to the microsecond.
- `bench_truth.tools` refuses `--requests` that is not more than `--concurrency`. It reported the numbers of the first burst as the numbers after it.

### Fixed

- The Rust server builds on Windows. It read the clock with `clock_gettime`, which Windows does not have. Now it reads `QueryPerformanceCounter`, like Python's `time.perf_counter()`.
- On macOS, the Rust server reads `CLOCK_UPTIME_RAW`, the clock of `time.perf_counter()`. It read `CLOCK_MONOTONIC`, which also counts the time the Mac was asleep, so `arrived` did not match the client's clock.
- On Windows, both servers ask for 1 ms timers. With the default 15.6 ms timers, chunks left up to 16 ms late.
- The Python server does not send chunks early. Before Python 3.13, asyncio on Windows woke up to 15 ms before the planned time.
- `bench_truth.tools` compares the server's `arrived` with `time.perf_counter()`. It used `time.monotonic()`, which on Windows before Python 3.13 is another clock.

## [0.2.0] - 2026-11-14

### Added

- `bench_truth.reference --session-per-request` opens a new session for every request.
- Results for SGLang `bench_serving` with and without sgl-project/sglang#50505 in `results/sglang-2026-11-14.json`.
- Tests that run the same protocol checks against both servers, `check.sh`, and a GitHub Actions workflow that runs it.
- The Python server takes `--itl`, `--tokens` and `--word`, like the Rust server.
- Each release has a static server binary for x86-64 Linux, built and tested by `.github/workflows/release.yml`.
- `scripts/wire_check.py` compares the server's records with a tcpdump capture of the same run. The results are in `results/wire-2026-11-14.json`.

### Changed

- The Python server listens on 127.0.0.1 only, like the Rust server.

### Fixed

- The Rust server sets `TCP_NODELAY`. Without it, at concurrency 1024, chunks left up to 44 ms after the server recorded them.
- Both servers send the final usage chunk only when the request sets `stream_options.include_usage`. SGLang's `bench_serving` does not set it and failed on the empty `choices` list.

## [0.1.0] - 2026-11-14

### Added

- A Python server and a Rust server that stream on a fixed schedule and record each request on `/truth`.
- Scenarios `uniform`, `slow_first`, `usage_eos` and `fast`, with `--itl`, `--tokens` and `--word` overrides on the Rust server.
- `bench_truth.reference`, a minimal aiohttp client, with `--trust-env`.
- `bench_truth.compare.split_ttft`, which splits a client's TTFT into the first burst and later requests.
- Scripts that run `vllm bench serve`, guidellm, aiperf and inference-perf against the server.
- Results for guidellm 0.8.1, aiperf 0.13.0 and inference-perf 0.7.0 in `results/tools-2026-11-14.json`.

[Unreleased]: https://github.com/oscar030406/bench-truth/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/oscar030406/bench-truth/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/oscar030406/bench-truth/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/oscar030406/bench-truth/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/oscar030406/bench-truth/releases/tag/v0.1.0
