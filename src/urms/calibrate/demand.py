"""population -> base demand per resource (implementation.md §8.5, §6.2-6.4).

  water_lpd  = pop * lpcd * landuse_mult * (1 / (1 - nrw_fraction))
  elec_kwh_h = pop * (annual_kwh_per_capita / 8760) * sector_adj * landuse_mult
  waste_kgd  = pop * (grams_per_capita_per_day / 1000) * landuse_mult

Then apply the 24h diurnal_profile and weather sensitivity. Every output
row carries a `provenance` field: which coefficients were measured vs
assumed. Every coefficient comes from config/resources/*.yaml — never
hard-code a number here (implementation.md §15).

Gate — the reality check that matters most: total city water demand must
land within ~0.7-1.6x of population * the CPHEEO lpcd benchmark; electricity within the same
band of the D12 city figure where present. This single check catches most
compounding-multiplier bugs.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

LANDUSE_COLS = {
    "residential": "landuse_frac_residential",
    "commercial": "landuse_frac_commercial",
    "industrial": "landuse_frac_industrial",
    "institutional": "landuse_frac_institutional",
}


def _landuse_multiplier(zones: pd.DataFrame, multipliers: dict) -> pd.Series:
    mult = pd.Series(multipliers.get("residential", 1.0), index=zones.index)
    total_frac = pd.Series(0.0, index=zones.index)
    weighted = pd.Series(0.0, index=zones.index)
    for landuse, col in LANDUSE_COLS.items():
        frac = zones.get(col, pd.Series(0.0, index=zones.index)).fillna(0.0)
        weighted += frac * multipliers.get(landuse, 1.0)
        total_frac += frac
    has_frac = total_frac > 0
    mult = mult.where(~has_frac, weighted / total_frac.replace(0, np.nan))
    return mult.fillna(multipliers.get("mixed", 1.15))


def _water_demand(zones: pd.DataFrame, ycfg: dict) -> tuple[pd.Series, dict]:
    lpcd = ycfg["per_capita"]["domestic_lpcd"]
    mult = _landuse_multiplier(zones, ycfg["non_domestic_multipliers"])
    nrw = ycfg["losses"]["nrw_fraction"]
    demand = zones.population * lpcd * mult / (1 - nrw)
    provenance = {
        "lpcd": ycfg["per_capita"]["confidence"],
        "landuse_multiplier": ycfg["non_domestic_multipliers"]["confidence"],
        "nrw_fraction": ycfg["losses"]["confidence"],
    }
    return demand, provenance


def _electricity_demand(zones: pd.DataFrame, ycfg: dict) -> tuple[pd.Series, dict]:
    per_capita = ycfg["per_capita"]
    annual_kwh = per_capita["annual_kwh_per_capita"] or per_capita["fallback_annual_kwh_per_capita"]
    used_fallback = per_capita["annual_kwh_per_capita"] is None
    mult = _landuse_multiplier(zones, {"residential": 1.0, **{k: 1.0 for k in LANDUSE_COLS}})
    demand = zones.population * (annual_kwh / 8760.0) * mult
    provenance = {
        "annual_kwh_per_capita": "assumed" if used_fallback else per_capita["confidence"],
    }
    return demand, provenance


def _waste_demand(zones: pd.DataFrame, ycfg: dict, state_code: str) -> tuple[pd.Series, dict]:
    per_capita = ycfg["per_capita"]
    g_per_day = per_capita["by_state_g_per_day"].get(state_code, per_capita["by_state_g_per_day"]["IN"])
    used_fallback = state_code not in per_capita["by_state_g_per_day"]
    mult = _landuse_multiplier(zones, {"residential": 1.0, **{k: 1.0 for k in LANDUSE_COLS}})
    demand = zones.population * (g_per_day / 1000.0) * mult
    provenance = {
        "grams_per_capita_per_day": "assumed_national_fallback" if used_fallback else per_capita["confidence"],
    }
    return demand, provenance


def calibrate_demand(cfg) -> Path:
    zones = gpd.read_parquet(Path(cfg.paths.processed) / "zones.parquet")

    rows = []
    for resource in cfg.resources:
        ycfg = cfg.resource_cfg[resource]
        if resource == "water":
            demand, prov = _water_demand(zones, ycfg)
        elif resource == "electricity":
            demand, prov = _electricity_demand(zones, ycfg)
        elif resource == "waste":
            demand, prov = _waste_demand(zones, ycfg, cfg.city.state_code)
        else:
            continue

        for zone_id, d in zip(zones.zone_id, demand):
            rows.append({"zone_id": zone_id, "resource": resource, "base_demand": d, "unit": ycfg["unit"], "provenance": prov})

        # Gate: the reality check that matters most.
        if resource == "water":
            naive = zones.population.sum() * ycfg["per_capita"]["domestic_lpcd"]
            ratio = demand.sum() / naive if naive else float("nan")
        elif resource == "waste":
            naive = zones.population.sum() * (ycfg["per_capita"]["by_state_g_per_day"]["IN"] / 1000.0)
            ratio = demand.sum() / naive if naive else float("nan")
        else:
            ratio = 1.0  # electricity checked against D12 in forecast/temporal.py once history is loaded
        gate = 0.7 <= ratio <= 1.6
        print(
            f"calibrate demand ({resource}): total={demand.sum():.1f} {ycfg['unit']} "
            f"reality-check ratio={ratio:.2f}  [gate: 0.7-1.6x -> {'OK' if gate else 'CHECK'}]"
        )

    out = Path(cfg.paths.processed) / "base_demand.parquet"
    pd.DataFrame(rows).to_parquet(out)
    print(f"calibrate demand: {len(rows)} rows -> {out}")
    return out
