"""Carga de config/sources.yaml -> objetos tipados."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"


@dataclass
class HttpSettings:
    timeout_seconds: int = 60
    max_retries: int = 4
    backoff_seconds: float = 2.0


@dataclass
class CountryConfig:
    code: str
    name: str
    connector: str
    timezone: str
    resolution: str
    currency: str
    table: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class FxConfig:
    provider: str
    series_url: str
    fill_method: str = "forward"


@dataclass
class AppConfig:
    countries: list[CountryConfig]
    fx: FxConfig
    http: HttpSettings
    target_currency: str = "EUR"

    def country(self, code: str) -> CountryConfig:
        for c in self.countries:
            if c.code.upper() == code.upper():
                return c
        raise KeyError(f"Pais no configurado: {code}")

    @property
    def enabled_countries(self) -> list[CountryConfig]:
        return [c for c in self.countries if c.enabled]


def load_config(path: str | Path | None = None) -> AppConfig:
    path = Path(path or os.getenv("DAYAHEAD_CONFIG", DEFAULT_CONFIG))
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {})
    http = HttpSettings(**defaults.get("http", {}))
    countries = [CountryConfig(**c) for c in raw.get("countries", [])]
    return AppConfig(
        countries=countries,
        fx=FxConfig(**raw["fx"]),
        http=http,
        target_currency=defaults.get("target_currency", "EUR"),
    )
