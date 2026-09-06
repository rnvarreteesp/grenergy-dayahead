"""Regresion del sink de Fabric.

Estos tests no necesitan una sesion de Spark (ni JVM): usan las utilidades de
tipos de PySpark, que son Python puro. Se saltan si pyspark no esta instalado,
porque en local el sink que se usa es DuckDB; en Fabric pyspark siempre esta.

Motivo del test: la primera version creaba el DataFrame sin esquema y Spark
intentaba inferirlo. En ES, RO y DE la columna `fx_rate` es nula en todas las
filas (ya publican en EUR), Spark no puede deducir el tipo de una columna
enteramente nula y la ingesta reventaba con CANNOT_DETERMINE_TYPE. Polonia no
fallaba porque ahi `fx_rate` si trae valores.
"""
from __future__ import annotations

from datetime import date, datetime
from functools import reduce

import pytest

pyspark_types = pytest.importorskip("pyspark.sql.types",
                                    reason="pyspark solo esta disponible dentro de Fabric")

from dayahead.models import COLUMNS, PricePoint  # noqa: E402
from dayahead.sinks.delta_sink import price_schema  # noqa: E402


def _point(fx_rate: float | None) -> PricePoint:
    ts = datetime(2026, 9, 4, 22, 0)
    return PricePoint(
        country_code="ES", bidding_zone="10YES-REE------0", ts_utc=ts, ts_local=ts,
        delivery_date=date(2026, 9, 5), resolution="PT15M", price_original=100.5,
        currency_original="EUR", price_eur=100.5, fx_rate=fx_rate, source="entsoe",
        ingested_at_utc=datetime(2026, 9, 5, 10, 0))


def _rows(points):
    return [tuple(p.as_row()[c] for c in COLUMNS) for p in points]


class TestEsquemaExplicito:
    def test_orden_identico_a_columns(self):
        assert [f.name for f in price_schema().fields] == COLUMNS

    def test_fx_rate_es_double_nullable(self):
        fx = next(f for f in price_schema().fields if f.name == "fx_rate")
        assert fx.dataType.simpleString() == "double"
        assert fx.nullable is True

    def test_columnas_clave_no_admiten_nulos(self):
        no_nulos = {f.name for f in price_schema().fields if not f.nullable}
        assert {"country_code", "ts_utc", "resolution"} <= no_nulos


class TestRegresionCannotDetermineType:
    def test_la_inferencia_falla_con_fx_rate_todo_nulo(self):
        """Documenta el fallo original: sin esquema, Spark no puede inferir."""
        inferido = reduce(pyspark_types._merge_type,
                          (pyspark_types._infer_schema(p.as_row()) for p in [_point(None)] * 4))
        nulos = [f.name for f in inferido.fields
                 if isinstance(f.dataType, pyspark_types.NullType)]
        assert nulos == ["fx_rate"], "si esto cambia, revisar por que se anadio el esquema"

    def test_el_esquema_explicito_acepta_fx_rate_nulo(self):
        verificar = pyspark_types._make_type_verifier(price_schema())
        for fila in _rows([_point(None)] * 4):      # ES / RO / DE
            verificar(fila)

    def test_el_esquema_explicito_acepta_fx_rate_con_valor(self):
        verificar = pyspark_types._make_type_verifier(price_schema())
        for fila in _rows([_point(4.3265)] * 4):    # PL
            verificar(fila)
