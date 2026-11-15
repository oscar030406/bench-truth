# docs

- [findings.md](findings.md): what each benchmark client reported against the truth, and why.
- [accuracy.md](accuracy.md): how a packet capture checks the server's records.
- `assets/`: the chart at the top of the main README.

`scripts/hero_chart.py` draws the chart from the results files. If a results file changes, run it again:

| Source | Product | Command | Check |
|---|---|---|---|
| `results/*.json` | `assets/ttft-c256-light.svg`, `assets/ttft-c256-dark.svg` | `python scripts/hero_chart.py` | `python scripts/hero_chart.py --check`, in `check.sh` |
