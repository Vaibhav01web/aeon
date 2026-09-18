"""D1 — building footprints: VIDA merged Google+Microsoft+OSM, remote
GeoParquet, bbox-pruned via DuckDB. No download, no account.

Gate: for a metro bbox expect >= 400,000 footprints, median area_m2 roughly
40-90, and a mixed bf_source. If you get < 10,000, the bbox is wrong or the
bbox-struct filter ran after (rather than before) the geometry filter.
Runtime: 30s-4min depending on bandwidth. Cache aggressively; never re-run
this during the demo itself.
"""

from __future__ import annotations

from pathlib import Path

from urms.conf import Config
from urms.db import connect

# Read via source.coop's S3-compatible endpoint, not https://: DuckDB can only
# expand the `*` glob where it can list objects, which plain HTTP can't do.
VIDA = (
    "s3://vida/google-microsoft-osm-open-buildings/"
    "geoparquet/by_country_s2/country_iso={iso}/*.parquet"
)


def fetch_buildings(cfg: Config, iso: str = "IND") -> Path:
    lon0, lat0, lon1, lat1 = cfg.city.bbox
    con = connect()
    con.execute(
        "SET s3_endpoint='data.source.coop'; SET s3_url_style='path'; "
        "SET s3_access_key_id=''; SET s3_secret_access_key='';"  # anonymous, no account
    )
    # ~200 shard footers are fetched concurrently; one transient DNS/HTTP
    # failure otherwise aborts the whole query.
    con.execute("SET http_retries=10; SET http_retry_wait_ms=1000; SET http_retry_backoff=2; SET threads=4;")
    con.execute(
        f"""
      CREATE OR REPLACE TABLE buildings AS
      SELECT row_number() OVER () - 1              AS building_id,
             geometry,
             bf_source,
             ST_X(ST_Centroid(geometry))           AS centroid_lon,
             ST_Y(ST_Centroid(geometry))           AS centroid_lat,
             ST_Area(ST_Transform(geometry,'EPSG:4326','{cfg.city.utm_crs}')) AS area_m2
      FROM read_parquet('{VIDA.format(iso=iso)}', hive_partitioning=true)
      WHERE bbox.xmin > {lon0} AND bbox.xmax < {lon1}      -- row-group pruning FIRST
        AND bbox.ymin > {lat0} AND bbox.ymax < {lat1}
        AND ST_Intersects(geometry,
              ST_MakeEnvelope({lon0},{lat0},{lon1},{lat1}))  -- exact refine
    """
    )
    n = con.execute("SELECT count(*) FROM buildings").fetchone()[0]
    if n < 10_000:
        raise RuntimeError(
            f"Only {n} footprints for bbox {cfg.city.bbox} — bbox is likely "
            "wrong, or too small for a metro-scale city. See implementation.md §14."
        )

    out = Path(cfg.paths.processed) / "buildings.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY buildings TO '{out}' (FORMAT PARQUET)")
    print(f"acquire buildings: {n} rows -> {out}  [gate: n>=10000 OK]")
    return out
