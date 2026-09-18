"""D7-D8 — OSM extract via Geofabrik + osmium (never public Overpass on the
critical path — see implementation.md §4.5 trap #1).

Pipeline: clip India PBF to the city bbox -> `osmium tags-filter` per layer
-> `osmium export` to GeoJSONSeq -> GeoParquet. Writes raw layers only;
aggregation onto zones happens in zones/features.py, because `acquire`
runs before `zones build`.

Outputs in data/processed/: wards.parquet, infra.parquet, landuse.parquet,
roads.parquet, pois.parquet.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

logger = logging.getLogger(__name__)

GEOFABRIK_INDIA = "https://download.geofabrik.de/asia/india-latest.osm.pbf"

# Indian admin_level: ULB = 8, ward = 10. There is no level 9.
ADMIN_LEVEL_WARD = "10"

INFRA_TAGS = {
    "water": {"man_made": ["water_tower", "water_works", "reservoir_covered", "storage_tank"], "landuse": ["reservoir"]},
    "electricity": {"power": ["substation", "transformer", "plant"]},
    "waste": {"amenity": ["waste_transfer_station", "recycling"], "landuse": ["landfill"]},
}

LANDUSE_CLASS = {
    "residential": "residential",
    "commercial": "commercial",
    "retail": "commercial",
    "industrial": "industrial",
    "institutional": "institutional",
    "education": "institutional",
    "religious": "institutional",
}

ROAD_CLASSES = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "living_street", "service",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}

POLYGONAL = ("Polygon", "MultiPolygon")
LINEAR = ("LineString", "MultiLineString")


def download_india_pbf(cfg) -> Path:
    out = Path(cfg.paths.raw) / "india-latest.osm.pbf"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(".pbf.part")
    subprocess.run(["curl", "-L", "--fail", "-C", "-", "-o", str(part), GEOFABRIK_INDIA], check=True)
    part.rename(out)
    return out


def clip_to_city(cfg) -> Path:
    out = Path(cfg.paths.raw) / f"{cfg.city.slug}.osm.pbf"
    if out.exists():
        return out
    india_pbf = download_india_pbf(cfg)
    lon0, lat0, lon1, lat1 = cfg.city.bbox
    part = out.with_suffix(".pbf.part")
    subprocess.run(
        ["osmium", "extract", "--bbox", f"{lon0},{lat0},{lon1},{lat1}",
         "-s", "complete_ways", "-f", "pbf", "-o", str(part), "--overwrite", str(india_pbf)],
        check=True,
    )
    part.rename(out)
    return out


def _export(cfg, city_pbf: Path, filters: list[str], name: str) -> gpd.GeoDataFrame:
    """tags-filter + export one layer. Returns a GeoDataFrame with an `osm_id`
    column (e.g. 'w123', 'r45') and one column per OSM tag."""
    interim = Path(cfg.paths.interim)
    interim.mkdir(parents=True, exist_ok=True)
    filtered = interim / f"osm_{name}.osm.pbf"
    seq = interim / f"osm_{name}.geojsonseq"

    subprocess.run(["osmium", "tags-filter", str(city_pbf), *filters,
                    "-o", str(filtered), "--overwrite"], check=True)
    subprocess.run(["osmium", "export", str(filtered), "-f", "geojsonseq",
                    "-a", "type,id", "-o", str(seq), "--overwrite"], check=True)

    rows = []
    with open(seq) as f:
        for line in f:
            line = line.strip().lstrip("\x1e")  # RFC 8142 record separator
            if not line:
                continue
            feat = json.loads(line)
            props = feat.get("properties") or {}
            osm_type = props.pop("@type", "")
            osm_id = props.pop("@id", "")
            rows.append({"osm_id": f"{osm_type[:1]}{osm_id}", **props,
                         "geometry": shape(feat["geometry"])})

    if not rows:
        return gpd.GeoDataFrame({"osm_id": []}, geometry=gpd.GeoSeries([], crs="EPSG:4326"))
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def _write(gdf: gpd.GeoDataFrame, cfg, name: str) -> Path:
    out = Path(cfg.paths.processed) / f"{name}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(out, schema_version="1.1.0")
    print(f"acquire osm ({name}): {len(gdf)} rows -> {out}")
    return out


def extract_wards(cfg) -> Path | None:
    """admin_level=10 boundaries. Returns None (H3-only zoning) if the city
    has no tagged wards — that path must work (implementation.md §14)."""
    g = _export(cfg, clip_to_city(cfg), [f"r/admin_level={ADMIN_LEVEL_WARD}"], "wards")
    if not g.empty and "boundary" in g.columns:
        # g["boundary"], not g.boundary — the latter is geopandas' geometric boundary.
        g = g[g.geom_type.isin(POLYGONAL) & (g["boundary"] == "administrative")]
    else:
        g = g.iloc[0:0]
    if g.empty:
        logger.warning("No admin_level=10 ward polygons in OSM for %s — "
                       "continuing with H3-only zoning.", cfg.city.name)
        return None
    wards = gpd.GeoDataFrame({
        "zone_id": "ward:" + g.osm_id,
        "ward_id": g["ward"].fillna(g.osm_id) if "ward" in g.columns else g.osm_id,
        "name": g["name"] if "name" in g.columns else None,
        "geometry": g.geometry,
    }, crs="EPSG:4326")
    wards["area_km2"] = wards.to_crs(cfg.city.utm_crs).area / 1e6
    return _write(wards.reset_index(drop=True), cfg, "wards")


def _parse_voltage(v) -> float | None:
    """OSM voltage is volts, possibly 'a;b' for multi-voltage substations — take the max."""
    if not isinstance(v, str):
        return None
    try:
        return max(float(x) for x in v.split(";") if x.strip())
    except ValueError:
        return None


def extract_infra(cfg) -> Path:
    """One row per asset: asset_id, resource, asset_type, point geometry,
    tagged_capacity (in the resource's demand unit, or null -> capacity/infra.py
    applies the YAML default and marks it ASSUMED)."""
    filters = [f"nwr/{k}={','.join(vs)}"
               for tags in INFRA_TAGS.values() for k, vs in tags.items()]
    g = _export(cfg, clip_to_city(cfg), filters, "infra")

    elec = cfg.resource_cfg.get("electricity", {}).get("capacity", {})
    volt_map = {float(k): v for k, v in elec.get("voltage_to_mva", {}).items() if k != "confidence"}
    margin = elec.get("n_minus_1_margin", 1.0)

    rows = []
    for _, f in g.drop_duplicates("osm_id").iterrows():
        for resource, tags in INFRA_TAGS.items():
            match = next(((k, v) for k, vs in tags.items() for v in vs if f.get(k) == v), None)
            if match is None or resource not in cfg.resources:
                continue
            tagged = None
            if resource == "electricity":
                volts = _parse_voltage(f.get("voltage"))
                if volts is not None and volt_map:
                    nearest = min(volt_map, key=lambda k: abs(k - volts))
                    tagged = volt_map[nearest] * 1000 * margin  # MVA -> kWh per hour
            rows.append({
                "asset_id": f.osm_id, "resource": resource, "asset_type": match[1],
                "name": f.get("name"), "voltage": f.get("voltage"),
                "tagged_capacity": tagged,
                "geometry": f.geometry.representative_point(),
            })
            break

    if rows:
        infra = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    else:
        infra = gpd.GeoDataFrame({"asset_id": [], "resource": [], "asset_type": [], "tagged_capacity": []},
                                 geometry=gpd.GeoSeries([], crs="EPSG:4326"))
    return _write(infra, cfg, "infra")


def extract_landuse_roads_pois(cfg) -> list[Path]:
    city_pbf = clip_to_city(cfg)

    lu = _export(cfg, city_pbf, [f"wr/landuse={','.join(LANDUSE_CLASS)}"], "landuse")
    if not lu.empty:
        lu = lu[lu.geom_type.isin(POLYGONAL)].drop_duplicates("osm_id")
        lu = gpd.GeoDataFrame({"landuse_class": lu["landuse"].map(LANDUSE_CLASS), "geometry": lu.geometry}, crs="EPSG:4326")

    roads = _export(cfg, city_pbf, [f"w/highway={','.join(sorted(ROAD_CLASSES))}"], "roads")
    if not roads.empty:
        roads = roads[roads.geom_type.isin(LINEAR)].drop_duplicates("osm_id")[["osm_id", "highway", "geometry"]]

    pois = _export(cfg, city_pbf, ["nwr/amenity=school,hospital,clinic", "nwr/shop"], "pois")
    if not pois.empty:
        pois = pois.drop_duplicates("osm_id")
        amenity = pois["amenity"] if "amenity" in pois.columns else pd.Series(None, index=pois.index, dtype=object)
        kind = amenity.map({"school": "school", "hospital": "hospital", "clinic": "hospital"})
        if "shop" in pois.columns:
            kind = kind.where(kind.notna(), pois["shop"].notna().map({True: "shop", False: None}))
        pois = gpd.GeoDataFrame({"poi_kind": kind, "geometry": pois.geometry.representative_point()},
                                crs="EPSG:4326").dropna(subset=["poi_kind"])

    return [_write(lu, cfg, "landuse"), _write(roads, cfg, "roads"), _write(pois, cfg, "pois")]
