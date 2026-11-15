"""Draw the README chart from the results files: the mean TTFT that each client
reports at concurrency 256, before and with the fix, against the true TTFT.

    python scripts/hero_chart.py           # write docs/assets/ttft-c256-{light,dark}.svg
    python scripts/hero_chart.py --check   # exit 1 if the files differ from a new drawing
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets"

# Name, version, results file, the run's version or tool, the version with the fix.
ROWS = [
    ("evalscope perf", "1.15.0", "evalscope", "main 59bb367", "main 59bb367 + PR #2088"),
    (
        "TensorRT-LLM benchmark_serving",
        "178ec0b",
        "tensorrt-llm",
        "178ec0b",
        "178ec0b with the change of NVIDIA/TensorRT-LLM#22283",
    ),
    (
        "InferenceX bench_serving",
        "3370376",
        "inferencex",
        "3370376",
        "3370376 with https://github.com/SemiAnalysisAI/InferenceX/pull/5088 (c1993ee)",
    ),
    ("vllm bench serve", "191e5d5", "vllm", "main 191e5d5", "vllm-project/vllm#67624"),
    ("SGLang bench_serving", "89386ea", "sglang", "89386ea", "sgl-project/sglang#50505"),
    ("aiperf", "0.13.0", "tools", "aiperf", None),
    ("guidellm", "0.8.1", "tools", "guidellm", None),
    ("inference-perf", "0.7.0", "tools", "inference-perf", None),
]

THEMES = {
    "light": {
        "ink": "#0b0b0b",
        "ink2": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "link": "#c3c2b7",
        "surface": "#ffffff",
        "before": "#86b6ef",
        "fixed": "#1c5cab",
    },
    "dark": {
        "ink": "#f0efec",
        "ink2": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "link": "#4a4a46",
        "surface": "#0d1117",
        "before": "#184f95",
        "fixed": "#6da7ec",
    },
}
FONT = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
W, X0, X1, TOP, ROW, MAX_MS = 860, 340, 800, 118, 34, 150


def runs(name: str, key: str) -> list[dict]:
    """The runs at concurrency 256 of one version, or of one tool in tools-*.json."""
    (path,) = (ROOT / "results").glob(f"{name}-*.json")
    found = []
    for r in json.loads(path.read_text(encoding="utf-8"))["runs"]:
        c = r.get("max_concurrency") or r.get("parallel") or r.get("concurrency")
        if c == 256 and key in (r.get("version"), r.get("tool")) and r.get("set") != "reference":
            found.append(r)
    assert found, (name, key)
    return found


def reported(rs: list[dict]) -> float:
    return statistics.mean(r.get("reported_mean_ttft_ms") or r["reported_ttft_ms"] for r in rs)


def data() -> tuple[list[tuple], list[float]]:
    rows, truths = [], []
    for name, version, results, before, fixed in ROWS:
        rs = runs(results, before)
        fs = runs(results, fixed) if fixed else []
        truths += [r["true_ttft_ms"] for r in rs + fs]
        rows.append((name, version, reported(rs), reported(fs) if fs else None))
    return rows, truths


def x(ms: float) -> float:
    return X0 + (X1 - X0) * ms / MAX_MS


def svg(theme: str, rows: list[tuple], truths: list[float]) -> str:
    t = THEMES[theme]
    h = TOP + ROW * len(rows) + 44
    bottom = TOP + ROW * len(rows) - 6
    truth = statistics.mean(truths)
    lo, hi = min(truths), max(truths)
    desc = "; ".join(
        f"{n} {v}: {b:.0f} ms" + (f" before the fix, {f:.0f} ms with it" if f else "")
        for n, v, b, f in rows
    )
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" '
        f'viewBox="0 0 {W} {h}" role="img" aria-labelledby="t d" font-family="{FONT}">',
        "<title id='t'>Mean TTFT that each benchmark client reports at concurrency 256</title>",
        f"<desc id='d'>True TTFT {lo:.1f} to {hi:.1f} ms. {desc}.</desc>",
        f'<text x="16" y="30" font-size="18" font-weight="600" fill="{t["ink"]}">'
        "Mean TTFT that each client reports at concurrency 256</text>",
        f'<text x="16" y="54" font-size="13" fill="{t["ink2"]}">The true mean TTFT was '
        f"{lo:.1f} to {hi:.1f} ms in every run. Each dot is the mean of 1 to 3 runs.</text>",
    ]
    # Legend
    lx = 16
    for color, label in (
        (t["before"], "Before the fix"),
        (t["fixed"], "With the fix, or no fix needed"),
    ):
        out.append(f'<circle cx="{lx + 5}" cy="80" r="5" fill="{color}"/>')
        out.append(f'<text x="{lx + 16}" y="84.5" font-size="13" fill="{t["ink2"]}">{label}</text>')
        lx += 30 + 7.2 * len(label)
    # Grid and axis labels
    for ms in range(0, MAX_MS + 1, 50):
        out.append(
            f'<line x1="{x(ms):.1f}" y1="{TOP - 14}" x2="{x(ms):.1f}" y2="{bottom}" '
            f'stroke="{t["grid"]}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{x(ms):.1f}" y="{bottom + 20}" font-size="12" fill="{t["muted"]}" '
            f'text-anchor="middle">{ms} ms</text>'
        )
    out.append(
        f'<line x1="{x(truth):.1f}" y1="{TOP - 14}" x2="{x(truth):.1f}" y2="{bottom}" '
        f'stroke="{t["ink2"]}" stroke-width="2"/>'
    )
    out.append(
        f'<text x="{x(truth) + 6:.1f}" y="{TOP - 2}" font-size="12" fill="{t["ink2"]}">'
        f"True TTFT, {truth:.0f} ms</text>"
    )
    for i, (name, version, before, fixed) in enumerate(rows):
        y = TOP + ROW * i + ROW / 2
        out.append(
            f'<text x="16" y="{y + 4.5:.1f}" font-size="14" fill="{t["ink"]}">{name} '
            f'<tspan fill="{t["muted"]}">{version}</tspan></text>'
        )
        dots = [(before, t["before"] if fixed else t["fixed"])]
        if fixed:
            out.append(
                f'<line x1="{x(fixed):.1f}" y1="{y}" x2="{x(before):.1f}" y2="{y}" '
                f'stroke="{t["link"]}" stroke-width="2"/>'
            )
            dots.append((fixed, t["fixed"]))
        for ms, color in dots:
            out.append(
                f'<circle cx="{x(ms):.1f}" cy="{y}" r="5" fill="{color}" '
                f'stroke="{t["surface"]}" stroke-width="2"/>'
            )
        out.append(
            f'<text x="{x(before) + 12:.1f}" y="{y + 4.5:.1f}" font-size="12" '
            f'fill="{t["ink2"]}">{before:.0f} ms</text>'
        )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> None:
    rows, truths = data()
    stale = []
    for theme in THEMES:
        path = OUT / f"ttft-c256-{theme}.svg"
        text = svg(theme, rows, truths)
        if "--check" in sys.argv:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.relative_to(ROOT).as_posix())
        else:
            OUT.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        sys.exit(f"{', '.join(stale)} differ from the results files: run scripts/hero_chart.py")


if __name__ == "__main__":
    main()
