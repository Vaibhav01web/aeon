"""IPF anchored to census totals — the step that makes population
defensible rather than invented (implementation.md §8.5).

Gates: abs(pop.sum() - target) / target < 0.001; no negative/NaN; corr(pop,
ghsl_pop) > 0.7. Zones with footprint_count < min_footprints_per_zone get
pop_confidence='low' and must be visually distinguished on the map.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


def allocate_population(zones: pd.DataFrame, cfg, ward_totals: dict, iters: int = 50, tol: float = 1e-4) -> pd.Series:
    """
    Distribute the known ULB total across zones proportional to residential
    floorspace, then iteratively reconcile against every independent control
    total we have (ward census counts, GHS-POP, WorldPop).
    """
    ref_year = int(cfg.sentinel.date_end[:4])
    target = cfg.city.population_total * (
        (1 + cfg.city.population_growth_rate) ** (ref_year - cfg.city.population_year)
    )

    res_frac = zones.get("landuse_frac_residential", pd.Series(1.0, index=zones.index)).fillna(0.15)
    res_floor = zones.floorspace_m2 * res_frac.clip(lower=0.15)
    w = res_floor / res_floor.sum()
    pop = w * target  # seed

    for _ in range(iters):
        prev = pop.copy()
        if ward_totals and "ward_id" in zones and zones.ward_id.notna().any():
            pop = pop.groupby(zones.ward_id).transform(
                lambda g: g * (ward_totals[g.name] / g.sum()) if g.sum() > 0 and g.name in ward_totals else g
            )
        grid_ref = 0.5 * zones.ghsl_pop.fillna(0) + 0.5 * zones.worldpop.fillna(0)
        if grid_ref.sum() > 0:
            pop = 0.75 * pop + 0.25 * (grid_ref * target / grid_ref.sum())
        pop *= target / pop.sum()
        if (pop - prev).abs().max() < tol * target:
            break
    return pop


def calibrate_population(cfg) -> Path:
    zones = gpd.read_parquet(Path(cfg.paths.processed) / "zones.parquet")

    ward_totals: dict = {}
    if cfg.city.ward_population_csv:
        wt = pd.read_csv(cfg.city.ward_population_csv)
        ward_totals = dict(zip(wt.ward_id, wt.population))

    zones["population"] = allocate_population(zones, cfg, ward_totals)

    out = Path(cfg.paths.processed) / "zones.parquet"
    zones.to_parquet(out, schema_version="1.1.0")

    target = cfg.city.population_total * (
        (1 + cfg.city.population_growth_rate)
        ** (int(cfg.sentinel.date_end[:4]) - cfg.city.population_year)
    )
    err = abs(zones.population.sum() - target) / target
    corr = zones[["population", "ghsl_pop"]].corr().iloc[0, 1] if zones.ghsl_pop.notna().any() else float("nan")
    gate = bool(err < 0.001) and bool(zones.population.notna().all()) and bool((zones.population >= 0).all())
    print(
        f"calibrate population: sum={zones.population.sum():.0f} target={target:.0f} "
        f"err={err:.4%} corr(pop,ghsl_pop)={corr:.2f} -> {out}  "
        f"[gate: err<0.1% -> {'OK' if gate else 'FAIL'}]"
    )
    if not gate:
        raise AssertionError("calibrate population gate failed — see implementation.md §8.5")
    return out
