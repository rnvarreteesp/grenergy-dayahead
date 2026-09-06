"""Orquestador del ETL.

    python -m dayahead.run --countries ES,RO,DE,PL --lookback 3
    python -m dayahead.run --from 2026-08-01 --to 2026-08-31 --sink duckdb

Ventana por defecto: D-2 .. D+1. El "+1" recoge la subasta del dia siguiente en
cuanto se publica (13:00 CET) y el "-2" vuelve a pasar por dias ya cargados
para tapar huecos y recoger revisiones sin duplicar (la escritura es MERGE).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta

from .config import AppConfig, CountryConfig, load_config
from .connectors import get_connector
from .fx import FxRates
from .http import HttpClient
from .sinks.base import Sink
from .transform import assess, deduplicate

log = logging.getLogger("dayahead")


def _daterange(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def run_country(country: CountryConfig, start: date, end: date, cfg: AppConfig,
                http: HttpClient, fx: FxRates, sink: Sink) -> dict:
    """Ingesta de un pais. Un fallo aqui no debe tumbar al resto (aislamiento)."""
    log.info("[%s] %s .. %s", country.code, start, end)
    fx.load(country.currency, start, end)

    connector = get_connector(country.connector)(country, http, fx)
    points = deduplicate(list(connector.fetch(start, end)))
    quality = assess(points, _daterange(start, end), country.resolution, country.timezone, country.code)
    written = sink.upsert(country.table, points)

    return {
        "country": country.code,
        "table": country.table,
        "rows": written,
        "days_complete": sum(1 for q in quality if q.complete),
        "days_incomplete": [str(q.delivery_date) for q in quality if not q.complete],
        "missing_points": sum(q.missing for q in quality),
    }


def build_sink(name: str, cfg: AppConfig, db_path: str) -> Sink:
    if name == "duckdb":
        from .sinks.duckdb_sink import DuckDBSink
        return DuckDBSink(db_path)
    if name == "delta":
        from pyspark.sql import SparkSession  # solo disponible dentro de Fabric
        from .sinks.delta_sink import DeltaSink
        return DeltaSink(SparkSession.builder.getOrCreate())
    raise ValueError(f"Sink desconocido: {name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ETL precios Day Ahead multi-pais")
    ap.add_argument("--countries", help="Codigos separados por coma. Por defecto: todos los enabled")
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD (fecha de entrega local)")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    ap.add_argument("--lookback", type=int, default=2, help="Dias hacia atras si no se da --from")
    ap.add_argument("--horizon", type=int, default=1, help="Dias hacia delante si no se da --to")
    ap.add_argument("--sink", default=os.getenv("DAYAHEAD_SINK", "duckdb"), choices=["duckdb", "delta"])
    ap.add_argument("--db", default=os.getenv("DAYAHEAD_DB", "data/dayahead.duckdb"))
    ap.add_argument("--config", default=None)
    ap.add_argument("--fail-on-gaps", action="store_true", help="Salir con codigo 2 si algun dia queda incompleto")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    cfg = load_config(args.config)
    today = date.today()
    start = date.fromisoformat(args.date_from) if args.date_from else today - timedelta(days=args.lookback)
    end = date.fromisoformat(args.date_to) if args.date_to else today + timedelta(days=args.horizon)

    selected = cfg.enabled_countries
    if args.countries:
        wanted = {c.strip().upper() for c in args.countries.split(",")}
        selected = [c for c in cfg.countries if c.code.upper() in wanted]

    http = HttpClient(cfg.http.timeout_seconds, cfg.http.max_retries, cfg.http.backoff_seconds)
    fx = FxRates(cfg.fx, http)
    sink = build_sink(args.sink, cfg, args.db)

    results, failures = [], []
    for country in selected:
        try:
            results.append(run_country(country, start, end, cfg, http, fx, sink))
        except Exception as exc:  # noqa: BLE001
            log.exception("[%s] fallo la ingesta", country.code)
            failures.append({"country": country.code, "error": str(exc)})
    sink.close()

    summary = {"window": {"from": str(start), "to": str(end)}, "results": results, "failures": failures}
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if failures:
        return 1
    if args.fail_on_gaps and any(r["missing_points"] for r in results):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
