"""CP-SAT allocation (implementation.md §8.8).

  minimise   Sum_z  unmet[z] * PENALTY  +  Sum_(a,z) x[a,z] * dist_cost[a,z]
  s.t.       Sum_z x[a,z]  <=  capacity[a]                    for all assets a
             Sum_a x[a,z]  +  unmet[z]  =  demand_p90[z]       for all zones z
             x[a,z] = 0  where dist(a,z) > service_radius

CP-SAT is integral — work in whole litres/kWh/kg. PENALTY >> max distance
cost so unmet demand always dominates. 30s cap; accept FEASIBLE, not just
OPTIMAL — never let the demo hang on a solver.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from ortools.sat.python import cp_model

from urms.capacity.infra import _default_capacity

PENALTY = 10_000
MAX_SOLVE_SECONDS = 30


def _allocate_resource(zones: gpd.GeoDataFrame, assets: gpd.GeoDataFrame, demand: pd.Series, service_radius_m: float):
    model = cp_model.CpModel()
    z_idx = list(range(len(zones)))
    a_idx = list(range(len(assets)))

    dist = np.zeros((len(assets), len(zones)))
    for ai, arow in enumerate(assets.itertuples()):
        dist[ai] = zones.geometry.distance(arow.geometry).values

    demand_int = demand.round().astype(int).clip(lower=0).values
    capacity_int = assets.capacity.round().astype(int).clip(lower=0).values

    x = {}
    for a in a_idx:
        for z in z_idx:
            if dist[a, z] <= service_radius_m:
                x[a, z] = model.NewIntVar(0, int(max(demand_int[z], 0)), f"x_{a}_{z}")
    unmet = [model.NewIntVar(0, int(demand_int[z]), f"unmet_{z}") for z in z_idx]

    for a in a_idx:
        model.Add(sum(x[a, z] for z in z_idx if (a, z) in x) <= int(capacity_int[a]))
    for z in z_idx:
        model.Add(sum(x[a, z] for a in a_idx if (a, z) in x) + unmet[z] == int(demand_int[z]))

    dist_cost = sum(int(dist[a, z]) * x[a, z] for a, z in x)
    model.Minimize(PENALTY * sum(unmet) + dist_cost)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = MAX_SOLVE_SECONDS
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"CP-SAT allocation returned {solver.StatusName(status)} — see implementation.md §14")

    rows = []
    for (a, z), var in x.items():
        qty = solver.Value(var)
        if qty > 0:
            rows.append({
                "from_asset_id": assets.iloc[a].get("asset_id", a),
                "to_zone_id": zones.iloc[z].zone_id,
                "quantity": qty, "cost": qty * dist[a, z], "unmet": 0,
            })
    for z in z_idx:
        u = solver.Value(unmet[z])
        if u > 0:
            rows.append({"from_asset_id": None, "to_zone_id": zones.iloc[z].zone_id,
                         "quantity": 0, "cost": 0.0, "unmet": u})
    return rows, solver.StatusName(status)


def allocate(cfg) -> Path:
    zones = gpd.read_parquet(Path(cfg.paths.processed) / "zones.parquet").to_crs(cfg.city.utm_crs)
    gap = pd.read_parquet(Path(cfg.paths.processed) / "gap.parquet")
    infra_path = Path(cfg.paths.processed) / "infra.parquet"

    all_rows = []
    for resource in cfg.resources:
        if not infra_path.exists():
            print(f"decide allocate ({resource}): infra.parquet missing — skipping, see implementation.md §14")
            continue
        assets = gpd.read_parquet(infra_path).to_crs(cfg.city.utm_crs)
        assets = assets[assets.resource == resource] if "resource" in assets else assets.iloc[0:0]
        if assets.empty:
            continue
        ycfg = cfg.resource_cfg[resource]
        assets = assets.assign(capacity=[
            t if pd.notna(t) else _default_capacity(resource, a, ycfg)
            for t, a in zip(assets.tagged_capacity, assets.asset_type)
        ])
        demand = gap.query("resource == @resource").set_index("zone_id").reindex(zones.zone_id).demand_p90.fillna(0)
        radius = cfg.resource_cfg[resource]["capacity"]["service_radius_m"]
        rows, status = _allocate_resource(zones, assets, demand, radius)
        for r in rows:
            r["resource"] = resource
        all_rows.extend(rows)
        print(f"decide allocate ({resource}): status={status}, {len(rows)} allocations")

    out = Path(cfg.paths.processed) / "allocation.parquet"
    pd.DataFrame(all_rows).to_parquet(out)
    print(f"decide allocate: {len(all_rows)} rows -> {out}")
    return out
