"""Check the server's /truth records against a packet capture of the same run.

The server records when it hands a chunk to its HTTP stack, and when its handler
starts. This script finds in a tcpdump capture when each request finished arriving
and when each chunk actually crossed the loopback device. It reports two delays:

- arrival: from the last request packet to the handler start (`arrived`).
- chunk: from the recorded send time (`arrived + sent[i]`) to the packet with chunk i.

The capture uses the wall clock and the records use CLOCK_MONOTONIC. Pass their
difference (time.time() - time.monotonic(), read just before the run) as --offset.

    sudo tcpdump -i lo -s 0 -w run.pcap tcp port 8100 &
    python -m bench_truth.reference --concurrency 64
    curl -s http://127.0.0.1:8100/truth > truth.json
    python scripts/wire_check.py run.pcap truth.json --offset 1794640000.123456

Use the default chunk text (no --word): the script finds chunk i by its text " t{i}".
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import struct
from collections import defaultdict

ID = re.compile(rb"x-bt-id: ([^\r\n]+)", re.IGNORECASE)
CHUNK = re.compile(rb'"(?:text|content)": ?" t(\d+)"')


def packets(path: str, port: int):
    """Yield (time, client_port, from_server, payload) for TCP packets with data."""
    with open(path, "rb") as f:
        head = f.read(24)
        magic = struct.unpack("<I", head[:4])[0]
        if magic not in (0xA1B2C3D4, 0xA1B23C4D):
            raise ValueError("expected a little-endian pcap file")
        scale = 1e-6 if magic == 0xA1B2C3D4 else 1e-9
        if struct.unpack("<I", head[20:24])[0] != 1:
            raise ValueError("expected an Ethernet capture, as tcpdump -i lo writes")
        while rec := f.read(16):
            sec, frac, incl, _ = struct.unpack("<IIII", rec)
            frame = f.read(incl)
            ip = frame[14:]
            if ip[0] >> 4 != 4 or ip[9] != 6:
                continue
            total = struct.unpack("!H", ip[2:4])[0]
            tcp = ip[(ip[0] & 15) * 4 : total]
            src, dst = struct.unpack("!HH", tcp[:4])
            payload = tcp[(tcp[12] >> 4) * 4 :]
            if payload and port in (src, dst):
                yield sec + frac * scale, dst if src == port else src, src == port, payload


def wire_times(path: str, port: int) -> dict[str, dict]:
    """Per request id: the end of its request and the first packet of each chunk."""
    current: dict[int, str] = {}  # connection (client port) -> id of its open request
    request_end: dict[str, float] = {}
    chunks: dict[str, dict[int, float]] = defaultdict(dict)
    for t, conn, from_server, payload in packets(path, port):
        if not from_server:
            if m := ID.search(payload):
                current[conn] = m.group(1).decode().strip()
            rid = current.get(conn)
            if rid is not None and not chunks[rid]:
                request_end[rid] = t  # the last client packet before the first chunk
            continue
        rid = current.get(conn)
        if rid is None:
            continue
        for m in CHUNK.finditer(payload):
            chunks[rid].setdefault(int(m.group(1)), t)
    return {rid: {"request_end": request_end[rid], "chunks": chunks[rid]} for rid in request_end}


def check(pcap: str, records: list[dict], offset: float, port: int) -> dict:
    wire = wire_times(pcap, port)
    arrival, chunk, missing = [], [], 0
    for r in records:
        w = wire.get(r["id"])
        if w is None or len(w["chunks"]) != len(r["sent"]):
            missing += 1
            continue
        arrival.append(r["arrived"] + offset - w["request_end"])
        chunk += [w["chunks"][i] - (r["arrived"] + offset + s) for i, s in enumerate(r["sent"])]

    def ms(xs: list[float]) -> dict:
        return {
            "min_ms": min(xs) * 1000,
            "median_ms": statistics.median(xs) * 1000,
            "p99_ms": statistics.quantiles(xs, n=100, method="inclusive")[98] * 1000,
            "max_ms": max(xs) * 1000,
        }

    return {
        "requests": len(arrival),
        "unmatched": missing,
        "arrival": ms(arrival),
        "chunk": ms(chunk),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pcap")
    p.add_argument("truth", help="the JSON that /truth returned for the run")
    p.add_argument("--offset", type=float, required=True, help="time.time() - time.monotonic()")
    p.add_argument("--port", type=int, default=8100)
    a = p.parse_args()
    with open(a.truth) as f:
        records = [r for r in json.load(f) if r["id"] and r["id"] != "warmup"]
    print(json.dumps(check(a.pcap, records, a.offset, a.port), indent=1))


if __name__ == "__main__":
    main()
