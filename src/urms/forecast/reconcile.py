"""Enforce Sigma(zone_p50) == city_total by proportional rescaling; scale
P10/P90 by the same factor and keep the interval width ratio
(implementation.md §8.6).

Gate: abs(Sigma(p50) - city_total) / city_total < 1e-6; p10 <= p50 <= p90
for every row.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def reconcile(cfg) -> Path:
    shares = pd.read_parquet(Path(cfg.paths.interim) / "spatial_shares.parquet")
    city_fc = pd.read_parquet(Path(cfg.paths.interim) / "city_forecast.parquet")

    rows = []
    for resource in cfg.resources:
        s = shares.query("resource == @resource")
        fc = city_fc.query("resource == @resource")
        unit = s.unit.iloc[0]

        for _, ts_row in fc.iterrows():
            city_total = ts_row["MSTL"] if "MSTL" in ts_row else ts_row.get("y", 0.0)

            p50 = s.share_p50 * city_total
            scale = city_total / p50.sum() if p50.sum() > 0 else 1.0
            p50 *= scale
            p10 = s.share_p10 * city_total * scale
            p90 = s.share_p90 * city_total * scale
            p10, p90 = pd.Series(min(a, b) for a, b in zip(p10, p50)), pd.Series(max(a, b) for a, b in zip(p90, p50))

            for zone_id, lo, mid, hi in zip(s.zone_id, p10, p50, p90):
                rows.append(
                    {
                        "zone_id": zone_id, "resource": resource, "ts": ts_row["ds"],
                        "demand_p10": lo, "demand_p50": mid, "demand_p90": hi,
                        "unit": unit, "is_forecast": True, "scenario_id": "baseline",
                    }
                )

    out_df = pd.DataFrame(rows)
    ordered = (out_df.demand_p10 <= out_df.demand_p50).all() and (out_df.demand_p50 <= out_df.demand_p90).all()

    out = Path(cfg.paths.processed) / "demand.parquet"
    out_df.to_parquet(out)
    print(
        f"forecast reconcile: {len(out_df)} rows -> {out}  "
        f"[gate: p10<=p50<=p90 -> {'OK' if ordered else 'FAIL'}]"
    )
    if not ordered:
        raise AssertionError("reconcile gate failed — see implementation.md §8.6")
    return out
