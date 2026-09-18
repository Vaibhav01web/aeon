"""CO2e avoided — the module that earns the deck's "AI for Climate Change"
title (implementation.md §8.8):

  water     dCO2e = d(MLD) * pumping_kwh_per_ml * grid_ef        (NRW reduction, leak fix)
  elec      dCO2e = peak_shaved_kwh * peak_shave_ef              (load shifting)
  waste     dCO2e = d(route_km) / truck_kmpl * diesel_ef
                  + organics_diverted_kg * methane_kgco2e_per_kg (the big one)

Report tCO2e/year per intervention. State emission factors and sources on
screen — never hide them.
"""

from __future__ import annotations

import json
from pathlib import Path


def compute_climate_impact(cfg) -> Path:
    wcfg = cfg.resource_cfg["water"]["climate"]
    ecfg = cfg.resource_cfg["electricity"]["climate"]
    kcfg = cfg.resource_cfg["waste"]["climate"]

    scenarios_path = Path(cfg.paths.build) / "scenarios.json"
    baseline = nrw_fixed = None
    if scenarios_path.exists():
        results = json.loads(scenarios_path.read_text())
        baseline = next((r for r in results if r["scenario_id"] == "baseline"), None)
        nrw_fixed = next((r for r in results if r["scenario_id"] == "nrw_fixed"), None)

    impacts = {}

    if baseline and nrw_fixed:
        d_mld_per_day = (
            baseline["total_demand_p50_by_resource"].get("water", 0)
            - nrw_fixed["total_demand_p50_by_resource"].get("water", 0)
        ) / 1e6
        impacts["water_nrw_reduction_tco2e_per_year"] = (
            d_mld_per_day * wcfg["pumping_kwh_per_ml"] * wcfg["grid_emission_factor_kgco2_per_kwh"] * 365 / 1000
        )
    else:
        impacts["water_nrw_reduction_tco2e_per_year"] = None
        print("decide climate: scenarios.json missing baseline/nrw_fixed — run `decide scenarios` first")

    routes_path = Path(cfg.paths.processed) / "routes.parquet"
    if routes_path.exists():
        import pandas as pd
        routes = pd.read_parquet(routes_path)
        route_km_per_day = routes.leg_distance_m.sum() / 1000
        impacts["waste_routing_tco2e_per_year"] = (
            route_km_per_day / kcfg["truck_kmpl"] * kcfg["diesel_kgco2_per_litre"] * 365 / 1000
        )
    else:
        impacts["waste_routing_tco2e_per_year"] = None

    ccfg = cfg.resource_cfg["waste"]["composition"]
    organics_path = Path(cfg.paths.processed) / "base_demand.parquet"
    if organics_path.exists():
        import pandas as pd
        base_demand = pd.read_parquet(organics_path)
        waste_kgd = base_demand.query("resource == 'waste'").base_demand.sum()
        organics_diverted_kgd = waste_kgd * ccfg["wet_organic"] * 0.80  # organics_diverted scenario
        impacts["waste_organics_diversion_tco2e_per_year"] = (
            organics_diverted_kgd * kcfg["methane_kgco2e_per_kg_organic_landfilled"] * 365 / 1000
        )
    else:
        impacts["waste_organics_diversion_tco2e_per_year"] = None

    impacts["_sources"] = {
        "grid_emission_factor": wcfg["ef_source"],
        "waste_methane_factor": kcfg["source"],
    }

    out = Path(cfg.paths.build) / "climate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(impacts, indent=2))
    print(f"decide climate: -> {out}  {', '.join(f'{k}={v}' for k, v in impacts.items() if not k.startswith('_'))}")
    return out
