"""DuckDB connection factory — the entire data layer (spatial + httpfs)."""

from __future__ import annotations

import duckdb


def connect(read_only: bool = False, db_path: str = "data/urms.duckdb") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path, read_only=read_only)
    for ext in ("spatial", "httpfs"):
        try:
            con.execute(f"INSTALL {ext}; LOAD {ext};")
        except duckdb.Exception as e:
            raise RuntimeError(
                f"DuckDB extension '{ext}' unavailable: {e}\n"
                "Extensions are downloaded at runtime and are NOT autoloadable.\n"
                "On Kaggle: enable Internet in notebook settings. Offline: side-load "
                "the .duckdb_extension file and LOAD it by path."
            ) from e
    con.execute("SET s3_region='us-west-2';")
    con.execute("SET enable_progress_bar=true;")
    return con
