"""Dobles de prueba: los tests no tocan la red."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dayahead.config import CountryConfig  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    @property
    def text(self) -> str:
        return self._payload if isinstance(self._payload, str) else json.dumps(self._payload)

    def json(self):
        return json.loads(self._payload) if isinstance(self._payload, str) else self._payload


class FakeHttp:
    """Devuelve la respuesta cuya clave aparece en la URL. Registra las llamadas."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, params: dict | None = None) -> FakeResponse:
        self.calls.append((url, params))
        for key, payload in self.routes.items():
            if key in url:
                if callable(payload):
                    return FakeResponse(payload(params))
                return FakeResponse(payload)
        raise AssertionError(f"URL no esperada en el test: {url}")


class FakeFx:
    """Tipo fijo para que los tests sean deterministas."""

    def __init__(self, rate: float = 4.0):
        self.rate = rate

    def load(self, currency, start, end):
        pass

    def to_eur(self, amount: float, currency: str, day: date):
        if currency == "EUR":
            return round(amount, 4), 1.0
        return round(amount / self.rate, 4), self.rate


@pytest.fixture
def fx():
    return FakeFx()


@pytest.fixture
def country_es():
    return CountryConfig(
        code="ES", name="Espana", connector="entsoe", timezone="Europe/Madrid",
        resolution="PT15M", currency="EUR", table="dayahead_prices_es",
        params={
            "base_url": "https://web-api.tp.entsoe.eu/api",
            "document_type": "A44",
            "domain": "10YES-REE------0",
            "contract_market_agreement_type": "A01",
            "security_token_env": "ENTSOE_SECURITY_TOKEN",
        },
    )


@pytest.fixture
def country_pl():
    return CountryConfig(
        code="PL", name="Polonia", connector="pse", timezone="Europe/Warsaw",
        resolution="PT15M", currency="PLN", table="dayahead_prices_pl",
        params={"base_url": "https://api.raporty.pse.pl/api/rce-pln",
                "timestamp_marks": "interval_end"},
    )


@pytest.fixture
def country_de():
    return CountryConfig(
        code="DE", name="Alemania", connector="smard", timezone="Europe/Berlin",
        resolution="PT60M", currency="EUR", table="dayahead_prices_de",
        params={
            "index_url": "https://www.smard.de/app/chart_data/{filter}/{region}/index_{resolution}.json",
            "block_url": "https://www.smard.de/app/chart_data/{filter}/{region}/{filter}_{region}_{resolution}_{timestamp}.json",
            "filter": "4169", "region": "DE", "smard_resolution": "hour",
        },
    )
