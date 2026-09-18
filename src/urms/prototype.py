"""Prototype pipeline — end to end from data already on disk, in seconds.

Uses the H3 grid + OSM layers (landuse, roads, POIs, infra). If
buildings.parquet exists, population is distributed by footprint area;
otherwise by an OSM density proxy (residential area, road length, POIs),
and every output says so. Demand coefficients come from
config/resources/*.yaml exactly as in the full pipeline. The P10-P90 band
is a flat assumed band here, not a fitted quantile model.

    python -m urms.cli prototype
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from urms.capacity.gap import _minmax_norm
from urms.capacity.infra import _default_capacity
from urms.serve.export import ATTRIBUTION
from urms.zones.features import _add_osm_features

BAND = 0.15  # assumed +/- demand uncertainty for the prototype


def _population_weights(zones: gpd.GeoDataFrame, cfg) -> tuple[pd.Series, str]:
    bpath = Path(cfg.paths.processed) / "buildings.parquet"
    if bpath.exists():
        import h3
        b = pd.read_parquet(bpath, columns=["centroid_lat", "centroid_lon", "area_m2"])
        b = b[b.area_m2 >= cfg.zones.min_building_area_m2]
        b["zone_id"] = [h3.latlng_to_cell(la, lo, cfg.zones.h3_res_model)
                        for la, lo in zip(b.centroid_lat, b.centroid_lon)]
        roof = zones.zone_id.map(b.groupby("zone_id").area_m2.sum()).fillna(0.0)
        res = zones.landuse_frac_residential.fillna(0).clip(lower=0.15)
        return roof * res, "building footprints (VIDA) x residential share"

    res_area = zones.landuse_frac_residential.fillna(0) * zones.area_km2
    pois = zones[["poi_school", "poi_hospital", "poi_shop"]].sum(axis=1)
    w = 0.5 * _minmax_norm(res_area) + 0.3 * _minmax_norm(zones.road_km.fillna(0)) + 0.2 * _minmax_norm(pois)
    return w, "OSM density proxy (residential area, road length, POIs) — building footprints pending"


def _assets(cfg) -> gpd.GeoDataFrame:
    infra = gpd.read_parquet(Path(cfg.paths.processed) / "infra.parquet").to_crs(cfg.city.utm_crs)
    infra["capacity"] = [
        t if pd.notna(t) else _default_capacity(r, a, cfg.resource_cfg[r])
        for t, r, a in zip(infra.tagged_capacity, infra.resource, infra.asset_type)
    ]
    infra["tagged"] = infra.tagged_capacity.notna()
    return infra


def _capacity(zones_proj: gpd.GeoDataFrame, assets: gpd.GeoDataFrame, resource: str, cfg, drop_top_n: int = 0):
    a = assets[assets.resource == resource].sort_values("capacity", ascending=False).iloc[drop_top_n:]
    n = len(zones_proj)
    cap, n_assets, n_tagged = np.zeros(n), np.zeros(n, int), np.zeros(n, int)
    if a.empty:
        return cap, n_assets, n_tagged
    cx, cy = zones_proj.geometry.centroid.x.values, zones_proj.geometry.centroid.y.values
    radius = cfg.resource_cfg[resource]["capacity"]["service_radius_m"]
    for geom, c, tagged in zip(a.geometry, a.capacity, a.tagged):
        d = np.hypot(cx - geom.x, cy - geom.y)
        m = d < radius
        if not m.any():
            continue
        w = 1.0 / np.clip(d[m], 1.0, None)
        cap[m] += c * w / w.sum()
        n_assets[m] += 1
        n_tagged[m] += int(tagged)
    return cap, n_assets, n_tagged


def _demand(zones: pd.DataFrame, cfg, shocks: dict) -> dict[str, np.ndarray]:
    pop = zones.population.values * shocks.get("population_multiplier", 1.0)
    dT = shocks.get("temperature_delta_c", 0.0)
    commercial = zones.landuse_frac_commercial.fillna(0).values
    festival = shocks.get("landuse_multiplier", {}).get("commercial", 1.0)
    event = 1 + (festival - 1) * commercial

    out = {}
    w = cfg.resource_cfg["water"]
    mult = np.ones(len(zones))
    for lu, col in (("commercial", "landuse_frac_commercial"), ("industrial", "landuse_frac_industrial"),
                    ("institutional", "landuse_frac_institutional")):
        mult += zones[col].fillna(0).values * (w["non_domestic_multipliers"][lu] - 1)
    nrw = shocks.get("water_nrw_fraction", w["losses"]["nrw_fraction"])
    heat_w = 1 + w["temporal"]["weather_sensitivity"]["temp_coeff_per_degc"] * dT
    out["water"] = pop * w["per_capita"]["domestic_lpcd"] * mult / (1 - nrw) * heat_w * event

    e = cfg.resource_cfg["electricity"]
    kwh = e["per_capita"]["annual_kwh_per_capita"] or e["per_capita"]["fallback_annual_kwh_per_capita"]
    peak = max(e["temporal"]["diurnal_profile"])
    heat_e = 1 + e["temporal"]["weather_sensitivity"]["temp_coeff_per_degc"] * dT
    out["electricity"] = pop * kwh / 8760.0 * peak * heat_e * event  # peak-hour kWh/h

    k = cfg.resource_cfg["waste"]["per_capita"]["by_state_g_per_day"]
    g = k.get(cfg.city.state_code, k["IN"])
    out["waste"] = pop * g / 1000.0 * event
    return out


def _gap_rows(zones: pd.DataFrame, demand: dict, caps: dict, cfg) -> pd.DataFrame:
    """Per-zone rows for the map — urban zones only (city totals use every zone)."""
    urban = (zones.population / zones.area_km2 >= cfg.zones.urban_min_density_per_km2).values
    frames = []
    for r in cfg.resources:
        cap, n_assets, n_tagged = caps[r]
        d50 = demand[r]
        d90 = d50 * (1 + BAND)
        f = pd.DataFrame({
            "zone_id": zones.zone_id.values, "resource": r, "population": zones.population.values,
            "demand_p10": d50 * (1 - BAND), "demand_p50": d50, "demand_p90": d90,
            "capacity": cap, "n_assets": n_assets,
            "capacity_provenance": np.where((n_assets > 0) & (n_tagged == n_assets), "osm_tagged", "osm_default_assumed"),
            "redundancy": np.minimum(n_assets / 2.0, 1.0),
            "builtup_growth": 0.0,
        })
        f["gap_p90"] = f.demand_p90 - f.capacity
        f["deficit_ratio"] = (f.gap_p90 / f.demand_p90.clip(lower=1e-6)).clip(-1, 1)
        f = f[urban].reset_index(drop=True)
        # No OSM asset within service radius means missing map data, not a
        # confirmed shortage — keep those zones out of the risk ranking.
        mapped = f.n_assets > 0
        nd = _minmax_norm(f.deficit_ratio[mapped]).reindex(f.index)
        terms = pd.DataFrame({"capacity deficit": 0.55 * nd, "low redundancy": 0.20 * (1 - f.redundancy)})
        score = terms.sum(axis=1)
        # Bands are a within-city ranking (top 10% critical, next 20% high,
        # next 30% medium): the map answers "which zones first", not an
        # absolute shortage verdict, since capacities are largely assumed.
        pct = score[mapped].rank(pct=True, method="average").reindex(f.index)
        f["risk_score"] = (100 * pct).round(0)
        f["risk_band"] = np.select(
            [~mapped, pct >= 0.9, pct >= 0.7, pct >= 0.4],
            ["no mapped capacity", "critical", "high", "medium"], default="low")
        f["top_driver"] = np.where(mapped, terms.idxmax(axis=1), "no OSM asset within service radius")
        frames.append(f)
    return pd.concat(frames, ignore_index=True)


def _columnar(df: pd.DataFrame) -> dict:
    """{col: [values]}; NaN becomes null so the browser's JSON.parse accepts it."""
    out = {}
    for c in df.columns:
        s = df[c].round(3) if df[c].dtype.kind == "f" else df[c]
        out[c] = [None if (isinstance(v, float) and np.isnan(v)) else v for v in s.tolist()]
    return out


