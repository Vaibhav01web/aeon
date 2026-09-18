"""D2 — Sentinel-2 L2A features via Earth Search (Element 84 / AWS Open
Data): NDVI / NDBI / NDWI, no auth required, not requester-pays.

Traps: `resolution` is metres only because `crs` is a projected CRS — with
EPSG:4326 it means degrees. Use band aliases ('red','nir') not raw asset
keys ('B04','B08'). Do not use Sentinel-1 on AWS — it is requester-pays.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

STAC_URL = "https://earth-search.aws.element84.com/v1"


def fetch_s2_features(cfg, window: Literal["current", "baseline"] = "current") -> Path:
    import odc.stac
    from pystac_client import Client

    s = cfg.sentinel
    start, end = (
        (s.date_start, s.date_end)
        if window == "current"
        else (s.baseline_start, s.baseline_end)
    )
    items = list(
        Client.open(STAC_URL)
        .search(
            collections=["sentinel-2-l2a"],
            bbox=cfg.city.bbox,
            datetime=f"{start}/{end}",
            query={"eo:cloud_cover": {"lt": s.max_cloud_cover}},
        )
        .items()
    )
    if not items:
        raise RuntimeError(
            f"No S2 scenes for {start}..{end} under {s.max_cloud_cover}% cloud. "
            "Widen the date window or raise max_cloud_cover (see implementation.md §14)."
        )

    ds = odc.stac.load(
        items,
        bands=s.bands,
        bbox=cfg.city.bbox,
        crs=cfg.city.utm_crs,           # MUST be projected, see conf.py validator
        resolution=s.resolution,
        groupby="solar_day",
        chunks={"x": 2048, "y": 2048},
        resampling="bilinear",
    )
    # SCL cloud/shadow/cirrus mask: keep 4=veg 5=bare 6=water 7=unclassified
    keep = ds.scl.isin([4, 5, 6, 7])
    ds = ds.where(keep)
    med = ds.median(dim="time", skipna=True)
    eps = 1e-6
    indices = {
        "ndvi": (med.nir - med.red) / (med.nir + med.red + eps),
        "ndbi": (med.swir16 - med.nir) / (med.swir16 + med.nir + eps),
        "ndwi": (med.green - med.nir) / (med.green + med.nir + eps),
    }

    out_dir = Path(cfg.paths.interim)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"s2_{window}.tif"
    stacked = None
    for name, arr in indices.items():
        arr = arr.rio.write_crs(cfg.city.utm_crs).rename(name)
        stacked = arr.expand_dims(band=[name]) if stacked is None else stacked.combine_first(
            arr.expand_dims(band=[name])
        )
    stacked.rio.to_raster(out)

    print(
        f"acquire sentinel ({window}): {len(items)} scenes -> {out}  "
        f"[gate: scenes>=4 -> {'OK' if len(items) >= 4 else 'WARN'}]"
    )
    return out
