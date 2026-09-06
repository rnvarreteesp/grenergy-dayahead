"""Interfaz de escritura. El orquestador no sabe si escribe en DuckDB o en Fabric."""
from __future__ import annotations

import abc
from typing import Sequence

from ..models import PricePoint


class Sink(abc.ABC):
    @abc.abstractmethod
    def upsert(self, table: str, points: Sequence[PricePoint]) -> int:
        """Escritura idempotente sobre (country_code, ts_utc, resolution). Devuelve filas escritas."""

    def close(self) -> None:  # pragma: no cover
        pass
