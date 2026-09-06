"""Tests de los tres conectores sobre las particularidades reales de cada API."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from dayahead.connectors.entsoe import EntsoeConnector
from dayahead.connectors.pse import PseConnector
from dayahead.connectors.smard import SmardConnector

NS = 'xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"'


def _entsoe_xml(*timeseries: str) -> str:
    return f'<?xml version="1.0" encoding="utf-8"?><Publication_MarketDocument {NS}>' \
           + "".join(timeseries) + "</Publication_MarketDocument>"


def _ts(contract: str, start: str, end: str, points: list[tuple[int, float]]) -> str:
    pts = "".join(
        f"<Point><position>{p}</position><price.amount>{v}</price.amount></Point>" for p, v in points)
    return (f"<TimeSeries><contract_MarketAgreement.type>{contract}</contract_MarketAgreement.type>"
            f"<currency_Unit.name>EUR</currency_Unit.name>"
            f"<Period><timeInterval><start>{start}</start><end>{end}</end></timeInterval>"
            f"<resolution>PT15M</resolution>{pts}</Period></TimeSeries>")


class TestEntsoe:
    """Un dia de mercado espanol en verano va de 22:00Z a 22:00Z (96 cuartos)."""

    DAY = ("2026-09-01T22:00Z", "2026-09-02T22:00Z")

    def _fetch(self, country_es, fx, monkeypatch, xml):
        from tests.conftest import FakeHttp
        monkeypatch.setenv("ENTSOE_SECURITY_TOKEN", "token-de-prueba")
        http = FakeHttp({"entsoe.eu": xml})
        return list(EntsoeConnector(country_es, http, fx).fetch(date(2026, 9, 2), date(2026, 9, 2))), http

    def test_descarta_subastas_intradiarias(self, country_es, fx, monkeypatch):
        """Solo A01 (subasta diaria) es Day Ahead: A07 son intradiarias."""
        xml = _entsoe_xml(
            _ts("A01", *self.DAY, [(1, 100.0)]),
            _ts("A07", *self.DAY, [(1, 999.0)]),
        )
        points, _ = self._fetch(country_es, fx, monkeypatch, xml)
        assert {p.price_original for p in points} == {100.0}
        assert 999.0 not in {p.price_original for p in points}

    def test_rellena_curva_dispersa(self, country_es, fx, monkeypatch):
        """curveType A03: la posicion 1 vale hasta la 5, que trae precio nuevo."""
        xml = _entsoe_xml(_ts("A01", *self.DAY, [(1, 50.0), (5, 60.0), (96, 70.0)]))
        points, _ = self._fetch(country_es, fx, monkeypatch, xml)
        assert len(points) == 96, "deben reconstruirse los 96 cuartos horarios"
        assert [p.price_original for p in points[:4]] == [50.0] * 4
        assert points[4].price_original == 60.0
        assert points[-1].price_original == 70.0

    def test_recorta_fuera_de_ventana(self, country_es, fx, monkeypatch):
        """ENTSO-E devuelve dias completos aunque no se pidan: no deben escribirse."""
        xml = _entsoe_xml(
            _ts("A01", *self.DAY, [(1, 10.0)]),
            _ts("A01", "2026-09-02T22:00Z", "2026-09-03T22:00Z", [(1, 20.0)]),
        )
        points, _ = self._fetch(country_es, fx, monkeypatch, xml)
        assert {p.delivery_date for p in points} == {date(2026, 9, 2)}

    def test_timestamps_en_utc_y_local(self, country_es, fx, monkeypatch):
        xml = _entsoe_xml(_ts("A01", *self.DAY, [(1, 42.0)]))
        points, _ = self._fetch(country_es, fx, monkeypatch, xml)
        first = points[0]
        assert first.ts_utc == datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
        assert first.ts_local.hour == 0 and first.ts_local.day == 2   # medianoche en Madrid
        assert first.delivery_date == date(2026, 9, 2)

    def test_acknowledgement_sin_datos(self, country_es, fx, monkeypatch):
        """Cuando aun no hay subasta, ENTSO-E responde un Acknowledgement, no un error."""
        ack = (f'<Acknowledgement_MarketDocument {NS}><Reason>'
               f"<text>No matching data found</text></Reason></Acknowledgement_MarketDocument>")
        points, _ = self._fetch(country_es, fx, monkeypatch, ack)
        assert points == []


class TestPse:
    def test_desplaza_fin_de_intervalo_a_inicio(self, country_pl, fx):
        """dtime_utc marca el FIN: 22:15 corresponde al intervalo que empieza a las 22:00."""
        from tests.conftest import FakeHttp
        http = FakeHttp({"pse.pl": {"value": [
            {"dtime_utc": "2026-09-02 22:15:00", "rce_pln": 400.0, "business_date": "2026-09-03"},
            {"dtime_utc": "2026-09-02 22:30:00", "rce_pln": 800.0, "business_date": "2026-09-03"},
        ]}})
        points = list(PseConnector(country_pl, http, fx).fetch(date(2026, 9, 3), date(2026, 9, 3)))
        assert points[0].ts_utc == datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
        assert points[1].ts_utc == datetime(2026, 9, 2, 22, 15, tzinfo=timezone.utc)

    def test_convierte_pln_a_eur_conservando_original(self, country_pl, fx):
        from tests.conftest import FakeHttp
        http = FakeHttp({"pse.pl": {"value": [
            {"dtime_utc": "2026-09-02 22:15:00", "rce_pln": 400.0, "business_date": "2026-09-03"}]}})
        p = list(PseConnector(country_pl, http, fx).fetch(date(2026, 9, 3), date(2026, 9, 3)))[0]
        assert p.price_original == 400.0 and p.currency_original == "PLN"
        assert p.price_eur == 100.0 and p.fx_rate == 4.0      # 400 PLN / 4.0

    def test_ignora_precios_nulos(self, country_pl, fx):
        from tests.conftest import FakeHttp
        http = FakeHttp({"pse.pl": {"value": [
            {"dtime_utc": "2026-09-02 22:15:00", "rce_pln": None, "business_date": "2026-09-03"}]}})
        assert list(PseConnector(country_pl, http, fx).fetch(date(2026, 9, 3), date(2026, 9, 3))) == []


class TestSmard:
    LUN = int(datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc).timestamp() * 1000)  # bloque semanal

    def test_descarga_el_bloque_que_contiene_el_dia(self, country_de, fx):
        """El indice da bloques semanales: hay que coger el que ya estaba abierto."""
        from tests.conftest import FakeHttp
        hour = 3_600_000
        http = FakeHttp({
            "index_hour.json": {"timestamps": [self.LUN - 7 * 24 * hour, self.LUN]},
            f"hour_{self.LUN}.json": {"series": [
                [self.LUN + 24 * hour, 100.0],       # 2026-08-31 22:00Z -> dia 09-01 local
                [self.LUN + 25 * hour, None],        # hora sin publicar
                [self.LUN + 26 * hour, 120.0],
            ]},
        })
        points = list(SmardConnector(country_de, http, fx).fetch(date(2026, 9, 1), date(2026, 9, 1)))
        assert [p.price_original for p in points] == [100.0, 120.0], "los null se descartan"
        assert all(p.resolution == "PT60M" for p in points)

    def test_no_devuelve_puntos_fuera_del_rango(self, country_de, fx):
        from tests.conftest import FakeHttp
        hour = 3_600_000
        http = FakeHttp({
            "index_hour.json": {"timestamps": [self.LUN]},
            f"hour_{self.LUN}.json": {"series": [
                [self.LUN, 10.0],                    # 08-30 22:00Z -> dia 08-31, fuera
                [self.LUN + 24 * hour, 20.0],        # dentro
            ]},
        })
        points = list(SmardConnector(country_de, http, fx).fetch(date(2026, 9, 1), date(2026, 9, 1)))
        assert [p.price_original for p in points] == [20.0]
