"""Pydantic config loader — reads config/cities/<slug>.yaml (or $URMS_CITY),
validates it, and merges in config/resources/*.yaml and config/scenarios.yaml.

No city name, bbox, CRS or coefficient may appear anywhere else under src/.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator

CONFIG_ROOT = Path("config")


class CityCfg(BaseModel):
    name: str
    slug: str
    state: str
    state_code: str
    ulb_name: str
    lgd_code: str | None = None
    bbox: tuple[float, float, float, float]   # lon_min, lat_min, lon_max, lat_max
    utm_crs: str
    timezone: str
    population_total: float
    population_year: int
    population_growth_rate: float
    area_km2: float
    ward_population_csv: str | None = None

    @field_validator("utm_crs")
    @classmethod
    def must_be_projected(cls, v: str) -> str:
        if v.upper() in ("EPSG:4326", "WGS84"):
            raise ValueError(
                "utm_crs must be a projected CRS — EPSG:4326 makes "
                "odc.stac.load(resolution=...) interpret metres as degrees."
            )
        return v


class ZonesCfg(BaseModel):
    h3_res_model: int
    h3_res_display: int
    use_wards: bool
    min_footprints_per_zone: int
    min_building_area_m2: float = 0.0


class SentinelCfg(BaseModel):
    date_start: str
    date_end: str
    baseline_start: str
    baseline_end: str
    max_cloud_cover: int
    bands: list[str]
    resolution: int


class ForecastCfg(BaseModel):
    horizon_hours: int
    quantiles: list[float]
    temporal_model: Literal["MSTL", "AutoETS", "AutoARIMA", "SeasonalNaive"]
    seasonality: list[int]


class PathsCfg(BaseModel):
    raw: str
    interim: str
    processed: str
    build: str


class Config(BaseModel):
    city: CityCfg
    zones: ZonesCfg
    sentinel: SentinelCfg
    resources: list[Literal["water", "electricity", "waste"]]
    forecast: ForecastCfg
    paths: PathsCfg
    resource_cfg: dict[str, dict] = {}
    scenarios: list[dict] = []


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_config(city_yaml: str | Path | None = None) -> Config:
    """Resolve the active city config, in order: explicit arg > $URMS_CITY >
    config/city.yaml. Merge in resource coefficients and scenarios."""
    if city_yaml is None:
        city_yaml = os.environ.get("URMS_CITY", CONFIG_ROOT / "city.yaml")
    raw = _load_yaml(Path(city_yaml))

    resource_cfg = {
        r: _load_yaml(CONFIG_ROOT / "resources" / f"{r}.yaml")
        for r in raw["resources"]
    }
    scenarios = _load_yaml(CONFIG_ROOT / "scenarios.yaml")["scenarios"]

    return Config(**raw, resource_cfg=resource_cfg, scenarios=scenarios)
