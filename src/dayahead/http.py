"""Cliente HTTP con reintentos: las APIs publicas fallan de forma intermitente."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class HttpClient:
    def __init__(self, timeout: int = 60, max_retries: int = 4, backoff: float = 2.0):
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "grenergy-dayahead-etl/1.0"})

    def get(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code in RETRYABLE_STATUS:
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                resp.raise_for_status()
                return resp
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == self.max_retries:
                    break
                sleep = self.backoff * (2 ** (attempt - 1))
                log.warning("GET %s fallo (intento %s/%s): %s. Reintento en %.1fs",
                            url, attempt, self.max_retries, exc, sleep)
                time.sleep(sleep)
        raise RuntimeError(f"GET {url} fallo tras {self.max_retries} intentos") from last_exc
