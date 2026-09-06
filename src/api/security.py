"""Seguridad de la API.

Mecanismo elegido: API key de larga duracion -> JWT (HS256) de corta duracion
como Bearer token. La justificacion completa esta en
docs/DECISIONES_TECNICAS.md, en resumen:

  - Sin estado en servidor: la API puede escalar horizontalmente sin sesiones
    compartidas, que es como se despliega detras de Fabric/Azure.
  - El secreto de larga duracion (API key) viaja una sola vez, no en cada
    peticion, y se puede revocar sin tocar a los demas clientes.
  - El token corto lleva claims (sub, scope, exp) y permite que la interfaz
    web lo guarde en memoria sin exponer la credencial permanente.

En produccion sobre Azure lo natural seria delegar en Entra ID (OAuth2 client
credentials) y validar tokens contra JWKS: el codigo esta aislado en este
modulo justamente para poder sustituirlo sin tocar los endpoints.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


def _secret() -> str:
    secret = os.getenv("DAYAHEAD_JWT_SECRET")
    if not secret:
        raise RuntimeError("Falta DAYAHEAD_JWT_SECRET")
    return secret


def valid_api_keys() -> set[str]:
    return {k.strip() for k in os.getenv("DAYAHEAD_API_KEYS", "").split(",") if k.strip()}


def issue_token(api_key: str) -> dict:
    if api_key not in valid_api_keys():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key invalida")
    ttl = int(os.getenv("DAYAHEAD_JWT_TTL_MINUTES", "60"))
    now = datetime.now(timezone.utc)
    payload = {
        "sub": f"client:{api_key[:6]}",
        "scope": "prices:read",
        "iat": now,
        "exp": now + timedelta(minutes=ttl),
        "iss": "grenergy-dayahead-api",
    }
    return {
        "access_token": jwt.encode(payload, _secret(), algorithm=ALGORITHM),
        "token_type": "bearer",
        "expires_in": ttl * 60,
    }


def require_token(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta cabecera Authorization: Bearer <token>")
    try:
        claims = jwt.decode(creds.credentials, _secret(), algorithms=[ALGORITHM],
                            issuer="grenergy-dayahead-api")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expirado")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Token invalido: {exc}")
    if "prices:read" not in claims.get("scope", ""):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Scope insuficiente")
    return claims


class RateLimiter:
    """Ventana deslizante en memoria: suficiente para una instancia local.
    Con varias replicas habria que moverlo a Redis o al API Gateway."""

    def __init__(self, max_requests: int = 120, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str) -> None:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.max_requests:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Limite de peticiones superado")
        hits.append(now)
        self._hits[key] = hits
