"""Esquema canonico al que se normalizan las cuatro fuentes."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, date
from typing import Optional

# Resoluciones soportadas -> duracion en minutos.
RESOLUTION_MINUTES = {"PT15M": 15, "PT30M": 30, "PT60M": 60}


@dataclass(frozen=True)
class PricePoint:
    """Un precio Day Ahead para un intervalo concreto.

    `ts_utc` es SIEMPRE el INICIO del intervalo en UTC. Las fuentes que
    publican el fin del intervalo (PSE) se desplazan en el conector, de forma
    que aguas abajo todo el sistema comparte la misma convencion.
    """

    country_code: str
    bidding_zone: str
    ts_utc: datetime           # inicio de intervalo, tz-aware UTC
    ts_local: datetime         # mismo instante en hora local del mercado
    delivery_date: date        # dia de entrega en hora local
    resolution: str            # PT15M | PT60M
    price_original: float
    currency_original: str
    price_eur: Optional[float]
    fx_rate: Optional[float]   # unidades de moneda origen por 1 EUR
    source: str
    ingested_at_utc: datetime

    def as_row(self) -> dict:
        d = asdict(self)
        d["ts_utc"] = self.ts_utc.replace(tzinfo=None)
        d["ts_local"] = self.ts_local.replace(tzinfo=None)
        d["ingested_at_utc"] = self.ingested_at_utc.replace(tzinfo=None)
        return d


# Clave natural de la tabla. La carga es idempotente sobre esta clave.
PRIMARY_KEY = ("country_code", "ts_utc", "resolution")

COLUMNS = [
    "country_code", "bidding_zone", "ts_utc", "ts_local", "delivery_date",
    "resolution", "price_original", "currency_original", "price_eur",
    "fx_rate", "source", "ingested_at_utc",
]
