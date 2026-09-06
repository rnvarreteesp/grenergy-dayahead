"""ENTSO-E Transparency Platform (Espana y Rumania).

Tres particularidades reales de esta API, verificadas contra el endpoint:

1. Una peticion A44 devuelve VARIAS TimeSeries por dia. Se distinguen por
   `contract_MarketAgreement.type`: A01 es la subasta diaria (Day Ahead) y A07
   las intradiarias. Sin filtrar por A01 se mezclan precios de mercados
   distintos en la misma tabla.
2. `curveType` A03 (bloques de tamano variable): los puntos se publican de
   forma dispersa y una posicion es valida HASTA la siguiente declarada. Hay
   que rellenar hacia delante o faltan hasta 15 de los 96 cuartos horarios.
3. El dia de mercado va de 00:00 local a 00:00 local, por lo que en UTC empieza
   a las 22:00Z o 23:00Z segun el horario de verano.
"""
from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from ..models import RESOLUTION_MINUTES, PricePoint
from .base import Connector, register

log = logging.getLogger(__name__)

_ACK_TAG = "Acknowledgement_MarketDocument"


@register("entsoe")
class EntsoeConnector(Connector):

    def fetch(self, start: date, end: date) -> Iterable[PricePoint]:
        p = self.country.params
        token = os.getenv(p["security_token_env"])
        if not token:
            raise RuntimeError(f"Falta la variable de entorno {p['security_token_env']}")

        lo, hi = self.local_day_bounds_utc(start, end)
        resp = self.http.get(p["base_url"], params={
            "documentType": p["document_type"],
            "in_Domain": p["domain"],
            "out_Domain": p["domain"],
            "periodStart": lo.strftime("%Y%m%d%H%M"),
            "periodEnd": hi.strftime("%Y%m%d%H%M"),
            "securityToken": token,
        })

        root = ET.fromstring(resp.text)
        ns = {"n": root.tag.split("}")[0].strip("{")}

        if root.tag.endswith(_ACK_TAG):
            reason = root.findtext(".//n:Reason/n:text", default="sin detalle", namespaces=ns)
            log.warning("ENTSO-E %s sin datos para %s..%s: %s", self.country.code, start, end, reason)
            return []

        wanted = p.get("contract_market_agreement_type")
        points: dict[datetime, float] = {}

        for ts in root.findall("n:TimeSeries", ns):
            contract = ts.findtext("n:contract_MarketAgreement.type", namespaces=ns)
            if wanted and contract != wanted:
                continue                     # descarta intradiarios (A07)
            for period in ts.findall("n:Period", ns):
                points.update(self._parse_period(period, ns))

        if not points:
            log.warning("ENTSO-E %s: 0 puntos tras filtrar contract=%s", self.country.code, wanted)

        # ENTSO-E puede devolver dias completos fuera del rango pedido (la
        # respuesta se alinea a dias de mercado): se recorta a la ventana.
        return [self._build(ts_utc, price)
                for ts_utc, price in sorted(points.items()) if lo <= ts_utc < hi]

    def _parse_period(self, period: ET.Element, ns: dict) -> dict[datetime, float]:
        """Expande un Period a puntos equiespaciados rellenando huecos (curveType A03)."""
        start = _parse_ts(period.findtext("n:timeInterval/n:start", namespaces=ns))
        end = _parse_ts(period.findtext("n:timeInterval/n:end", namespaces=ns))
        resolution = period.findtext("n:resolution", namespaces=ns)
        step = RESOLUTION_MINUTES.get(resolution)
        if step is None:
            log.warning("Resolucion no soportada %s, Period ignorado", resolution)
            return {}

        by_position = {
            int(pt.findtext("n:position", namespaces=ns)): float(pt.findtext("n:price.amount", namespaces=ns))
            for pt in period.findall("n:Point", ns)
        }
        if not by_position:
            return {}

        total = int((end - start).total_seconds() // 60 // step)
        out: dict[datetime, float] = {}
        last: float | None = None
        for position in range(1, total + 1):
            last = by_position.get(position, last)   # forward fill
            if last is None:
                continue                              # aun no hay primer valor
            out[start + timedelta(minutes=step * (position - 1))] = last
        return out


def _parse_ts(value: str) -> datetime:
    """'2026-09-01T22:00Z' -> datetime aware UTC."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
