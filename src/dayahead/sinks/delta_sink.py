"""Sink Delta Lake para el Lakehouse de Microsoft Fabric.

Se usa MERGE en vez de append para que una recarga del mismo dia actualice en
sitio en lugar de duplicar: ENTSO-E revisa precios y el bloque semanal de SMARD
se solapa con la ejecucion anterior, asi que el pipeline se ejecuta tantas
veces como haga falta con el mismo resultado.
"""
from __future__ import annotations

import logging
from typing import Sequence

from ..models import COLUMNS, PricePoint
from .base import Sink

log = logging.getLogger(__name__)

_MERGE_KEYS = ["country_code", "ts_utc", "resolution"]


class DeltaSink(Sink):
    def __init__(self, spark, schema: str = "dbo"):
        self.spark = spark
        self.schema = schema

    def upsert(self, table: str, points: Sequence[PricePoint]) -> int:
        if not points:
            return 0
        from delta.tables import DeltaTable

        full = f"{self.schema}.{table}"
        df = self.spark.createDataFrame([p.as_row() for p in points]).select(*COLUMNS)
        df = df.repartition("delivery_date")

        if not self.spark.catalog.tableExists(full):
            (df.write.format("delta")
               .partitionBy("delivery_date")
               .mode("overwrite")
               .saveAsTable(full))
            log.info("Creada %s con %s filas", full, df.count())
            return df.count()

        condition = " AND ".join(f"t.{k} = s.{k}" for k in _MERGE_KEYS)
        (DeltaTable.forName(self.spark, full).alias("t")
            .merge(df.alias("s"), condition)
            .whenMatchedUpdateAll()
            .whenNotMatchedInsertAll()
            .execute())
        log.info("MERGE sobre %s: %s filas de origen", full, len(points))
        return len(points)
