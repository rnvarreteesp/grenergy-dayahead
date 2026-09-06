"""Sink local (DuckDB). Permite ejecutar y probar todo el ETL sin Fabric,
y es la base de datos que consume la API REST en local."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

import duckdb

from ..models import COLUMNS, PricePoint
from .base import Sink

log = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    country_code      VARCHAR NOT NULL,
    bidding_zone      VARCHAR,
    ts_utc            TIMESTAMP NOT NULL,
    ts_local          TIMESTAMP NOT NULL,
    delivery_date     DATE NOT NULL,
    resolution        VARCHAR NOT NULL,
    price_original    DOUBLE,
    currency_original VARCHAR,
    price_eur         DOUBLE,
    fx_rate           DOUBLE,
    source            VARCHAR,
    ingested_at_utc   TIMESTAMP,
    PRIMARY KEY (country_code, ts_utc, resolution)
);
"""


class DuckDBSink(Sink):
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path))

    def upsert(self, table: str, points: Sequence[PricePoint]) -> int:
        if not points:
            return 0
        self.con.execute(_DDL.format(table=table))
        rows = [tuple(p.as_row()[c] for c in COLUMNS) for p in points]
        placeholders = ", ".join("?" * len(COLUMNS))
        # DuckDB soporta upsert nativo sobre la PK -> idempotente.
        self.con.executemany(
            f"INSERT OR REPLACE INTO {table} ({', '.join(COLUMNS)}) VALUES ({placeholders})", rows)
        self.con.commit()
        log.info("DuckDB %s: %s filas upsert", table, len(rows))
        return len(rows)

    def close(self) -> None:
        self.con.close()
