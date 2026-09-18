"""Greedy p-median: where should the ULB build the next asset?
(implementation.md §8.8). Repeatedly places the next asset at the
candidate zone that most reduces Sum(risk_score * population).
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

N_CANDIDATES = 5


def _service_reduction(zones: gpd.GeoDataFrame, candidate_idx: int, radius_m: float, weight: np.ndarray) -> float:
    dist = zones.geometry.distance(zones.iloc[candidate_idx].geometry)
    served = dist.values < radius_m
    return float(weight[served].sum())


def site_next_assets(cfg) -> Path:
    zones = gpd.read_parquet(Path(cfg.paths.processed) / "zones.parquet").to_crs(cfg.city.utm_crs)
    gap = pd.read_parquet(Path(cfg.paths.processed) / "gap.parquet")

    rows = []
    for resource in cfg.resources:
        g = gap.query("resource == @resource").set_index("zone_id").reindex(zones.zone_id)
        weight = (g.risk_score.fillna(0) * zones.population.fillna(0)).values
        radius = cfg.resource_cfg[resource]["capacity"]["service_radius_m"]

        remaining_weight = weight.copy()
        chosen = []
        for _ in range(N_CANDIDATES):
            if remaining_weight.sum() <= 0:
                break
            taken = {c[0] for c in chosen}
            reductions = [
                _service_reduction(zones, i, radius, remaining_weight)
                if i not in taken else -1
                for i in range(len(zones))
            ]
            best = int(np.argmax(reductions))
            chosen.append((best, reductions[best]))
            dist = zones.geometry.distance(zones.iloc[best].geometry).values
            remaining_weight[dist < radius] = 0.0

        for rank, (idx, reduction) in enumerate(chosen):
            rows.append({
                "resource": resource, "rank": rank + 1, "zone_id": zones.iloc[idx].zone_id,
                "risk_reduction_estimate": reduction,
            })

    out = Path(cfg.paths.processed) / "siting.parquet"
    pd.DataFrame(rows).to_parquet(out)
    print(f"decide siting: {len(rows)} candidate sites across {cfg.resources} -> {out}")
    return out
