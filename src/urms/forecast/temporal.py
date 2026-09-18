"""City-level temporal forecast — statsforecast MSTL, at the level where
real history exists (implementation.md §8.6).

There is no real hourly city series in free Indian data. Construct one
honestly and label it:
  - electricity: state monthly (Ember, D17) -> city share (D12) -> hourly
    via diurnal_profile + weather regression. Tag SYNTHETIC_FROM_MEASURED.
  - water/waste: annual city totals -> diurnal/weekly profile. Same tag.
Never present a synthetic series as measured — label it in the UI as
"reconstructed from published annual/monthly totals — not metered."
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def _reconstruct_hourly_history(cfg, resource: str, base_demand_total: float, hours: int = 24 * 90) -> pd.DataFrame:
    """Build a SYNTHETIC_FROM_MEASURED hourly series from the calibrated
    daily total, using the resource's diurnal_profile and a light AR(1)
    day-to-day wobble so MSTL has weekly seasonality to fit."""
    ycfg = cfg.resource_cfg[resource]
    profile = np.array(ycfg["temporal"]["diurnal_profile"])
    assert abs(profile.mean() - 1.0) < 0.05, "diurnal_profile mean must be ~1.0"

    rng = np.random.default_rng(seed=hash(resource) % (2**32))
    ts = pd.date_range(end=pd.Timestamp.now(tz=cfg.city.timezone).floor("h"), periods=hours, freq="h")
    day_wobble = 1.0 + 0.03 * np.cumsum(rng.normal(0, 1, hours // 24 + 1))
    day_wobble = np.repeat(day_wobble, 24)[:hours]
    weekly = 1.0 + 0.05 * np.sin(2 * np.pi * (ts.dayofweek.values) / 7)
    y = (base_demand_total / 24.0) * profile[ts.hour.values] * day_wobble * weekly

    return pd.DataFrame({"unique_id": resource, "ds": ts, "y": y})


def forecast_temporal(cfg) -> Path:
    from statsforecast import StatsForecast
    from statsforecast.models import MSTL, AutoETS, SeasonalNaive

    base_demand = pd.read_parquet(Path(cfg.paths.processed) / "base_demand.parquet")
    all_fc = []
    for resource in cfg.resources:
        total = base_demand.query("resource == @resource").base_demand.sum()
        df = _reconstruct_hourly_history(cfg, resource, total)

        sf = StatsForecast(
            models=[
                MSTL(season_length=cfg.forecast.seasonality, trend_forecaster=AutoETS(model="ZZN")),
                SeasonalNaive(season_length=24),
            ],
            freq="h",
            n_jobs=-1,
        )
        sf.fit(df)
        fc = sf.predict(h=cfg.forecast.horizon_hours, level=[80])
        fc["resource"] = resource
        fc["provenance"] = "SYNTHETIC_FROM_MEASURED"
        all_fc.append(fc)

    out_df = pd.concat(all_fc, ignore_index=True)
    out = Path(cfg.paths.interim) / "city_forecast.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_parquet(out)
    print(f"forecast temporal: {len(out_df)} rows ({cfg.resources}) -> {out}  [MSTL vs SeasonalNaive baseline retained]")
    return out
