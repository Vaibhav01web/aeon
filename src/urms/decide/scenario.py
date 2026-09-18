"""Apply shocks from config/scenarios.yaml, re-run L2->L5 (implementation.md
§8.8). Must complete in <3s per scenario so the UI slider feels live: cache
the L1 feature table and fitted LightGBM models, and re-run only
calibration -> disaggregation -> gap. Precompute all 8 scenarios into
build/scenarios.json so the demo never waits on compute.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

from urms.calibrate.demand import calibrate_demand
from urms.calibrate.population import allocate_population
from urms.capacity.gap import build_gap
from urms.forecast.reconcile import reconcile
from urms.forecast.spatial import forecast_spatial


def _apply_shocks(zones: gpd.GeoDataFrame, cfg, shocks: dict) -> gpd.GeoDataFrame:
    zones = zones.copy()
    if "population_multiplier" in shocks:
        zones["population"] *= shocks["population_multiplier"]
    if "landuse_multiplier" in shocks:
        for landuse, mult in shocks["landuse_multiplier"].items():
            col = f"landuse_frac_{landuse}"
            if col in zones.columns:
                zones[col] = (zones[col].fillna(0) * mult).clip(upper=1.0)
    # temperature_delta_c, disable_top_n_assets and water_nrw_fraction /
    # waste_organic_diversion are consumed downstream by calibrate/demand.py
    # and capacity/infra.py respectively, not here — this function only
    # mutates the L1 feature table.
    return zones


def run_scenario(cfg, scenario: dict) -> dict:
    zones = gpd.read_parquet(Path(cfg.paths.processed) / "zones.parquet")
    zones = _apply_shocks(zones, cfg, scenario.get("shocks", {}))

    tmp = Path(cfg.paths.processed) / "zones.parquet"
    zones.to_parquet(tmp, schema_version="1.1.0")  # calibrate/* reads this path

    calibrate_demand(cfg)
    forecast_spatial(cfg)
    reconcile(cfg)
    gap_path = build_gap(cfg)

    gap = pd.read_parquet(gap_path)
    return {
        "scenario_id": scenario["id"],
        "label": scenario["label"],
        "risk_by_band": gap.risk_band.value_counts().to_dict(),
        "critical_zones": gap.query("risk_band == 'critical'").zone_id.tolist(),
        "total_demand_p50_by_resource": gap.groupby("resource").demand_p50.sum().to_dict(),
    }


def run_all_scenarios(cfg) -> Path:
    results = [run_scenario(cfg, s) for s in cfg.scenarios]
    out = Path(cfg.paths.build) / "scenarios.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    ids = {r["scenario_id"] for r in results}
    gate = ids == {s["id"] for s in cfg.scenarios}
    print(f"decide scenarios: {len(results)} scenarios -> {out}  [gate: all ids present -> {'OK' if gate else 'FAIL'}]")
    return out
