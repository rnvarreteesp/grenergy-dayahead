"""Acceso de solo lectura a las tablas generadas por el ETL.

En local lee el fichero DuckDB. Contra Fabric bastaria con sustituir esta clase
por una que use el SQL endpoint del Lakehouse (pyodbc): los endpoints no
conocen el motor.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import duckdb

# Granularidad de salida -> minutos del bucket
BUCKET_MINUTES = {"PT15M": 15, "PT60M": 60}


class PriceRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("DAYAHEAD_DB", "data/dayahead.duckdb")
        if not Path(self.db_path).exists():
            raise FileNotFoundError(
                f"No existe {self.db_path}. Ejecuta primero el ETL: python -m dayahead.run")
        self.con = duckdb.connect(self.db_path, read_only=True)

    def available_tables(self) -> dict[str, str]:
        rows = self.con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'dayahead_prices_%'"
        ).fetchall()
        return {t[0].rsplit("_", 1)[1].upper(): t[0] for t in rows}

    def countries(self) -> list[dict]:
        out = []
        for code, table in sorted(self.available_tables().items()):
            row = self.con.execute(
                f"SELECT resolution, currency_original, min(delivery_date), max(delivery_date), count(*) "
                f"FROM {table} GROUP BY 1, 2"
            ).fetchone()
            if not row:
                continue
            out.append({
                "country_code": code, "resolution": row[0], "source_currency": row[1],
                "first_date": row[2], "last_date": row[3], "points": row[4],
            })
        return out

    def prices(self, countries: list[str], date_from: date, date_to: date,
               granularity: str | None = None, limit: int = 200_000) -> list[dict]:
        """Series por pais. Si se pide granularidad, agrega por media dentro del bucket.

        Agregar por media es lo correcto para comparar un mercado PT15M con uno
        PT60M: la media aritmetica de los cuatro cuartos es el precio horario
        equivalente porque todos los intervalos duran lo mismo.
        """
        tables = self.available_tables()
        selected = [(c, tables[c]) for c in countries if c in tables]
        if not selected:
            return []

        if granularity and granularity in BUCKET_MINUTES:
            minutes = BUCKET_MINUTES[granularity]
            unions = [
                f"""SELECT country_code,
                           time_bucket(INTERVAL '{minutes} minutes', ts_utc) AS ts_utc,
                           avg(price_eur) AS price_eur,
                           '{granularity}' AS resolution,
                           count(*) AS points_aggregated
                    FROM {t}
                    WHERE delivery_date BETWEEN ? AND ?
                    GROUP BY 1, 2"""
                for _, t in selected
            ]
        else:
            unions = [
                f"""SELECT country_code, ts_utc, price_eur, resolution, 1 AS points_aggregated
                    FROM {t} WHERE delivery_date BETWEEN ? AND ?"""
                for _, t in selected
            ]

        sql = " UNION ALL ".join(unions) + " ORDER BY country_code, ts_utc LIMIT ?"
        params: list = []
        for _ in selected:
            params += [date_from, date_to]
        params.append(limit)

        cols = ["country_code", "ts_utc", "price_eur", "resolution", "points_aggregated"]
        return [dict(zip(cols, r)) for r in self.con.execute(sql, params).fetchall()]

    def daily_stats(self, countries: list[str], date_from: date, date_to: date) -> list[dict]:
        tables = self.available_tables()
        selected = [t for c, t in tables.items() if c in countries]
        if not selected:
            return []
        sql = " UNION ALL ".join(
            f"""SELECT country_code, delivery_date, avg(price_eur), min(price_eur),
                       max(price_eur), count(*)
                FROM {t} WHERE delivery_date BETWEEN ? AND ? GROUP BY 1, 2"""
            for t in selected)
        params: list = []
        for _ in selected:
            params += [date_from, date_to]
        cols = ["country_code", "delivery_date", "avg_eur", "min_eur", "max_eur", "points"]
        rows = self.con.execute(sql + " ORDER BY 2, 1", params).fetchall()
        return [dict(zip(cols, r)) for r in rows]
