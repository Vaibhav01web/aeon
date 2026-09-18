"""H3 res 8 (0.74 km², forecasting) + res 9 (0.105 km², display) zoning,
plus OSM ward polygons (admin_level=10) for the municipal audience.

⚠️ THE killer bug: `h3.cell_to_boundary` returns (lat, lng) tuples but
GeoJSON/Shapely need (lng, lat). `cell_polygon` below does the swap — any
other H3->geometry code path must do the same. Render 10 hexes in folium
at hour 6 and *look at them* (implementation.md §18 item 1).

Uses the H3 v4 API only (`latlng_to_cell`, `cell_to_boundary`,
`h3shape_to_cells`, `LatLngPoly`) — v3 names raise AttributeError.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import h3
import pandas as pd
from shapely.geometry import Polygon


def bbox_to_cells(bbox: tuple[float, float, float, float], res: int) -> set[str]:
    lon0, lat0, lon1, lat1 = bbox
    ring = [(lat0, lon0), (lat0, lon1), (lat1, lon1), (lat1, lon0)]  # (lat, lng)
    return h3.h3shape_to_cells(h3.LatLngPoly(ring), res)


def cell_polygon(cell: str) -> Polygon:
    # ⚠️ cell_to_boundary returns (lat, lng). Shapely/GeoJSON need (lng, lat).
    return Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(cell)])


def build_h3_layer(cfg, res: int, layer_label: str) -> gpd.GeoDataFrame:
    cells = bbox_to_cells(cfg.city.bbox, res)
    rows = [
        {
            "zone_id": c,
            "zone_type": layer_label,
            "geometry": cell_polygon(c),
            "area_km2": h3.cell_area(c, unit="km^2"),
        }
        for c in cells
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def build_zones(cfg) -> Path:
    h3_8 = build_h3_layer(cfg, cfg.zones.h3_res_model, "h3_8")
    h3_9 = build_h3_layer(cfg, cfg.zones.h3_res_display, "h3_9")
    layers = [h3_8, h3_9]

    if cfg.zones.use_wards:
        wards_path = Path(cfg.paths.processed) / "wards.parquet"
        if wards_path.exists():
            wards = gpd.read_parquet(wards_path)
            wards["zone_type"] = "ward"
            layers.append(wards)
        else:
            print(
                "zones build: use_wards=true but wards.parquet is missing "
                "(run `acquire osm` first) — continuing with H3-only zoning."
            )

    zones = gpd.GeoDataFrame(pd.concat(layers, ignore_index=True), crs="EPSG:4326")

    out = Path(cfg.paths.processed) / "zones_grid.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    zones.to_parquet(out, schema_version="1.1.0")

    # Gate: every zone area within 5% of h3.cell_area for H3 zones.
    h3_zones = zones[zones.zone_type.isin(["h3_8", "h3_9"])]
    check = h3_zones.apply(
        lambda z: abs(z.area_km2 - h3.cell_area(z.zone_id, "km^2")) / z.area_km2 < 0.05,
        axis=1,
    )
    gate = bool(check.all())
    print(f"zones build: {len(zones)} zones -> {out}  [gate: area_km2 matches h3.cell_area -> {'OK' if gate else 'FAIL'}]")
    if not gate:
        raise AssertionError("zones build gate failed — see implementation.md §8.4")
    return out