def run_prototype(cfg) -> Path:
    grid = gpd.read_parquet(Path(cfg.paths.processed) / "zones_grid.parquet")
    zones = grid[grid.zone_type == "h3_8"].reset_index(drop=True)
    zones = _add_osm_features(zones, cfg)

    ref_year = int(cfg.sentinel.date_end[:4])
    target = cfg.city.population_total * (1 + cfg.city.population_growth_rate) ** (ref_year - cfg.city.population_year)
    weights, pop_method = _population_weights(zones, cfg)
    zones["population"] = weights / weights.sum() * target

    zones_proj = zones.to_crs(cfg.city.utm_crs)
    assets = _assets(cfg)
    base_caps = {r: _capacity(zones_proj, assets, r, cfg) for r in cfg.resources}

    build = Path(cfg.paths.build)
    (build / "scenario_gap").mkdir(parents=True, exist_ok=True)
    summaries, totals = [], {}
    for s in cfg.scenarios:
        shocks = s.get("shocks", {})
        caps = base_caps
        if shocks.get("disable_top_n_assets"):
            caps = {r: _capacity(zones_proj, assets, r, cfg, shocks["disable_top_n_assets"]) for r in cfg.resources}
        demand = _demand(zones, cfg, shocks)
        g = _gap_rows(zones, demand, caps, cfg)
        cols = ["zone_id", "resource", "population", "demand_p10", "demand_p50", "demand_p90", "capacity",
                "capacity_provenance", "gap_p90", "risk_score", "risk_band", "top_driver"]
        (build / "scenario_gap" / f"{s['id']}.json").write_text(json.dumps(_columnar(g[cols]), separators=(",", ":")))
        totals[s["id"]] = {r: float(demand[r].sum()) for r in cfg.resources}
        summaries.append({
            "scenario_id": s["id"], "label": s["label"],
            "risk_by_band": {r: g[g.resource == r].risk_band.value_counts().to_dict() for r in cfg.resources},
            "total_demand_p50_by_resource": totals[s["id"]],
        })
        if s["id"] == "baseline":
            (build / "gap.json").write_text(json.dumps(_columnar(g[cols]), separators=(",", ":")))

    wc, kc = cfg.resource_cfg["water"]["climate"], cfg.resource_cfg["waste"]
    d_mld = (totals["baseline"]["water"] - totals.get("nrw_fixed", totals["baseline"])["water"]) / 1e6
    diversion = next((s["shocks"].get("waste_organic_diversion", 0) for s in cfg.scenarios if s["id"] == "organics_diverted"), 0)
    climate = {
        "Fix leaks (NRW 35%→20%)": d_mld * wc["pumping_kwh_per_ml"] * wc["grid_emission_factor_kgco2_per_kwh"] * 365 / 1000,
        "Compost 80% wet waste": totals["baseline"]["waste"] * kc["composition"]["wet_organic"] * diversion
                                 * kc["climate"]["methane_kgco2e_per_kg_organic_landfilled"] * 365 / 1000,
    }

    meta = {
        "city": cfg.city.model_dump(),
        "resources": cfg.resources,
        "units": {r: cfg.resource_cfg[r]["display_unit"] for r in cfg.resources},
        "unit_scale": {r: cfg.resource_cfg[r]["unit_scale"] for r in cfg.resources},
        "demand_basis": {"water": "litres/day", "electricity": "peak-hour kWh/h", "waste": "kg/day"},
        "population_method": pop_method,
        "population_target": target,
        "climate_tco2e_per_year": climate,
        "climate_sources": [wc["ef_source"], kc["climate"]["source"]],
        "attribution": ATTRIBUTION,
        "notice": f"Prototype: population distributed by {pop_method}. Map shows urban zones "
                  f"(≥ {cfg.zones.urban_min_density_per_km2:.0f} people/km², Census of India urban criterion); "
                  "city totals include every zone. Risk bands rank zones within the city — top 10% critical, "
                  "next 20% high, next 30% medium. P10-P90 is an assumed "
                  f"±{int(BAND*100)}% band; capacity from OSM tags where present, otherwise config defaults (ASSUMED).",
    }
    (build / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    (build / "scenarios.json").write_text(json.dumps(summaries, indent=1))

    web_data = Path("web/data")
    if web_data.exists():
        shutil.rmtree(web_data)
    shutil.copytree(build, web_data)

    base = summaries[0]
    print(f"prototype: {len(zones)} zones, population {zones.population.sum():,.0f} ({pop_method})")
    for r in cfg.resources:
        print(f"  {r:<12} total {totals['baseline'][r] * cfg.resource_cfg[r]['unit_scale']:,.1f} "
              f"{cfg.resource_cfg[r]['display_unit']}  bands={base['risk_by_band'][r]}")
    print(f"  climate: " + ", ".join(f"{k} = {v:,.0f} tCO2e/yr" for k, v in climate.items()))
    print(f"  -> {build}/ and web/data/  (run: python -m http.server 8080 -d web)")
    return build
