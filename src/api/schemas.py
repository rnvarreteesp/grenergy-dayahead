from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    api_key: str = Field(..., min_length=8, description="Clave entregada al cliente")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class CountryInfo(BaseModel):
    country_code: str
    resolution: str
    source_currency: str
    first_date: date
    last_date: date
    points: int


class PricePointOut(BaseModel):
    ts_utc: datetime
    price_eur: Optional[float]


class PriceSeries(BaseModel):
    from_: date = Field(..., alias="from")
    to: date
    currency: str
    unit: str
    granularity: str
    series: dict[str, list[PricePointOut]]

    model_config = {"populate_by_name": True}


class DailyStat(BaseModel):
    country_code: str
    delivery_date: date
    avg_eur: Optional[float]
    min_eur: Optional[float]
    max_eur: Optional[float]
    points: int
