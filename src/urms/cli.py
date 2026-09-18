
"""typer CLI — every pipeline stage is a subcommand (replaces Airflow).
Stage imports are lazy so `--help` and `doctor` work before heavy deps load.

    python -m urms.cli --help
"""

from __future__ import annotations

import shutil
import subprocess

import typer

from urms.conf import load_config

app = typer.Typer(no_args_is_help=True, help="Aeon URMS pipeline")
acquire = typer.Typer(no_args_is_help=True, help="L0 — fetch open data")
zones = typer.Typer(no_args_is_help=True, help="L1 — H3/ward zoning and features")
calibrate = typer.Typer(no_args_is_help=True, help="L2 — floorspace -> population -> base demand")
forecast = typer.Typer(no_args_is_help=True, help="L3 — temporal forecast + spatial disaggregation")
capacity = typer.Typer(no_args_is_help=True, help="L4 — capacity and gap")
decide = typer.Typer(no_args_is_help=True, help="L5 — allocation, routing, siting, anomaly, scenarios, climate")
serve = typer.Typer(no_args_is_help=True, help="L6 — static export")
validate = typer.Typer(no_args_is_help=True, help="Validation harness")

for name, sub in [
    ("acquire", acquire), ("zones", zones), ("calibrate", calibrate), ("forecast", forecast),
    ("capacity", capacity), ("decide", decide), ("serve", serve), ("validate", validate),
]:
    app.add_typer(sub, name=name)


# ---- doctor -----------------------------------------------------------------

