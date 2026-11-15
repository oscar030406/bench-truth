# How we checked the server

The comparisons in [findings.md](findings.md) are only as good as the server's records. [`scripts/wire_check.py`](../scripts/wire_check.py) compares the records of one run with a tcpdump capture of the same run. It measures two delays:

- From the last packet of a request to the start of the server's handler.
- From the recorded send time of a chunk to the first packet that carries it.

The table shows the Rust server 0.2.0 on 4 cores with `bench_truth.reference`, 64 tokens per response and 4 x concurrency requests. The raw numbers are in [`results/wire-2026-11-14.json`](../results/wire-2026-11-14.json).

| Concurrency | Request to handler, median / p99 | Recorded send to packet, median / p99 / max | Server's mean TTFT |
|---|---|---|---|
| 16 | 0.026 / 0.105 ms | 0.002 / 0.039 / 0.412 ms | 51.00 ms |
| 64 | 0.024 / 0.091 ms | 0.002 / 0.028 / 0.285 ms | 51.29 ms |
| 256 | 0.023 / 11.031 ms | 0.002 / 0.018 / 0.275 ms | 51.17 ms |
| 1024 | 0.023 / 0.208 ms | 0.002 / 0.015 / 0.315 ms | 51.09 ms |

The last column is the server's own time from handler start to the first chunk. It stays at 51 ms up to concurrency 1024, so the server is not what slows a client down.

A few requests of the first burst wait longer for the handler. At concurrency 256, the longest wait was 14 ms. The TTFT split of `bench_truth.reference` counts that wait as time before the handler. It is a small part of the first burst's cost. In the same run, the first burst waited 0.64 ms in the server on average. Before the handler, the client measured 14.8 ms for the same requests.

## `TCP_NODELAY`

Before version 0.2.0, the Rust server did not set `TCP_NODELAY`. At concurrency 1024, Nagle's algorithm then held chunks until the client acknowledged the previous chunk. Most chunks (65%) left more than 1 ms after their record, and the worst chunk left 44 ms late.

The first chunk of a response was never held: it left at most 0.09 ms after its record. So the TTFT numbers in [findings.md](findings.md) do not change. Apart from the guidellm run that the clock step explains, the ITL errors there are 0.10% or less. So the held chunks did not change them either.

Version 0.2.0 sets `TCP_NODELAY`. Python's asyncio sets it on every TCP socket, so servers on uvicorn, such as vLLM and SGLang, also send each chunk at once.

## When the servers read the clock

In version 0.2.0, the Python server read the clock after each write returned, so its packets left slightly before their records. Under CPU load, a client on the same machine could read a chunk before the server recorded it. The Rust server read the clock just after it handed a chunk to the HTTP library. From version 0.3.0, both servers read the clock just before the hand-off, so no client can read a chunk before its record.
