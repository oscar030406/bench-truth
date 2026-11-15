"""Run a bench-truth command: `bench-truth serve|truth|reference [options]`."""

import importlib
import sys

COMMANDS = {
    "serve": "bench_truth.server",
    "truth": "bench_truth.truth",
    "reference": "bench_truth.reference",
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(f"usage: bench-truth {{{','.join(COMMANDS)}}} [options]")
    command = sys.argv.pop(1)
    importlib.import_module(COMMANDS[command]).main(prog=f"bench-truth {command}")


if __name__ == "__main__":
    main()
