"""CVRP waste collection routing (implementation.md §8.8).

⚠️ Import path: `from ortools.constraint_solver import pywrapcp,
routing_enums_pb2`. There is no `ortools.routing` module.

Distance matrix from OSRM `/table/v1/driving/`. Cap collection points at
~120 for the demo (matrix cost is O(n^2)) by taking the top-N zones by
waste volume. Compare against a nearest-neighbour baseline and report %
distance saved — the headline number for the waste slice.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import requests
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

OSRM_LOCAL = "http://localhost:5000"
MAX_STOPS = 120


def _osrm_table(coords: list[tuple[float, float]], osrm_url: str = OSRM_LOCAL) -> np.ndarray:
    """coords: list of (lon, lat). Falls back to haversine x 1.4 circuity
    factor if OSRM is unreachable, per implementation.md §14 (never crash
    the pipeline on an optional routing backend)."""
    loc_str = ";".join(f"{lon},{lat}" for lon, lat in coords)
    try:
        r = requests.get(f"{osrm_url}/table/v1/driving/{loc_str}", params={"annotations": "distance"}, timeout=15)
        r.raise_for_status()
        return np.array(r.json()["distances"])
    except requests.RequestException:
        print("decide routing: OSRM unreachable — falling back to haversine x1.4 circuity factor")
        return _haversine_matrix(coords) * 1.4


def _haversine_matrix(coords: list[tuple[float, float]]) -> np.ndarray:
    R = 6371000
    lon, lat = np.radians(np.array(coords)).T
    dlon = lon[:, None] - lon[None, :]
    dlat = lat[:, None] - lat[None, :]
    a = np.sin(dlat / 2) ** 2 + np.cos(lat[:, None]) * np.cos(lat[None, :]) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def _nearest_neighbour_distance(matrix: np.ndarray, depot: int = 0) -> float:
    n = len(matrix)
    visited = {depot}
    cur, total = depot, 0.0
    while len(visited) < n:
        nxt = min((j for j in range(n) if j not in visited), key=lambda j: matrix[cur, j])
        total += matrix[cur, nxt]
        visited.add(nxt)
        cur = nxt
    return total


def route_waste(cfg) -> Path:
    gap = pd.read_parquet(Path(cfg.paths.processed) / "gap.parquet")
    zones = gap.query("resource == 'waste'").nlargest(MAX_STOPS, "demand_p50")

    import geopandas as gpd
    zg = gpd.read_parquet(Path(cfg.paths.processed) / "zones.parquet")
    zg = zg[zg.zone_id.isin(zones.zone_id)]
    centroids = zg.geometry.centroid
    coords = [(cfg.city.bbox[0] + cfg.city.bbox[2]) / 2, (cfg.city.bbox[1] + cfg.city.bbox[3]) / 2]
    coords = [tuple(coords)] + [(pt.x, pt.y) for pt in centroids]  # depot first

    wcfg = cfg.resource_cfg["waste"]["collection"]
    n_vehicles = wcfg["n_vehicles"]
    veh_cap = wcfg["vehicle_capacity_kg"]

    matrix = _osrm_table(coords)
    demands = [0] + zones.set_index("zone_id").reindex(zg.zone_id).demand_p50.round().astype(int).tolist()

    manager = pywrapcp.RoutingIndexManager(len(coords), n_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(i, j):
        return int(matrix[manager.IndexToNode(i), manager.IndexToNode(j)])

    def demand_cb(i):
        return int(demands[manager.IndexToNode(i)])

    t = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(t)
    d = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(d, 0, [veh_cap] * n_vehicles, True, "Cap")

    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.FromSeconds(30)

    solution = routing.SolveWithParameters(p)
    rows = []
    total_route_distance = 0.0
    if solution:
        for v in range(n_vehicles):
            idx = routing.Start(v)
            seq, cum_load, dist_accum = 0, 0, 0.0
            while not routing.IsEnd(idx):
                node = manager.IndexToNode(idx)
                cum_load += demands[node]
                zone_id = zg.iloc[node - 1].zone_id if node > 0 else "depot"
                nxt = solution.Value(routing.NextVar(idx))
                leg = matrix[node, manager.IndexToNode(nxt)]
                dist_accum += leg
                rows.append({
                    "route_id": v, "vehicle_id": v, "stop_seq": seq, "zone_id": zone_id,
                    "load_kg": demands[node], "cum_load_kg": cum_load,
                    "leg_distance_m": leg, "leg_duration_s": leg / (wcfg["avg_speed_kmph"] * 1000 / 3600),
                })
                seq += 1
                idx = nxt
            total_route_distance += dist_accum

    baseline = _nearest_neighbour_distance(matrix)
    pct_saved = 100 * (baseline - total_route_distance) / baseline if baseline else 0.0

    out = Path(cfg.paths.processed) / "routes.parquet"
    pd.DataFrame(rows).to_parquet(out)
    print(
        f"decide routing: {len(rows)} stops, {total_route_distance/1000:.1f} km solved vs "
        f"{baseline/1000:.1f} km nearest-neighbour baseline -> {pct_saved:.1f}% saved -> {out}"
    )
    return out
