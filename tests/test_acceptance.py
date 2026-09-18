"""Acceptance gates (implementation.md §11). Data-dependent tests skip
until the pipeline has produced their input artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from urms.conf import load_config

PROCESSED = Path("data/processed")
BUILD = Path("build")


@pytest.fixture(scope="session")
def cfg():
    return load_config()


def _need(path: Path):
    if not path.exists():
        pytest.skip(f"{path} not built yet")
    return path


@pytest.fixture
def buildings():
    return pd.read_parquet(_need(PROCESSED / "buildings.parquet"))


@pytest.fixture
def zones():
    import geopandas as gpd
    return gpd.read_parquet(_need(PROCESSED / "zones.parquet"))


@pytest.fixture
def demand():
    return pd.read_parquet(_need(PROCESSED / "demand.parquet"))


@pytest.fixture
def capacity():
    return pd.read_parquet(_need(PROCESSED / "capacity.parquet"))


def test_buildings_plausible(buildings, cfg):
    assert len(buildings) > 10_000
    assert buildings.area_m2.between(5, 20_000).mean() > 0.95
    assert buildings.bf_source.nunique() >= 1
    assert buildings.centroid_lon.between(cfg.city.bbox[0], cfg.city.bbox[2]).all()


def test_h3_orientation(zones):
    """Catches the (lat,lng) bug: centroids must be inside India."""
    c = zones.geometry.centroid
    assert c.x.between(60, 100).all(), "longitude out of India - lat/lng swapped"
    assert c.y.between(6, 38).all(), "latitude out of India - lat/lng swapped"


def test_zone_area_matches_h3(zones):
    import h3
    z = zones[zones.zone_type == "h3_8"].iloc[0]
    assert abs(z.area_km2 - h3.cell_area(z.zone_id, "km^2")) / z.area_km2 < 0.05


def test_density_agrees_with_ghsl(zones):
    assert zones[["roof_area_m2", "ghsl_builtup_frac"]].corr().iloc[0, 1] > 0.6


def test_spatial_autocorrelation(zones, cfg):
    from esda import Moran
    from libpysal.weights import KNN
    w = KNN.from_dataframe(zones.to_crs(cfg.city.utm_crs), k=6)
    w.transform = "r"
    m = Moran(zones.floorspace_m2.values, w)
    assert m.I > 0.15 and m.p_sim < 0.05


def test_population_conserved(zones, cfg):
    ref_year = int(cfg.sentinel.date_end[:4])
    t = cfg.city.population_total * (1 + cfg.city.population_growth_rate) ** (ref_year - cfg.city.population_year)
    assert abs(zones.population.sum() - t) / t < 0.001
    assert (zones.population >= 0).all() and zones.population.notna().all()
    assert zones[["population", "ghsl_pop"]].corr().iloc[0, 1] > 0.7


def test_demand_sanity(demand, zones, cfg):
    """The single most valuable test: catches compounding multipliers."""
    lpcd = cfg.resource_cfg["water"]["per_capita"]["domestic_lpcd"]
    w = demand.query("resource=='water' and scenario_id=='baseline'")
    daily_mld = w.groupby("ts").demand_p50.sum().mean() * 24 / 1e6
    naive_mld = zones.population.sum() * lpcd / 1e6
    assert 0.7 * naive_mld < daily_mld < 1.6 * naive_mld


def test_quantiles_ordered(demand):
    assert (demand.demand_p10 <= demand.demand_p50).all()
    assert (demand.demand_p50 <= demand.demand_p90).all()


def test_no_paid_services():
    """Guards the ZERO-BUDGET constraint. Fails the build if violated."""
    banned = ["planet.com", "api.planet", "maxar", "mapbox.com", "access_token=",
              "requester_pays", "sentinel-s1-l1c", "MAPBOX_TOKEN", "STADIA",
              "cartocdn.com", "fly.io"]
    for p in Path("src").rglob("*.py"):
        src = p.read_text()
        for b in banned:
            assert b not in src, f"{p}: paid/keyed dependency '{b}'"
    for p in Path("web").rglob("*"):
        if p.suffix in (".html", ".js"):
            assert "mapbox-gl" not in p.read_text()


def test_no_hardcoded_city_values():
    """implementation.md §15: no city name, bbox or coefficient under src/."""
    import re
    pattern = re.compile(r"\b(18\.[0-9]{2}|73\.[0-9]{2}|Pune|135)\b")
    for p in Path("src").rglob("*.py"):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            assert not pattern.search(line), f"{p}:{i}: hard-coded city value: {line.strip()}"


def test_every_number_has_provenance(capacity):
    assert capacity.capacity_provenance.isin(["osm_tagged", "osm_default_assumed"]).all()


def test_scenarios_all_present(cfg):
    scenarios = json.loads(_need(BUILD / "scenarios.json").read_text())
    assert {s["scenario_id"] for s in scenarios} == {s["id"] for s in cfg.scenarios}
