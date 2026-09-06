"""Conversion a EUR.

Solo Polonia publica en moneda local (PLN/MWh). Se usa el tipo de referencia
diario del BCE (PLN por 1 EUR) y se aplica el tipo del DIA DE ENTREGA en hora
local del mercado, no el del momento de la ingesta: asi una recarga historica
reproduce exactamente los mismos numeros (idempotencia).
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, timedelta

from .config import FxConfig
from .http import HttpClient

log = logging.getLogger(__name__)

# Margen hacia atras para cubrir fines de semana y festivos TARGET.
_LOOKBACK_DAYS = 10


class FxRates:
    """Tipos de cambio diarios con arrastre del ultimo dia habil."""

    def __init__(self, cfg: FxConfig, http: HttpClient):
        self.cfg = cfg
        self.http = http
        self._cache: dict[tuple[str, date], float] = {}

    def load(self, currency: str, start: date, end: date) -> None:
        if currency.upper() == "EUR":
            return
        url = self.cfg.series_url.format(currency=currency.upper())
        resp = self.http.get(url, params={
            "startPeriod": (start - timedelta(days=_LOOKBACK_DAYS)).isoformat(),
            "endPeriod": end.isoformat(),
            "format": "csvdata",
        })
        reader = csv.DictReader(io.StringIO(resp.text))
        found = 0
        for row in reader:
            value = row.get("OBS_VALUE")
            if not value:
                continue
            self._cache[(currency.upper(), date.fromisoformat(row["TIME_PERIOD"]))] = float(value)
            found += 1
        log.info("FX %s/EUR: %s tipos cargados (%s..%s)", currency, found, start, end)
        if not found:
            raise RuntimeError(f"El BCE no devolvio tipos para {currency} en {start}..{end}")

    def rate(self, currency: str, day: date) -> float:
        """Tipo del dia; si no hay publicacion (finde/festivo) arrastra el anterior."""
        currency = currency.upper()
        if currency == "EUR":
            return 1.0
        for back in range(_LOOKBACK_DAYS + 1):
            hit = self._cache.get((currency, day - timedelta(days=back)))
            if hit is not None:
                return hit
        raise RuntimeError(f"Sin tipo de cambio {currency}/EUR para {day} ni los {_LOOKBACK_DAYS} dias previos")

    def to_eur(self, amount: float, currency: str, day: date) -> tuple[float, float]:
        rate = self.rate(currency, day)
        return round(amount / rate, 4), rate
