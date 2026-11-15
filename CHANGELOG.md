# Changelog

This file lists the changes in each release. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `bench_truth.truth --last N` keeps the last N requests by arrival time, to drop warmup and test requests. `--concurrency C` summarises the first C requests apart from the rest.
- The `bench-truth` command. `bench-truth serve`, `bench-truth truth` and `bench-truth reference` run the three modules.
- `bench-truth truth --clear` and `DELETE /truth` delete the records, so that the next run starts with an empty `/truth`.
- Both servers take `--ttft` and `--host`.
- CI runs every check on Linux, macOS and Windows, on x86-64 and arm64, with Python 3.14 and the oldest allowed Python, 3.10.

### Changed

- The Rust server reads its options with clap, so it has `--help` and `--version`.

### Fixed

- The Rust server builds on Windows. It read the clock with `clock_gettime`, which Windows does not have. Now it reads `QueryPerformanceCounter`, like Python's `time.perf_counter()`.
- On macOS, the Rust server reads `CLOCK_UPTIME_RAW`, the clock of `time.perf_counter()`. It read `CLOCK_MONOTONIC`, which also counts the time the Mac was asleep, so `arrived` did not match the client's clock.
- On Windows, both servers ask for 1 ms timers. With the default 15.6 ms timers, chunks left up to 16 ms late.
- The Python server does not send chunks early. Before Python 3.13, asyncio on Windows woke up to 15 ms before the planned time.
- The Python server records the time of each chunk just before it writes the chunk, like the Rust server. It recorded the time after the write. Under CPU load, a client on the same machine could then read a chunk before its record.

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

[Unreleased]: https://github.com/oscar030406/bench-truth/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/oscar030406/bench-truth/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/oscar030406/bench-truth/releases/tag/v0.1.0
