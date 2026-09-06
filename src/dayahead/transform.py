"""Validaciones de calidad previas a la escritura.

El requisito del enunciado es "carga incremental diaria SIN HUECOS", asi que la
deteccion de huecos es parte del pipeline, no un extra: si un dia llega
incompleto se registra y se puede reintentar sin duplicar nada (la escritura es
un MERGE idempotente sobre la clave natural).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from .models import RESOLUTION_MINUTES, PricePoint

log = logging.getLogger(__name__)


@dataclass
class DayQuality:
    country_code: str
    delivery_date: date
    expected: int
    received: int

    @property
    def complete(self) -> bool:
        return self.received >= self.expected

    @property
    def missing(self) -> int:
        return max(self.expected - self.received, 0)


def expected_points(day: date, resolution: str, timezone_name: str) -> int:
    """Puntos esperados en un dia LOCAL.

    Contempla el cambio de hora: el ultimo domingo de marzo el dia tiene 23h
    (92 cuartos) y el de octubre 25h (100 cuartos). Contarlo bien evita falsas
    alarmas de hueco dos dias al ano.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone_name)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    minutes = (end.astimezone(ZoneInfo("UTC")) - start.astimezone(ZoneInfo("UTC"))).total_seconds() / 60
    return int(minutes // RESOLUTION_MINUTES[resolution])


def assess(points: Sequence[PricePoint], days: Iterable[date], resolution: str,
           timezone_name: str, country_code: str) -> list[DayQuality]:
    counts: dict[date, int] = defaultdict(int)
    for p in points:
        counts[p.delivery_date] += 1
    report = []
    for day in days:
        q = DayQuality(country_code, day, expected_points(day, resolution, timezone_name), counts.get(day, 0))
        if not q.complete:
            log.warning("%s %s incompleto: %s/%s puntos (faltan %s)",
                        country_code, day, q.received, q.expected, q.missing)
        report.append(q)
    return report


def deduplicate(points: Sequence[PricePoint]) -> list[PricePoint]:
    """Ultimo valor gana para una misma clave natural (reprocesos, solapes de bloque)."""
    seen: dict[tuple, PricePoint] = {}
    for p in points:
        seen[(p.country_code, p.ts_utc, p.resolution)] = p
    return sorted(seen.values(), key=lambda x: x.ts_utc)
