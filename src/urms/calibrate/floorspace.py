"""footprint area x height -> floorspace (implementation.md §8.5).

zones/features.py already computes a GHSL-derived floorspace_m2 as part of
zonal-stat assembly. This module refines that estimate where OSM tags
`building:levels` on individual footprints: the zone MEDIAN of tagged
buildings is preferred over the GHSL height estimate, and which source was
used is recorded per zone (`floorspace_source`) for the provenance column.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


def refine_floorspace(cfg) -> Path:
    zones = gpd.read_parquet(Path(cfg.paths.processed) / "zones.parquet")
    buildings_path = Path(cfg.paths.processed) / "buildings.parquet"
    buildings = pd.read_parquet(buildings_path)

    if "building_levels" in buildings.columns and buildings.building_levels.notna().any():
        tagged = buildings.dropna(subset=["building_levels"])
        zone_median_floors = tagged.groupby("zone_h3_8").building_levels.median()
        zones["osm_median_floors"] = zones.zone_id.map(zone_median_floors)
    else:
        zones["osm_median_floors"] = np.nan

    use_osm = zones.osm_median_floors.notna()
    zones["est_floors"] = np.where(use_osm, zones.osm_median_floors, zones.est_floors)
    zones["floorspace_m2"] = zones.roof_area_m2 * zones.est_floors
    zones["floorspace_source"] = np.where(use_osm, "osm_tagged_median", "ghsl_height_estimate")

    out = Path(cfg.paths.processed) / "zones.parquet"
    zones.to_parquet(out, schema_version="1.1.0")
    n_osm = int(use_osm.sum())
    print(f"calibrate floorspace: {n_osm}/{len(zones)} zones refined with OSM building:levels -> {out}")
    return out
