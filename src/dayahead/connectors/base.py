"""Contrato comun de los conectores + registro por nombre.

Anadir una fuente nueva = subclase de Connector + @register("nombre").
El orquestador no conoce ninguna fuente concreta.
"""
from __future__ import annotations

import abc
from datetime import date, datetime, timezone
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from ..config import CountryConfig
from ..fx import FxRates
from ..http import HttpClient
from ..models import PricePoint

_REGISTRY: dict[str, type["Connector"]] = {}


def register(name: str) -> Callable[[type["Connector"]], type["Connector"]]:
    def _wrap(cls: type["Connector"]) -> type["Connector"]:
        _REGISTRY[name] = cls
        cls.connector_name = name
        return cls
    return _wrap


def get_connector(name: str) -> type["Connector"]:
    if name not in _REGISTRY:
        raise KeyError(f"Conector desconocido '{name}'. Disponibles: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


class Connector(abc.ABC):
    connector_name: str = "base"

    def __init__(self, country: CountryConfig, http: HttpClient, fx: FxRates):
        self.country = country
        self.http = http
        self.fx = fx
        self.tz = ZoneInfo(country.timezone)

    @abc.abstractmethod
    def fetch(self, start: date, end: date) -> Iterable[PricePoint]:
        """Devuelve los puntos de [start, end] ambos inclusive (fechas de entrega locales)."""

    # -- utilidades compartidas ------------------------------------------------

    def _build(self, ts_utc: datetime, price: float) -> PricePoint:
        ts_local = ts_utc.astimezone(self.tz)
        price_eur, rate = self.fx.to_eur(price, self.country.currency, ts_local.date())
        return PricePoint(
            country_code=self.country.code,
            bidding_zone=str(self.country.params.get("domain", self.country.code)),
            ts_utc=ts_utc,
            ts_local=ts_local,
            delivery_date=ts_local.date(),
            resolution=self.country.resolution,
            price_original=round(price, 4),
            currency_original=self.country.currency,
            price_eur=price_eur,
            fx_rate=None if self.country.currency == "EUR" else rate,
            source=self.connector_name,
            ingested_at_utc=datetime.now(timezone.utc),
        )

    def local_day_bounds_utc(self, start: date, end: date) -> tuple[datetime, datetime]:
        """[start 00:00 local, end+1 00:00 local) expresado en UTC."""
        from datetime import timedelta
        lo = datetime.combine(start, datetime.min.time(), tzinfo=self.tz)
        hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=self.tz)
        return lo.astimezone(timezone.utc), hi.astimezone(timezone.utc)
