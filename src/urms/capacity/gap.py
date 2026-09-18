"""gap = demand_p90 - capacity; risk = f(gap, growth_rate, redundancy)
(implementation.md §8.7).

  gap_p90       = demand_p90 - capacity_effective
  deficit_ratio = gap_p90 / max(demand_p90, eps)
  risk_score    = 100 * clip(0.55*norm(deficit_ratio)
                           + 0.25*norm(builtup_growth)
                           + 0.20*(1 - redundancy), 0, 1)

Bands: critical >= 70, high 45-70, medium 20-45, low < 20. `top_driver` is
the largest of the three terms — the UI uses it to say *why* a zone is red.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

EPS = 1e-6


def _minmax_norm(s: pd.Series) -> pd.Series:
    lo, hi = s.min(), s.max()
    return (s - lo) / (hi - lo) if hi > lo else pd.Series(0.0, index=s.index)


def _risk_band(score: float) -> str:
    if score >= 70:
        return "critical"
    if score >= 45:
        return "high"
    if score >= 20:
        return "medium"
    return "low"


def build_gap(cfg) -> Path:
    demand = pd.read_parquet(Path(cfg.paths.processed) / "demand.parquet")
    capacity = pd.read_parquet(Path(cfg.paths.processed) / "capacity.parquet")
    zones = gpd.read_parquet(Path(cfg.paths.processed) / "zones.parquet")[
        ["zone_id", "builtup_growth"]
    ]

    demand_p50 = demand.groupby(["zone_id", "resource"]).demand_p50.mean().reset_index()
    demand_p90 = demand.groupby(["zone_id", "resource"]).demand_p90.mean().reset_index()

    g = demand_p50.merge(demand_p90, on=["zone_id", "resource"])
    g = g.merge(capacity, on=["zone_id", "resource"], how="left")
    g = g.merge(zones, on="zone_id", how="left")
    g["capacity"] = g["capacity"].fillna(0.0)
    g["redundancy"] = g["redundancy"].fillna(0.0)
    g["builtup_growth"] = g["builtup_growth"].fillna(0.0)

    g["gap_p50"] = g.demand_p50 - g.capacity
    g["gap_p90"] = g.demand_p90 - g.capacity
    g["deficit_ratio"] = g.gap_p90 / g.demand_p90.clip(lower=EPS)

    norm_deficit = g.groupby("resource").deficit_ratio.transform(_minmax_norm)
    norm_growth = g.groupby("resource").builtup_growth.transform(_minmax_norm)

    g["risk_score"] = 100 * (
        0.55 * norm_deficit + 0.25 * norm_growth + 0.20 * (1 - g.redundancy)
    ).clip(0, 1)
    g["risk_band"] = g.risk_score.apply(_risk_band)

    terms = pd.DataFrame({
        "deficit": 0.55 * norm_deficit,
        "growth": 0.25 * norm_growth,
        "redundancy": 0.20 * (1 - g.redundancy),
    })
    g["top_driver"] = terms.idxmax(axis=1)

    out = Path(cfg.paths.processed) / "gap.parquet"
    g.to_parquet(out)
    band_counts = g.risk_band.value_counts().to_dict()
    gate = len(g.risk_band.unique()) > 1
    print(f"capacity gap: {len(g)} rows -> {out}  bands={band_counts}  [gate: not all one band -> {'OK' if gate else 'CHECK'}]")
    return out
