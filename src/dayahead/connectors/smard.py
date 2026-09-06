"""SMARD - Bundesnetzagentur (Alemania).

La API no acepta rango de fechas: publica BLOQUES SEMANALES. El indice
devuelve los timestamps (epoch ms) de inicio de cada bloque y hay que
descargar los bloques que solapan el rango pedido y recortar despues.
Los timestamps son UTC reales, asi que el cambio de hora sale correcto sin
tratamiento especial. Las horas futuras vienen con valor null y se descartan.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Iterable

from ..models import PricePoint
from .base import Connector, register

log = logging.getLogger(__name__)


@register("smard")
class SmardConnector(Connector):

    def fetch(self, start: date, end: date) -> Iterable[PricePoint]:
        p = self.country.params
        fmt = {"filter": p["filter"], "region": p["region"], "resolution": p["smard_resolution"]}
        lo, hi = self.local_day_bounds_utc(start, end)

        index = self.http.get(p["index_url"].format(**fmt)).json()
        blocks = self._blocks_covering(index["timestamps"], lo, hi)
        if not blocks:
            log.warning("SMARD %s: ningun bloque cubre %s..%s", self.country.code, start, end)
            return []

        out: list[PricePoint] = []
        for block in blocks:
            url = p["block_url"].format(timestamp=block, **fmt)
            series = self.http.get(url).json().get("series", [])
            for epoch_ms, value in series:
                if value is None:
                    continue                       # hora aun no publicada
                ts_utc = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
                if lo <= ts_utc < hi:
                    out.append(self._build(ts_utc, float(value)))
        out.sort(key=lambda x: x.ts_utc)
        return out

    @staticmethod
    def _blocks_covering(timestamps: list[int], lo: datetime, hi: datetime) -> list[int]:
        """Bloques semanales que solapan [lo, hi): el que contiene lo, y los siguientes."""
        lo_ms, hi_ms = lo.timestamp() * 1000, hi.timestamp() * 1000
        ordered = sorted(timestamps)
        selected = [t for t in ordered if lo_ms <= t < hi_ms]
        previous = [t for t in ordered if t <= lo_ms]
        if previous:
            selected.insert(0, previous[-1])       # el bloque que ya estaba abierto
        return selected
