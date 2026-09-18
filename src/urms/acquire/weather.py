"""D9 — Open-Meteo forecast + archive API. No key. <10k calls/day,
non-commercial free tier (fine for a hackathon demo)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def _city_centroid(cfg) -> tuple[float, float]:
    lon0, lat0, lon1, lat1 = cfg.city.bbox
    return (lat0 + lat1) / 2, (lon0 + lon1) / 2


def fetch_forecast(cfg) -> Path:
    lat, lon = _city_centroid(cfg)
    r = requests.get(
        FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,precipitation",
            "timezone": cfg.city.timezone,
            "forecast_hours": cfg.forecast.horizon_hours,
        },
        timeout=30,
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json()["hourly"])
    out = Path(cfg.paths.raw) / "weather_forecast.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    print(f"acquire weather (forecast): {len(df)} rows -> {out}")
    return out


def fetch_archive(cfg, start: str, end: str) -> Path:
    lat, lon = _city_centroid(cfg)
    r = requests.get(
        ARCHIVE_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "start_date": start,
            "end_date": end,
            "hourly": "temperature_2m,precipitation",
            "timezone": cfg.city.timezone,
        },
        timeout=30,
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json()["hourly"])
    out = Path(cfg.paths.raw) / "weather_archive.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    print(f"acquire weather (archive): {len(df)} rows -> {out}")
    return out
