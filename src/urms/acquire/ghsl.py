"""D3-D5 — GHSL built-up surface (BUILT-S), built-up volume (BUILT-V), and
population (POP), R2023A, CC-BY 4.0. Also D6 WorldPop as an independent
population cross-check.

⚠️ GHSL's default grid is Mollweide (ESRI:54009) tiled — prefer the
`_4326_3ss` product variant where available so no reprojection/tile-id
lookup is needed. The full Mollweide tile-id set for India is UNVERIFIED
in implementation.md §Appendix A (only R6_C25, R6_C26, R7_C25 confirmed) —
if a bbox lands outside those tiles, this will need the tile grid from
`GHSL_Data_Package_2023.pdf` before it will resolve a URL. Fail loudly
rather than silently skipping, per §15 "never invent data".
"""

from __future__ import annotations

from pathlib import Path

import requests

GHSL_BASE = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL"
WORLDPOP_URL = (
    "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/{iso}"
    "/v1/100m/constrained/{iso_lower}_pop_2025_CN_100m_R2025A_v1.tif"
)


def _download(url: str, out: Path, timeout: int = 120) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    return out


def fetch_worldpop(cfg, iso: str = "IND") -> Path:
    out = Path(cfg.paths.raw) / "worldpop.tif"
    url = WORLDPOP_URL.format(iso=iso, iso_lower=iso.lower())
    _download(url, out)
    print(f"acquire ghsl (worldpop): -> {out}")
    return out


def fetch_ghsl_tiles(cfg, product: str, epoch: str | None = None) -> Path:
    """product: 'BUILT_S' | 'BUILT_V' | 'POP'. Caller must know the tile id(s)
    covering cfg.city.bbox (see the docstring caveat above); this function
    does not guess one. Raise clearly if unset rather than silently using
    a wrong tile, per implementation.md §15 "never invent data"."""
    raise NotImplementedError(
        f"GHSL {product} tile id for bbox {cfg.city.bbox} is not resolved. "
        "Look it up in GHSL_Data_Package_2023.pdf against the confirmed tiles "
        "(R6_C25, R6_C26, R7_C25) or the target city's known coverage, then "
        "wire the exact FTP path under GHSL_BASE here. See implementation.md §4.1 (D3-D5), §14."
    )
