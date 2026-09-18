"""LightGBM quantile disaggregation — target is each zone's SHARE of the
city total (bounded, scale-free, transfers across cities). Trained on the
calibration-chain output (no per-zone ground truth exists), so this model
is a fast, smooth, uncertainty-aware SURROGATE for the physical chain, not
a discoverer of new information — say exactly that (implementation.md §8.6).
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

FEATURES = [
    "floorspace_m2", "footprint_count", "builtup_ratio", "est_floors",
    "ndvi", "ndbi", "ndwi", "builtup_growth", "population",
    "landuse_frac_residential", "landuse_frac_commercial",
    "landuse_frac_industrial", "road_km", "poi_school", "poi_hospital",
    "poi_shop", "spatial_lag_floorspace", "area_km2",
]


def forecast_spatial(cfg) -> Path:
    import lightgbm as lgb

    zones = gpd.read_parquet(Path(cfg.paths.processed) / "zones.parquet")
    base_demand = pd.read_parquet(Path(cfg.paths.processed) / "base_demand.parquet")

    X = zones[FEATURES].fillna(0.0)
    rows = []
    for resource in cfg.resources:
        bd = base_demand.query("resource == @resource").set_index("zone_id").reindex(zones.zone_id)
        city_total = bd.base_demand.sum()
        y = (bd.base_demand / city_total).fillna(0.0).values  # target: share of city total

        models = {
            q: lgb.LGBMRegressor(
                objective="quantile", alpha=q, n_estimators=400,
                learning_rate=0.05, num_leaves=31, min_child_samples=20, verbose=-1,
            ).fit(X, y)
            for q in cfg.forecast.quantiles
        }
        shares = {q: np.clip(m.predict(X), 0, None) for q, m in models.items()}

        for i, zone_id in enumerate(zones.zone_id):
            rows.append(
                {
                    "zone_id": zone_id,
                    "resource": resource,
                    "share_p10": shares[cfg.forecast.quantiles[0]][i],
                    "share_p50": shares[cfg.forecast.quantiles[1]][i],
                    "share_p90": shares[cfg.forecast.quantiles[2]][i],
                    "city_total": city_total,
                    "unit": cfg.resource_cfg[resource]["unit"],
                }
            )

    out_df = pd.DataFrame(rows)
    p10_le_p50 = (out_df.share_p10 <= out_df.share_p50).all()
    p50_le_p90 = (out_df.share_p50 <= out_df.share_p90).all()

    out = Path(cfg.paths.interim) / "spatial_shares.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_parquet(out)
    print(
        f"forecast spatial: {len(out_df)} rows -> {out}  "
        f"[gate: p10<=p50<=p90 -> {'OK' if p10_le_p50 and p50_le_p90 else 'FAIL'}]"
    )
    return out
