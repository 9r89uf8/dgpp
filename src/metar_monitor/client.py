"""HTTP transport for the MGM APIs."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import httpx

from .config import MGM_URL, MGM_HEADERS, CONNECT_TIMEOUT, READ_TIMEOUT
from .models import Observation

UTC = timezone.utc

# Forecast endpoints
_DAILY_FORECAST_URL = "https://servis.mgm.gov.tr/web/tahminler/gunluk?istno=90615"
_HOURLY_SHAPE_URL = "https://servis.mgm.gov.tr/web/tahminler/saatlik?istno=17130"


class MGMClient:
    """Async HTTP client with keep-alive for polling MGM."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT,
                read=READ_TIMEOUT,
                write=5.0,
                pool=5.0,
            ),
            headers=MGM_HEADERS,
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=4,
            ),
        )
        # Longer timeout client for forecast fetches (not latency-critical)
        self._forecast_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers=MGM_HEADERS,
        )

    async def fetch(self) -> tuple[Observation, float]:
        """Fetch the latest LTAC observation.

        Returns (observation, latency_ms).
        Raises on HTTP errors or parse failures — caller handles.
        """
        t0 = time.monotonic()
        response = await self._client.get(MGM_URL)
        latency_ms = (time.monotonic() - t0) * 1000
        response.raise_for_status()
        data = json.loads(response.content)
        if not data or not isinstance(data, list):
            raise ValueError(f"Unexpected response shape: {type(data)}")
        return Observation.from_dict(data[0]), latency_ms

    async def fetch_ltac_daily_forecast(self) -> float | None:
        """Fetch airport daily forecast max temperature.

        Returns the forecast max temp for today (Gun1), or None on failure.
        """
        try:
            response = await self._forecast_client.get(_DAILY_FORECAST_URL)
            response.raise_for_status()
            data = json.loads(response.content)
            if not data or not isinstance(data, list):
                return None
            d = data[0]
            # enYuksekGun1 = today's forecast max
            max_temp = d.get("enYuksekGun1")
            if max_temp is not None and max_temp != -9999:
                return float(max_temp)
            return None
        except Exception:
            return None

    async def fetch_ankara_temp_shape(self) -> list[tuple[datetime, float]]:
        """Fetch Ankara 3-hourly temperature shape.

        Returns list of (datetime_utc, temp_c) tuples. Empty on failure.
        """
        try:
            response = await self._forecast_client.get(_HOURLY_SHAPE_URL)
            response.raise_for_status()
            data = json.loads(response.content)
            if not data or not isinstance(data, list):
                return []
            d = data[0]
            tahmin = d.get("tahmin", [])
            result = []
            for entry in tahmin:
                tarih = entry.get("tarih")
                temp = entry.get("sicaklik")
                if tarih and temp is not None and temp != -9999:
                    try:
                        dt = datetime.fromisoformat(
                            tarih.replace("Z", "+00:00")
                        )
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=UTC)
                        result.append((dt, float(temp)))
                    except (ValueError, TypeError):
                        continue
            return result
        except Exception:
            return []

    async def close(self) -> None:
        await self._client.aclose()
        await self._forecast_client.aclose()

    async def __aenter__(self) -> MGMClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
