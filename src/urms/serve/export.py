"""Static export to build/ (implementation.md §8.9). Everything the map
needs is precomputed so the demo is always warm and runs with no network.

GeoParquet 1.1 is written with geopandas (schema_version="1.1.0") — DuckDB
does not write spec-compliant 1.1 bbox covering.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

ATTRIBUTION = (
    "Building footprints © Google (Open Buildings v3), © Microsoft (GlobalMLBuildingFootprints), "
    "© OpenStreetMap contributors — merged by VIDA, ODbL 1.0 · Contains modified Copernicus "
    "Sentinel data (2026) · GHSL © European Union 1995-2026, CC BY 4.0; WorldPop, CC BY 4.0 · "
    "Map data © OpenStreetMap contributors, ODbL · Basemap © OpenFreeMap, © OpenMapTiles · "
    "Weather © Open-Meteo, CC BY 4.0 · Routing © Project-OSRM, BSD-2"
)


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, default=str, separators=(",", ":")))


def _columnar(df: pd.DataFrame) -> dict:
    """Compact arrays-not-objects layout: {col: [values...]}."""
    return {c: df[c].tolist() for c in df.columns}


def export(cfg) -> Path:
    processed = Path(cfg.paths.processed)
    build = Path(cfg.paths.build)
    build.mkdir(parents=True, exist_ok=True)

    zones = gpd.read_parquet(processed / "zones.parquet")
    zones.to_parquet(build / "zones.parquet", schema_version="1.1.0")

    gap = pd.read_parquet(processed / "gap.parquet")
    _write_json(build / "gap.json", _columnar(gap.drop(columns=["asset_ids"], errors="ignore")))

    demand = pd.read_parquet(processed / "demand.parquet")
    _write_json(build / "demand.json", _columnar(demand))

    for name in ("allocation", "anomalies"):
        p = processed / f"{name}.parquet"
        if p.exists():
            _write_json(build / f"{name}.json", _columnar(pd.read_parquet(p)))

    routes_path = processed / "routes.parquet"
    if routes_path.exists():
        routes = pd.read_parquet(routes_path)
        _write_json(build / "routes.json", _columnar(routes))

    zones_4326 = zones.to_crs("EPSG:4326")
    zones_4326[["zone_id", "geometry"]].to_file(build / "zones.geojson", driver="GeoJSON")
    if shutil.which("tippecanoe"):
        subprocess.run(
            ["tippecanoe", "-o", str(build / "zones.pmtiles"), "-zg", "--force",
             "-l", "zones", str(build / "zones.geojson")],
            check=True,
        )
    else:
        print("serve export: tippecanoe not installed — skipping zones.pmtiles "
              "(the web map falls back to H3HexagonLayer from gap.json)")

    meta = {
        "city": cfg.city.model_dump(),
        "resources": cfg.resources,
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "units": {r: cfg.resource_cfg[r]["display_unit"] for r in cfg.resources},
        "unit_scale": {r: cfg.resource_cfg[r]["unit_scale"] for r in cfg.resources},
        "attribution": ATTRIBUTION,
        "synthetic_history_notice": "Hourly history reconstructed from published annual/monthly totals — not metered.",
    }
    _write_json(build / "meta.json", meta)

    print(f"serve export: -> {build}/ ({', '.join(sorted(p.name for p in build.iterdir()))})")
    return build
