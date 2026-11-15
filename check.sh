#!/usr/bin/env bash
# Run every check of bench-truth: lint, format, the README chart, the Rust build,
# and the tests against both servers. Needs uv and cargo on PATH.
#
#   bash check.sh
set -euo pipefail
cd "$(dirname "$0")"
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen python scripts/hero_chart.py --check
cargo fmt --check --manifest-path server-rs/Cargo.toml
cargo clippy --release --quiet --manifest-path server-rs/Cargo.toml -- -D warnings
cargo build --release --quiet --manifest-path server-rs/Cargo.toml
target="${CARGO_TARGET_DIR:-server-rs/target}"
BT_SERVER_BIN="$target/release/bench-truth-server" uv run --frozen pytest -q tests
