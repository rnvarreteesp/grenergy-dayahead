"""Sink Delta Lake para el Lakehouse de Microsoft Fabric.

Se usa MERGE en vez de append para que una recarga del mismo dia actualice en
sitio en lugar de duplicar: ENTSO-E revisa precios y el bloque semanal de SMARD
se solapa con la ejecucion anterior, asi que el pipeline se ejecuta tantas
veces como haga falta con el mismo resultado.

El esquema se declara de forma EXPLICITA, nunca se deja inferir. En los paises
que ya publican en euros `fx_rate` es nulo en todas las filas, y Spark no puede
deducir el tipo de una columna enteramente nula: falla con CANNOT_DETERMINE_TYPE.
Declararlo ademas garantiza que las cuatro tablas tengan tipos identicos, que es
lo que permite unirlas en la capa de lectura sin conversiones.
"""
from __future__ import annotations

import logging
from typing import Sequence

from ..models import COLUMNS, PricePoint
from .base import Sink

log = logging.getLogger(__name__)

_MERGE_KEYS = ["country_code", "ts_utc", "resolution"]


def price_schema():
    """StructType de la tabla, en el mismo orden que models.COLUMNS."""
    from pyspark.sql.types import (
        DateType, DoubleType, StringType, StructField, StructType, TimestampType,
    )

    fields = {
        "country_code": (StringType(), False),
        "bidding_zone": (StringType(), True),
        "ts_utc": (TimestampType(), False),
        "ts_local": (TimestampType(), False),
        "delivery_date": (DateType(), False),
        "resolution": (StringType(), False),
        "price_original": (DoubleType(), True),
        "currency_original": (StringType(), True),
        "price_eur": (DoubleType(), True),
        "fx_rate": (DoubleType(), True),          # nulo en ES, RO y DE
        "source": (StringType(), True),
        "ingested_at_utc": (TimestampType(), True),
    }
    if set(fields) != set(COLUMNS):
        raise RuntimeError("El esquema Delta y models.COLUMNS se han desincronizado")
    return StructType([StructField(name, *fields[name]) for name in COLUMNS])


class DeltaSink(Sink):
    def __init__(self, spark, schema: str = "dbo"):
        self.spark = spark
        self.schema = schema

    def upsert(self, table: str, points: Sequence[PricePoint]) -> int:
        if not points:
            return 0
        from delta.tables import DeltaTable

        full = f"{self.schema}.{table}"
        # Tuplas en el orden de COLUMNS + esquema explicito: sin inferencia y sin
        # depender del orden de las claves del diccionario.
        rows = [tuple(p.as_row()[c] for c in COLUMNS) for p in points]
        df = self.spark.createDataFrame(rows, schema=price_schema())
        df = df.repartition("delivery_date")

        if not self.spark.catalog.tableExists(full):
            (df.write.format("delta")
               .partitionBy("delivery_date")
               .mode("overwrite")
               .saveAsTable(full))
            log.info("Creada %s con %s filas", full, len(points))
            return len(points)

        condition = " AND ".join(f"t.{k} = s.{k}" for k in _MERGE_KEYS)
        (DeltaTable.forName(self.spark, full).alias("t")
            .merge(df.alias("s"), condition)
            .whenMatchedUpdateAll()
            .whenNotMatchedInsertAll()
            .execute())
        log.info("MERGE sobre %s: %s filas de origen", full, len(points))
        return len(points)
