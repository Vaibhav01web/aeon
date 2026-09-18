"""L1 feature table assembly — implementation.md §8.4 steps 1-6, producing
zones.parquet (contract: §7.2).

Gates (§8.4):
  corr(roof_area_m2, ghsl_builtup_frac) > 0.6   -- else zonal stats are
      misaligned; fix before proceeding, everything downstream depends on it.
  Moran's I on floorspace_m2 positive and significant (p_sim < 0.05).

Build spatial weights on the PROJECTED CRS (utm_crs), never 4326.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _zonal_mean(zones: gpd.GeoDataFrame, raster_path: Path, band: int = 1) -> pd.Series:
    """Mean raster value per zone via rasterio.mask. Returns NaN for zones
    with no raster coverage rather than raising — callers degrade gracefully."""
    import rasterio
    from rasterio.mask import mask

    if not raster_path.exists():
        logger.warning("%s missing — filling NaN for this feature.", raster_path)
        return pd.Series(np.nan, index=zones.index)

    vals = []
    with rasterio.open(raster_path) as src:
        zones_r = zones.to_crs(src.crs)
        for geom in zones_r.geometry:
            try:
                out, _ = mask(src, [geom], crop=True, indexes=band)
                data = out[out != src.nodata] if src.nodata is not None else out
                vals.append(float(np.nanmean(data)) if data.size else np.nan)
            except ValueError:
                vals.append(np.nan)
    return pd.Series(vals, index=zones.index)


def _assign_buildings(zones: gpd.GeoDataFrame, buildings: pd.DataFrame, cell_col: str) -> pd.DataFrame:
    agg = buildings.groupby(cell_col).agg(
        footprint_count=("building_id", "count"), roof_area_m2=("area_m2", "sum")
    )
    return zones.merge(agg, left_on="zone_id", right_index=True, how="left").fillna(
        {"footprint_count": 0, "roof_area_m2": 0.0}
    )


LANDUSE_CLASSES = ("residential", "commercial", "industrial", "institutional")


def _add_osm_features(zones: gpd.GeoDataFrame, cfg) -> gpd.GeoDataFrame:
    """Landuse area fractions, road length and POI counts per zone, computed
    in the projected CRS. Missing layers leave NaN/0 with a warning."""
    processed = Path(cfg.paths.processed)
    zp = zones[["zone_id", "geometry"]].to_crs(cfg.city.utm_crs)
    zone_area_m2 = zp.set_index("zone_id").area

    lu_path = processed / "landuse.parquet"
    if lu_path.exists() and len(lu := gpd.read_parquet(lu_path)):
        inter = gpd.overlay(zp, lu.to_crs(cfg.city.utm_crs), how="intersection", keep_geom_type=True)
        inter["a"] = inter.area
        frac = inter.pivot_table(index="zone_id", columns="landuse_class", values="a", aggfunc="sum")
        frac = frac.div(zone_area_m2, axis=0).clip(upper=1.0)
        for c in LANDUSE_CLASSES:
            zones[f"landuse_frac_{c}"] = zones.zone_id.map(frac[c] if c in frac else {}).fillna(0.0)
        fr = zones[[f"landuse_frac_{c}" for c in LANDUSE_CLASSES]]
        zones["dominant_landuse"] = np.where(
            fr.max(axis=1) > 0, fr.idxmax(axis=1).str.replace("landuse_frac_", ""), "unmapped")
    else:
        logger.warning("landuse.parquet missing/empty — landuse_frac_* set to NaN.")
        for c in LANDUSE_CLASSES:
            zones[f"landuse_frac_{c}"] = np.nan
        zones["dominant_landuse"] = "unmapped"

    roads_path = processed / "roads.parquet"
    if roads_path.exists() and len(roads := gpd.read_parquet(roads_path)):
        inter = gpd.overlay(roads.to_crs(cfg.city.utm_crs), zp, how="intersection", keep_geom_type=True)
        road_km = inter.assign(km=inter.length / 1000).groupby("zone_id").km.sum()
        zones["road_km"] = zones.zone_id.map(road_km).fillna(0.0)
    else:
        logger.warning("roads.parquet missing/empty — road_km set to NaN.")
        zones["road_km"] = np.nan

    pois_path = processed / "pois.parquet"
    if pois_path.exists() and len(pois := gpd.read_parquet(pois_path)):
        joined = gpd.sjoin(pois.to_crs(cfg.city.utm_crs), zp, predicate="within")
        counts = joined.groupby(["zone_id", "poi_kind"]).size().unstack(fill_value=0)
        for kind in ("school", "hospital", "shop"):
            zones[f"poi_{kind}"] = zones.zone_id.map(counts[kind] if kind in counts else {}).fillna(0).astype(int)
    else:
        logger.warning("pois.parquet missing/empty — poi_* set to 0.")
        for kind in ("school", "hospital", "shop"):
            zones[f"poi_{kind}"] = 0

    return zones


def build_features(cfg) -> Path:
    zones = gpd.read_parquet(Path(cfg.paths.processed) / "zones_grid.parquet")
    zones = zones[zones.zone_type == "h3_8"].reset_index(drop=True)  # forecasting resolution

    buildings = pd.read_parquet(Path(cfg.paths.processed) / "buildings.parquet")
    import h3

    buildings["zone_h3_8"] = buildings.apply(
        lambda b: h3.latlng_to_cell(b.centroid_lat, b.centroid_lon, cfg.zones.h3_res_model),
        axis=1,
    )
    zones = _assign_buildings(zones, buildings, "zone_h3_8")
    zones["builtup_ratio"] = zones.roof_area_m2 / (zones.area_km2 * 1e6)

    # Step 3-4: zonal stats -> height -> floorspace. Degrades to NaN if a
    # raster is missing (e.g. GHSL tile id not yet resolved, see acquire/ghsl.py).
    interim = Path(cfg.paths.interim)
    zones["ghs_built_v"] = _zonal_mean(zones, interim / "ghs_built_v.tif")
    zones["ghs_built_s"] = _zonal_mean(zones, interim / "ghs_built_s.tif")
    zones["ghsl_builtup_frac"] = zones["ghs_built_s"]
    zones["ghsl_pop"] = _zonal_mean(zones, interim / "ghs_pop.tif")
    zones["worldpop"] = _zonal_mean(zones, Path(cfg.paths.raw) / "worldpop.tif")
    zones["ndvi"] = _zonal_mean(zones, interim / "s2_current.tif", band=1)
    zones["ndbi"] = _zonal_mean(zones, interim / "s2_current.tif", band=2)
    zones["ndwi"] = _zonal_mean(zones, interim / "s2_current.tif", band=3)
    ndbi_baseline = _zonal_mean(zones, interim / "s2_baseline.tif", band=2)
    zones["builtup_growth"] = zones["ndbi"] - ndbi_baseline

    zones["mean_height_m"] = (zones.ghs_built_v / zones.ghs_built_s.replace(0, np.nan)).fillna(3.0)
    zones["est_floors"] = zones.mean_height_m.div(3.0).round().clip(lower=1)
    zones["floorspace_m2"] = zones.roof_area_m2 * zones.est_floors

    # Step 5: OSM landuse / roads / POIs — raw layers from acquire/osm.py.
    zones = _add_osm_features(zones, cfg)

    # Step 6: spatial lag (KNN k=6, projected CRS) + Moran's I.
    from libpysal.weights import KNN
    from esda import Moran

    zones_proj = zones.to_crs(cfg.city.utm_crs)
    w = KNN.from_dataframe(zones_proj, k=6)
    w.transform = "r"
    lag = w.sparse @ zones.floorspace_m2.fillna(0).values
    zones["spatial_lag_floorspace"] = lag

    moran = Moran(zones.floorspace_m2.fillna(0).values, w)
    zones["ward_id"] = None
    zones["pop_confidence"] = np.where(
        zones.footprint_count < cfg.zones.min_footprints_per_zone, "low", "high"
    )

    out = Path(cfg.paths.processed) / "zones.parquet"
    zones.to_parquet(out, schema_version="1.1.0")

    corr = zones[["roof_area_m2", "ghsl_builtup_frac"]].corr().iloc[0, 1]
    gate_corr = bool(corr > 0.6) if not np.isnan(corr) else False
    gate_moran = bool(moran.I > 0.15 and moran.p_sim < 0.05)
    print(
        f"zones features: {len(zones)} zones -> {out}  "
        f"[gates: corr(roof,ghsl)={corr:.2f} -> {'OK' if gate_corr else 'CHECK'}, "
        f"Moran's I={moran.I:.2f} p={moran.p_sim:.3f} -> {'OK' if gate_moran else 'CHECK'}]"
    )
    return out
