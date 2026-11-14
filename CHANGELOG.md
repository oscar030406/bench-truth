# Changelog

This file lists the changes in each release. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `bench_truth.reference --session-per-request` opens a new session for every request.
- Results for SGLang `bench_serving` with and without sgl-project/sglang#50505 in `results/sglang-2026-11-14.json`.

### Fixed

- Both servers send the final usage chunk only when the request sets `stream_options.include_usage`. SGLang's `bench_serving` does not set it and failed on the empty `choices` list.

## [0.1.0] - 2026-11-14

### Added

- A Python server and a Rust server that stream on a fixed schedule and record each request on `/truth`.
- Scenarios `uniform`, `slow_first`, `usage_eos` and `fast`, with `--itl`, `--tokens` and `--word` overrides on the Rust server.
- `bench_truth.reference`, a minimal aiohttp client, with `--trust-env`.
- `bench_truth.compare.split_ttft`, which splits a client's TTFT into the first burst and later requests.
- Scripts that run `vllm bench serve`, guidellm, aiperf and inference-perf against the server.
- Results for guidellm 0.8.1, aiperf 0.13.0 and inference-perf 0.7.0 in `results/tools-2026-11-14.json`.

[Unreleased]: https://github.com/oscar030406/bench-truth/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/oscar030406/bench-truth/releases/tag/v0.1.0
