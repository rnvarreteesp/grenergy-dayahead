"""API REST que expone los precios Day Ahead cargados por el ETL.

    uvicorn api.main:app --reload --port 8000    (con PYTHONPATH=src)

Todos los endpoints de datos exigen Bearer JWT; /health y /docs quedan abiertos
para poder monitorizar y explorar el contrato.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .repository import PriceRepository
from .schemas import CountryInfo, DailyStat, PriceSeries, TokenRequest, TokenResponse
from .security import RateLimiter, issue_token, require_token

load_dotenv()

app = FastAPI(
    title="Grenergy Day Ahead API",
    version="1.0.0",
    description="Precios Day Ahead de ES, RO, DE y PL normalizados a UTC y EUR/MWh.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("DAYAHEAD_CORS_ORIGINS", "*").split(",")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

limiter = RateLimiter(max_requests=120, window_seconds=60)
_repo: PriceRepository | None = None


def repo() -> PriceRepository:
    global _repo
    if _repo is None:
        _repo = PriceRepository()
    return _repo


def guard(request: Request, claims: Annotated[dict, Depends(require_token)]) -> dict:
    limiter.check(claims.get("sub", request.client.host if request.client else "anon"))
    return claims


@app.get("/health", tags=["infra"])
def health() -> dict:
    try:
        countries = [c["country_code"] for c in repo().countries()]
        return {"status": "ok", "countries": countries}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "detail": str(exc)}


@app.post("/auth/token", response_model=TokenResponse, tags=["auth"])
def token(body: TokenRequest) -> dict:
    """Intercambia la API key por un JWT de corta duracion."""
    return issue_token(body.api_key)


@app.get("/api/v1/countries", response_model=list[CountryInfo], tags=["datos"])
def countries(_: Annotated[dict, Depends(guard)]) -> list[dict]:
    return repo().countries()


@app.get("/api/v1/prices", response_model=PriceSeries, tags=["datos"])
def prices(
    _: Annotated[dict, Depends(guard)],
    countries: str = Query("ES,RO,DE,PL", description="Codigos ISO separados por coma"),
    date_from: date = Query(default_factory=lambda: date.today() - timedelta(days=2), alias="from"),
    date_to: date = Query(default_factory=lambda: date.today() + timedelta(days=1), alias="to"),
    granularity: str | None = Query(None, pattern="^(PT15M|PT60M)$",
                                    description="Homogeneiza la granularidad (media dentro del bucket)"),
) -> dict:
    if date_to < date_from:
        raise HTTPException(400, "'to' no puede ser anterior a 'from'")
    if (date_to - date_from).days > 366:
        raise HTTPException(400, "Rango maximo 366 dias")
    codes = [c.strip().upper() for c in countries.split(",") if c.strip()]
    rows = repo().prices(codes, date_from, date_to, granularity)
    series: dict[str, list[dict]] = {c: [] for c in codes}
    for r in rows:
        series.setdefault(r["country_code"], []).append(
            {"ts_utc": r["ts_utc"], "price_eur": round(r["price_eur"], 4) if r["price_eur"] is not None else None})
    return {
        "from": date_from, "to": date_to, "currency": "EUR", "unit": "EUR/MWh",
        "granularity": granularity or "native", "series": series,
    }


@app.get("/api/v1/stats/daily", response_model=list[DailyStat], tags=["datos"])
def daily(
    _: Annotated[dict, Depends(guard)],
    countries: str = Query("ES,RO,DE,PL"),
    date_from: date = Query(default_factory=lambda: date.today() - timedelta(days=7), alias="from"),
    date_to: date = Query(default_factory=lambda: date.today() + timedelta(days=1), alias="to"),
) -> list[dict]:
    codes = [c.strip().upper() for c in countries.split(",") if c.strip()]
    return repo().daily_stats(codes, date_from, date_to)


# La interfaz se sirve desde la misma app para evitar problemas de CORS en local.
_web = os.path.join(os.path.dirname(__file__), "..", "..", "web")
if os.path.isdir(_web):
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
