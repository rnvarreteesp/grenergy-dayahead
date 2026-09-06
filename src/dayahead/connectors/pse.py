"""PSE - Polskie Sieci Elektroenergetyczne (Polonia).

Dos cosas que hay que tratar:

1. La moneda es PLN/MWh -> se convierte a EUR con el tipo del BCE del dia de
   entrega (ver fx.py). Se conserva el precio original y el tipo aplicado para
   poder auditar la conversion.
2. `dtime_utc` marca el FINAL del intervalo (el registro 00:15 corresponde al
   periodo 00:00-00:15). Se resta la duracion del intervalo para dejar todos
   los paises con la misma convencion de inicio de intervalo.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from ..models import RESOLUTION_MINUTES, PricePoint
from .base import Connector, register

log = logging.getLogger(__name__)


@register("pse")
class PseConnector(Connector):

    def fetch(self, start: date, end: date) -> Iterable[PricePoint]:
        p = self.country.params
        shift = timedelta(minutes=RESOLUTION_MINUTES[self.country.resolution]) \
            if p.get("timestamp_marks") == "interval_end" else timedelta(0)

        out: list[PricePoint] = []
        day = start
        while day <= end:
            # $filter sobre business_date: un dia de entrega por peticion.
            resp = self.http.get(p["base_url"], params={"$filter": f"business_date eq '{day.isoformat()}'"})
            rows = resp.json().get("value", [])
            if not rows:
                log.warning("PSE: sin datos para %s", day)
            for row in rows:
                if row.get("rce_pln") is None:
                    continue
                ts_utc = datetime.strptime(row["dtime_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) - shift
                out.append(self._build(ts_utc, float(row["rce_pln"])))
            day += timedelta(days=1)
        out.sort(key=lambda x: x.ts_utc)
        return out
