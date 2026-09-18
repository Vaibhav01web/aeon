"""D12-D19 — demand-calibration data from open Indian portals: OpenCity
city electricity consumption (D12), Ember state electricity (D17). The
per-capita waste figures (D16, CPCB PDF) and CPHEEO lpcd (D15) are already
transcribed with sources into config/resources/*.yaml — this module only
fetches sources that are directly machine-readable.

Degrades gracefully: if a city is absent from D12, calibrate/demand.py
falls back to `fallback_annual_kwh_per_capita` from electricity.yaml and
tags the output ASSUMED. This module must log a warning and continue, not
raise, on a missing city — only the L0 building fetch is allowed to be fatal
(implementation.md §14).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

OPENCITY_ELECTRICITY_CSV = "https://data.opencity.in/dataset/electricity-consumption"
EMBER_INDIA_CSV = "https://ember-energy.org/data/india-electricity-data/"


def fetch_opencity_electricity(cfg) -> Path | None:
    """D12 — city-level electricity consumption 2017-19. Returns None (and
    logs a warning) if the city isn't present; caller must fall back."""
    out = Path(cfg.paths.raw) / "opencity_electricity.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(OPENCITY_ELECTRICITY_CSV, timeout=30)
        r.raise_for_status()
        out.write_bytes(r.content)
    except requests.RequestException as e:
        logger.warning(
            "D12 OpenCity electricity fetch failed (%s) — calibrate/demand.py "
            "will use fallback_annual_kwh_per_capita from electricity.yaml, tagged ASSUMED.",
            e,
        )
        return None

    df = pd.read_csv(out)
    city_col = next((c for c in df.columns if "city" in c.lower()), None)
    if city_col is None or cfg.city.name not in df[city_col].astype(str).values:
        logger.warning(
            "%s not found in D12 OpenCity dataset — falling back to national "
            "average, tagged ASSUMED per electricity.yaml.",
            cfg.city.name,
        )
        return None
    print(f"acquire stats (D12): {cfg.city.name} found -> {out}")
    return out


def fetch_ember_state_electricity(cfg) -> Path | None:
    """D17 — state-level electricity, monthly, CC-BY 4.0. Used to derive
    city peak via state_peak x city_consumption_share (the documented gap,
    electricity.yaml `peak_derivation`)."""
    out = Path(cfg.paths.raw) / "ember_state_electricity.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(EMBER_INDIA_CSV, timeout=30)
        r.raise_for_status()
        out.write_bytes(r.content)
    except requests.RequestException as e:
        logger.warning("D17 Ember fetch failed (%s) — state peak unavailable this run.", e)
        return None
    print(f"acquire stats (D17): -> {out}")
    return out
