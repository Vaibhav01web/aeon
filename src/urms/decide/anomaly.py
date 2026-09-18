"""Two-layer anomaly detection on forecast residuals (implementation.md
§8.7). Runs on a REPLAYED / SYNTHETIC meter stream — there is no free live
municipal telemetry feed in India, and the UI must say so.

  1. Residual z-score: z = (actual - p50) / (0.5*(p90-p10)/1.2816).
     Flag |z| > 3. Persistent positive residual in a water zone = candidate
     leak; sudden negative = supply interruption.
  2. IsolationForest(contamination=0.02) on
     [residual, residual_slope_6h, night_min_ratio, neighbour_residual_mean].
     night_min_ratio is the classic leak signature: real consumption
     collapses at 03:00, a leak does not.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

Z_THRESHOLD = 3.0


def detect_anomalies(cfg) -> Path:
    feed_path = Path(cfg.paths.interim) / "synthetic_feed.parquet"
    if not feed_path.exists():
        print("decide anomaly: synthetic_feed.parquet missing — run "
              "`python -m urms.tools.make_synthetic_feed` first (see implementation.md §8.7)")
        out = Path(cfg.paths.processed) / "anomalies.parquet"
        pd.DataFrame(columns=["zone_id", "resource", "ts", "z_score", "iforest_flag", "label"]).to_parquet(out)
        return out

    feed = pd.read_parquet(feed_path)
    feed = feed.sort_values(["zone_id", "resource", "ts"])

    feed["half_width"] = 0.5 * (feed.demand_p90 - feed.demand_p10) / 1.2816
    feed["residual"] = feed.actual - feed.demand_p50
    feed["z_score"] = feed.residual / feed.half_width.replace(0, np.nan)
    feed["z_flag"] = feed.z_score.abs() > Z_THRESHOLD

    feed["residual_slope_6h"] = feed.groupby(["zone_id", "resource"]).residual.diff(6)
    feed["hour"] = pd.to_datetime(feed.ts).dt.hour
    night = feed[feed.hour.between(2, 4)].groupby(["zone_id", "resource"]).actual.transform("mean")
    day_mean = feed.groupby(["zone_id", "resource"]).actual.transform("mean")
    feed["night_min_ratio"] = night / day_mean.replace(0, np.nan)
    feed["neighbour_residual_mean"] = feed.groupby(["resource", "ts"]).residual.transform("mean")

    features = feed[["residual", "residual_slope_6h", "night_min_ratio", "neighbour_residual_mean"]].fillna(0.0)
    iforest = IsolationForest(contamination=0.02, random_state=0)
    feed["iforest_flag"] = iforest.fit_predict(features) == -1

    out_cols = ["zone_id", "resource", "ts", "z_score", "z_flag", "iforest_flag", "label"]
    out_df = feed[[c for c in out_cols if c in feed.columns]]

    out = Path(cfg.paths.processed) / "anomalies.parquet"
    out_df.to_parquet(out)

    if "label" in feed.columns:
        tp = ((feed.iforest_flag) & (feed.label != "none")).sum()
        fp = ((feed.iforest_flag) & (feed.label == "none")).sum()
        fn = ((~feed.iforest_flag) & (feed.label != "none")).sum()
        precision = tp / (tp + fp) if (tp + fp) else float("nan")
        recall = tp / (tp + fn) if (tp + fn) else float("nan")
        print(f"decide anomaly: precision={precision:.2f} recall={recall:.2f} against injected labels -> {out}")
    else:
        print(f"decide anomaly: {out_df.iforest_flag.sum()} flagged -> {out}")
    return out