@app.command()
def doctor() -> None:
    """Ping every endpoint and extension; print a green/red table. Run before every session."""
    import requests

    cfg = load_config()
    lon0, lat0, lon1, lat1 = cfg.city.bbox
    lat, lon = (lat0 + lat1) / 2, (lon0 + lon1) / 2
    checks: list[tuple[str, bool, str]] = []

    def http(name: str, url: str, method: str = "HEAD") -> None:
        try:
            r = requests.request(method, url, timeout=10, allow_redirects=True)
            checks.append((name, r.status_code < 400, str(r.status_code)))
        except requests.RequestException as e:
            checks.append((name, False, type(e).__name__))

    http("Earth Search STAC", "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a", "GET")
    http("VIDA source.coop", "https://data.source.coop/vida/google-microsoft-osm-open-buildings/")
    http("Open-Meteo", f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m", "GET")
    http("OpenFreeMap", "https://tiles.openfreemap.org/styles/liberty", "GET")
    http("Geofabrik", "https://download.geofabrik.de/asia/india-latest.osm.pbf")

    try:
        import duckdb
        con = duckdb.connect()
        con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
        con.execute("SELECT ST_Point(1, 2)")
        checks.append(("DuckDB spatial+httpfs", True, duckdb.__version__))
    except Exception as e:
        checks.append(("DuckDB spatial+httpfs", False, type(e).__name__))

    osmium = shutil.which("osmium")
    ver = subprocess.run([osmium, "--version"], capture_output=True, text=True).stdout.splitlines()[0] if osmium else "not installed"
    checks.append(("osmium-tool", bool(osmium), ver))

    try:
        requests.get(f"http://localhost:5000/nearest/v1/driving/{lon},{lat}", timeout=3)
        checks.append(("OSRM (local :5000)", True, "up"))
    except requests.RequestException:
        checks.append(("OSRM (local :5000)", False, "down — routing falls back to haversine x1.4"))

    for name, ok, detail in checks:
        mark = typer.style(" OK ", fg="green") if ok else typer.style("FAIL", fg="red")
        typer.echo(f"[{mark}] {name:<24} {detail}")


@app.command()
def prototype() -> None:
    """Fast end-to-end demo from data on disk (grid + OSM + buildings) -> build/ and web/data/."""
    from urms.prototype import run_prototype
    run_prototype(load_config())


# ---- L0 acquire ---------------------------------------------------------------

@acquire.command("buildings")
def acquire_buildings() -> None:
    from urms.acquire.buildings import fetch_buildings
    fetch_buildings(load_config())


@acquire.command("sentinel")
def acquire_sentinel(window: str = typer.Option("current", help="current | baseline")) -> None:
    from urms.acquire.sentinel import fetch_s2_features
    fetch_s2_features(load_config(), window=window)


@acquire.command("ghsl")
def acquire_ghsl() -> None:
    from urms.acquire.ghsl import fetch_worldpop
    fetch_worldpop(load_config())


@acquire.command("osm")
def acquire_osm() -> None:
    from urms.acquire.osm import extract_infra, extract_landuse_roads_pois, extract_wards
    cfg = load_config()
    if cfg.zones.use_wards:
        extract_wards(cfg)
    extract_infra(cfg)
    extract_landuse_roads_pois(cfg)


@acquire.command("weather")
def acquire_weather() -> None:
    from urms.acquire.weather import fetch_forecast
    fetch_forecast(load_config())


@acquire.command("stats")
def acquire_stats() -> None:
    from urms.acquire.stats import fetch_ember_state_electricity, fetch_opencity_electricity
    cfg = load_config()
    fetch_opencity_electricity(cfg)
    fetch_ember_state_electricity(cfg)


# ---- L1 zones -------------------------------------------------------------------

@zones.command("build")
def zones_build() -> None:
    from urms.zones.grid import build_zones
    build_zones(load_config())


@zones.command("features")
def zones_features() -> None:
    from urms.zones.features import build_features
    build_features(load_config())


# ---- L2 calibrate ---------------------------------------------------------------

@calibrate.command("floorspace")
def calibrate_floorspace() -> None:
    from urms.calibrate.floorspace import refine_floorspace
    refine_floorspace(load_config())


@calibrate.command("population")
def calibrate_population_cmd() -> None:
    from urms.calibrate.population import calibrate_population
    calibrate_population(load_config())


@calibrate.command("demand")
def calibrate_demand_cmd() -> None:
    from urms.calibrate.demand import calibrate_demand
    calibrate_demand(load_config())


# ---- L3 forecast ----------------------------------------------------------------

@forecast.command("temporal")
def forecast_temporal_cmd() -> None:
    from urms.forecast.temporal import forecast_temporal
    forecast_temporal(load_config())


@forecast.command("spatial")
def forecast_spatial_cmd() -> None:
    from urms.forecast.spatial import forecast_spatial
    forecast_spatial(load_config())


@forecast.command("reconcile")
def forecast_reconcile() -> None:
    from urms.forecast.reconcile import reconcile
    reconcile(load_config())


# ---- L4 capacity ----------------------------------------------------------------

@capacity.command("infra")
def capacity_infra() -> None:
    from urms.capacity.infra import build_capacity
    build_capacity(load_config())


@capacity.command("gap")
def capacity_gap() -> None:
    from urms.capacity.gap import build_gap
    build_gap(load_config())


# ---- L5 decide ------------------------------------------------------------------

@decide.command("allocate")
def decide_allocate() -> None:
    from urms.decide.allocate import allocate
    allocate(load_config())


@decide.command("routing")
def decide_routing() -> None:
    from urms.decide.routing import route_waste
    route_waste(load_config())


@decide.command("siting")
def decide_siting() -> None:
    from urms.decide.siting import site_next_assets
    site_next_assets(load_config())


@decide.command("anomaly")
def decide_anomaly() -> None:
    from urms.decide.anomaly import detect_anomalies
    detect_anomalies(load_config())


@decide.command("scenarios")
def decide_scenarios() -> None:
    from urms.decide.scenario import run_all_scenarios
    run_all_scenarios(load_config())


@decide.command("climate")
def decide_climate() -> None:
    from urms.decide.climate import compute_climate_impact
    compute_climate_impact(load_config())


# ---- L6 serve / validate --------------------------------------------------------

@serve.command("export")
def serve_export() -> None:
    from urms.serve.export import export
    export(load_config())


@validate.command("all")
def validate_all() -> None:
    from urms.validate.report import write_report
    write_report(load_config())


if __name__ == "__main__":
    app()
