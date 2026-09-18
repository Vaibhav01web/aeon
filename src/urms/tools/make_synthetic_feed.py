"""Generate the replayed/synthetic meter feed that anomaly detection runs
on (implementation.md §8.7). There is no free live municipal telemetry feed
in India — this is labelled synthetic end to end.

Replays demand_p50 + AR(1) noise, then injects three LABELLED events:
  - step_leak:        a persistent +25% step in one water zone
  - meter_fault:      diurnal profile flattened to its mean in one zone
  - outage:           demand drops to zero for 6h in one zone
Precision/recall against these injected labels is a real, honest metric.

    python -m urms.tools.make_synthetic_feed
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from urms.conf import load_config


def make_feed(cfg, seed: int = 0) -> Path:
    rng = np.random.default_rng(seed)
    demand = pd.read_parquet(Path(cfg.paths.processed) / "demand.parquet")
    demand = demand.query("scenario_id == 'baseline'").copy()
    demand = demand.sort_values(["zone_id", "resource", "ts"]).reset_index(drop=True)

    phi, sigma = 0.6, 0.05
    noise = np.zeros(len(demand))
    for i in range(1, len(demand)):
        noise[i] = phi * noise[i - 1] + rng.normal(0, sigma)
    demand["actual"] = demand.demand_p50 * (1 + noise)
    demand["label"] = "none"

    water_zones = demand.query("resource == 'water'").zone_id.unique()
    if len(water_zones) >= 3:
        leak_z, fault_z, outage_z = rng.choice(water_zones, 3, replace=False)
        ts_sorted = sorted(demand.ts.unique())
        mid = ts_sorted[len(ts_sorted) // 2]

        leak = (demand.zone_id == leak_z) & (demand.resource == "water") & (demand.ts >= mid)
        demand.loc[leak, "actual"] *= 1.25
        demand.loc[leak, "label"] = "step_leak"

        fault = (demand.zone_id == fault_z) & (demand.resource == "water")
        demand.loc[fault, "actual"] = demand.loc[fault, "actual"].mean()
        demand.loc[fault, "label"] = "meter_fault"

        outage_window = ts_sorted[len(ts_sorted) // 3 : len(ts_sorted) // 3 + 6]
        outage = (demand.zone_id == outage_z) & (demand.resource == "water") & demand.ts.isin(outage_window)
        demand.loc[outage, "actual"] = 0.0
        demand.loc[outage, "label"] = "outage"

    out = Path(cfg.paths.interim) / "synthetic_feed.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    demand.to_parquet(out)
    print(f"make_synthetic_feed: {len(demand)} rows, labels={demand.label.value_counts().to_dict()} -> {out}")
    return out


if __name__ == "__main__":
    make_feed(load_config())
