"""Run a bench-truth command: `bench-truth serve|truth|reference [options]`."""

import runpy
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
    sys.argv[0] = f"bench-truth {command}"
    runpy.run_module(COMMANDS[command], run_name="__main__")


if __name__ == "__main__":
    main()
