"""OSM infra -> capacity per zone (implementation.md §8.7).

Capacity comes from `tagged_capacity` (set by acquire/osm.py from OSM tags,
e.g. substation voltage) where present, else the YAML default — flagged
capacity_provenance='osm_default_assumed'. Each asset's capacity is split
across zones within `service_radius_m` by inverse-distance weight.
All capacities are in the resource's demand unit (see config/resources/*.yaml).
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


def _default_capacity(resource: str, asset_type: str, ycfg: dict) -> float:
    cap = ycfg["capacity"]
    if resource == "electricity":
        return cap["default_mva_untagged"] * 1000 * cap.get("n_minus_1_margin", 1.0)  # MVA -> kWh/h
    defaults = cap.get("default_capacity_litres") or cap.get("default_capacity_kg_per_day") or {}
    return float(defaults.get(asset_type, 0.0))


def build_capacity(cfg) -> Path:
    zones = gpd.read_parquet(Path(cfg.paths.processed) / "zones.parquet")
    infra_path = Path(cfg.paths.processed) / "infra.parquet"
    infra = gpd.read_parquet(infra_path) if infra_path.exists() else None
    if infra is None:
        print("capacity infra: infra.parquet missing (run `acquire osm` first) — all zones get zero capacity.")

    centroids = zones.to_crs(cfg.city.utm_crs).geometry.centroid.reset_index(drop=True)
    rows = []
    for resource in cfg.resources:
        ycfg = cfg.resource_cfg[resource]
        radius = ycfg["capacity"]["service_radius_m"]
        n = len(zones)
        cap = np.zeros(n)
        n_assets = np.zeros(n, dtype=int)
        n_tagged = np.zeros(n, dtype=int)
        asset_ids: list[list[str]] = [[] for _ in range(n)]

        assets = infra[infra.resource == resource].to_crs(cfg.city.utm_crs) if infra is not None else []
        for asset in (assets.itertuples() if len(assets) else []):
            dist = centroids.distance(asset.geometry).values
            within = dist < radius
            if not within.any():
                continue
            weight = 1.0 / np.clip(dist[within], 1.0, None)
            weight /= weight.sum()
            tagged = asset.tagged_capacity is not None and not pd.isna(asset.tagged_capacity)
            asset_cap = asset.tagged_capacity if tagged else _default_capacity(resource, asset.asset_type, ycfg)
            cap[within] += weight * asset_cap
            n_assets[within] += 1
            n_tagged[within] += int(tagged)
            for i in np.flatnonzero(within):
                asset_ids[i].append(asset.asset_id)

        for i, zone_id in enumerate(zones.zone_id):
            all_tagged = n_assets[i] > 0 and n_tagged[i] == n_assets[i]
            rows.append({
                "zone_id": zone_id, "resource": resource, "capacity": float(cap[i]),
                "unit": ycfg["unit"], "n_assets": int(n_assets[i]), "asset_ids": asset_ids[i],
                "capacity_provenance": "osm_tagged" if all_tagged else "osm_default_assumed",
                "redundancy": min(n_assets[i] / 2.0, 1.0),
            })

    out = Path(cfg.paths.processed) / "capacity.parquet"
    df = pd.DataFrame(rows)
    df.to_parquet(out)
    tagged_share = (df.capacity_provenance == "osm_tagged").mean() if len(df) else 0
    print(f"capacity infra: {len(df)} rows -> {out}  ({tagged_share:.0%} of zone-resources fully OSM-tagged)")
    return out
