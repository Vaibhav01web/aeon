"""Data-quality assertions run by `urms validate all` (implementation.md §11).
Returns a list of (name, passed, detail) rather than raising, so report.py
can publish failing checks honestly instead of hiding them.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd


def run_checks(cfg) -> list[tuple[str, bool, str]]:
    processed = Path(cfg.paths.processed)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn):
        try:
            ok, detail = fn()
        except Exception as e:  # a missing artifact is a failed check, not a crash
            ok, detail = False, f"{type(e).__name__}: {e}"
        results.append((name, bool(ok), detail))

    def buildings():
        b = pd.read_parquet(processed / "buildings.parquet")
        frac = b.area_m2.between(5, 20_000).mean()
        return len(b) > 10_000 and frac > 0.95, f"n={len(b)}, plausible-area fraction={frac:.3f}"

    def h3_orientation():
        z = gpd.read_parquet(processed / "zones.parquet")
        c = z.geometry.centroid
        ok = c.x.between(60, 100).all() and c.y.between(6, 38).all()
        return ok, "centroids inside India" if ok else "lat/lng swapped — see implementation.md §18 #1"

    def density_vs_ghsl():
        z = gpd.read_parquet(processed / "zones.parquet")
        r = z[["roof_area_m2", "ghsl_builtup_frac"]].corr().iloc[0, 1]
        return r > 0.6, f"r={r:.3f} (target > 0.6)"

    def population_conserved():
        z = gpd.read_parquet(processed / "zones.parquet")
        ref_year = int(cfg.sentinel.date_end[:4])
        target = cfg.city.population_total * (1 + cfg.city.population_growth_rate) ** (ref_year - cfg.city.population_year)
        err = abs(z.population.sum() - target) / target
        return err < 0.001, f"relative error={err:.5f}"

    def quantiles_ordered():
        d = pd.read_parquet(processed / "demand.parquet")
        ok = (d.demand_p10 <= d.demand_p50).all() and (d.demand_p50 <= d.demand_p90).all()
        return ok, f"{len(d)} rows"

    def capacity_provenance():
        c = pd.read_parquet(processed / "capacity.parquet")
        ok = c.capacity_provenance.isin(["osm_tagged", "osm_default_assumed"]).all()
        return ok, c.capacity_provenance.value_counts().to_dict()

    for name, fn in [
        ("buildings_plausible", buildings),
        ("h3_orientation", h3_orientation),
        ("density_agrees_with_ghsl", density_vs_ghsl),
        ("population_conserved", population_conserved),
        ("quantiles_ordered", quantiles_ordered),
        ("every_capacity_has_provenance", capacity_provenance),
    ]:
        check(name, fn)
    return results
