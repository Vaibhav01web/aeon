"""Auto-generate build/validation.md — the honest metrics table
(implementation.md §12). Publish it, including the unflattering rows.
"""

from __future__ import annotations

from pathlib import Path

from urms.validate.checks import run_checks

KNOWN_LIMITATIONS = [
    "No per-zone ground-truth consumption exists in Indian open data; per-zone demand is validated indirectly (city totals, independent gridded population), never directly.",
    "Ward census figures are 2011 — 15 years stale. Growth is applied as a uniform CAGR; the Sentinel-2 change layer is the partial correction.",
    "Capacity is inferred from OSM tags. Indian municipal infrastructure coverage in OSM is uneven; untagged assets use assumed defaults, flagged in the UI.",
    "No free city-level electricity peak-load data exists; peak is derived from state peak x city consumption share.",
    "The footprint dataset is a snapshot whose input imagery spans 2014-2024 and lags current construction.",
    "The hourly series is reconstructed from published annual/monthly totals, not metered.",
]


def write_report(cfg) -> Path:
    results = run_checks(cfg)
    lines = [
        f"# Validation — {cfg.city.name}",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
        *[f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |" for name, ok, detail in results],
        "",
        "## Known limitations",
        "",
        *[f"{i}. {text}" for i, text in enumerate(KNOWN_LIMITATIONS, 1)],
        "",
    ]
    out = Path(cfg.paths.build) / "validation.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    passed = sum(ok for _, ok, _ in results)
    print(f"validate all: {passed}/{len(results)} checks passed -> {out}")
    return out
