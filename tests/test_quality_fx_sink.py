"""Calidad del dato, tipo de cambio e idempotencia de la escritura."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from dayahead.fx import FxRates
from dayahead.models import PricePoint
from dayahead.sinks.duckdb_sink import DuckDBSink
from dayahead.transform import assess, deduplicate, expected_points


class TestCambioDeHora:
    """Dos dias al ano el dia local no tiene 24 horas: contar 96 puntos siempre
    generaria falsas alarmas de hueco."""

    def test_dia_de_23_horas_marzo(self):
        assert expected_points(date(2026, 3, 29), "PT15M", "Europe/Madrid") == 92
        assert expected_points(date(2026, 3, 29), "PT60M", "Europe/Berlin") == 23

    def test_dia_de_25_horas_octubre(self):
        assert expected_points(date(2026, 10, 25), "PT15M", "Europe/Madrid") == 100
        assert expected_points(date(2026, 10, 25), "PT60M", "Europe/Berlin") == 25

    def test_dia_normal(self):
        assert expected_points(date(2026, 9, 3), "PT15M", "Europe/Warsaw") == 96
        assert expected_points(date(2026, 9, 3), "PT60M", "Europe/Berlin") == 24


def _point(ts: datetime, price: float = 10.0, country: str = "ES") -> PricePoint:
    return PricePoint(
        country_code=country, bidding_zone="Z", ts_utc=ts, ts_local=ts,
        delivery_date=ts.date(), resolution="PT15M", price_original=price,
        currency_original="EUR", price_eur=price, fx_rate=None, source="test",
        ingested_at_utc=datetime(2026, 9, 5, tzinfo=timezone.utc))


class TestDeteccionDeHuecos:
    def test_marca_dia_incompleto(self):
        day = date(2026, 9, 3)
        pts = [_point(datetime(2026, 9, 3, 0, 15 * i, tzinfo=timezone.utc)) for i in range(4)]
        report = assess(pts, [day], "PT15M", "Europe/Madrid", "ES")
        assert report[0].complete is False
        assert report[0].missing == 92          # 96 esperados - 4 recibidos

    def test_dia_completo(self):
        day = date(2026, 9, 3)
        pts = [_point(datetime(2026, 9, 3, tzinfo=timezone.utc).replace(hour=i // 4, minute=15 * (i % 4)))
               for i in range(96)]
        report = assess(pts, [day], "PT15M", "Europe/Madrid", "ES")
        assert report[0].complete is True and report[0].missing == 0


class TestDeduplicacion:
    def test_gana_el_ultimo_valor(self):
        ts = datetime(2026, 9, 3, 10, tzinfo=timezone.utc)
        out = deduplicate([_point(ts, 10.0), _point(ts, 99.0)])
        assert len(out) == 1 and out[0].price_original == 99.0

    def test_no_mezcla_paises(self):
        ts = datetime(2026, 9, 3, 10, tzinfo=timezone.utc)
        out = deduplicate([_point(ts, 10.0, "ES"), _point(ts, 20.0, "RO")])
        assert len(out) == 2


class TestTipoDeCambio:
    def test_arrastra_el_ultimo_dia_habil(self):
        """El BCE no publica sabado ni domingo: el precio del sabado usa el viernes."""
        fx = FxRates.__new__(FxRates)
        fx._cache = {("PLN", date(2026, 9, 4)): 4.3148}      # viernes
        assert fx.rate("PLN", date(2026, 9, 5)) == 4.3148    # sabado
        assert fx.rate("PLN", date(2026, 9, 6)) == 4.3148    # domingo

    def test_eur_no_convierte(self):
        fx = FxRates.__new__(FxRates)
        fx._cache = {}
        assert fx.rate("EUR", date(2026, 9, 5)) == 1.0

    def test_error_si_no_hay_tipo(self):
        fx = FxRates.__new__(FxRates)
        fx._cache = {}
        with pytest.raises(RuntimeError, match="Sin tipo de cambio"):
            fx.rate("PLN", date(2026, 9, 5))


class TestEscrituraIdempotente:
    def test_recargar_el_mismo_dia_no_duplica(self, tmp_path):
        """Requisito del enunciado: recargas diarias sin huecos ni duplicados."""
        sink = DuckDBSink(tmp_path / "t.duckdb")
        pts = [_point(datetime(2026, 9, 3, 10, tzinfo=timezone.utc), 10.0)]
        sink.upsert("dayahead_prices_es", pts)
        sink.upsert("dayahead_prices_es", pts)
        sink.upsert("dayahead_prices_es", [_point(datetime(2026, 9, 3, 10, tzinfo=timezone.utc), 55.0)])
        rows = sink.con.execute("SELECT count(*), max(price_eur) FROM dayahead_prices_es").fetchone()
        assert rows == (1, 55.0), "la clave natural actualiza en sitio, no inserta otra fila"
        sink.close()
