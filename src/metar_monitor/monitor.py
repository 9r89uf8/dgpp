"""Core async monitoring loop: poll → detect → alert → UI."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable, Awaitable

from .alert import fire_alert, fire_temp_alert
from .client import MGMClient
from .config import (
    AIRPORT_ICAO,
    FORECAST_REFRESH_S,
    METAR_UNAVAILABLE,
    MGM_DAILY_FORECAST_ISTNO,
    MGM_OBS_ISTNO,
    MGM_SHAPE_FORECAST_ISTNO,
)
from .db import Database
from .detector import MetarDetector, normalize_metar, parse_ddhhmmz
from .event_hub import EventHub
from .models import EventType, MetarEvent, Observation, PollStats
from .schedule import Scheduler
from .state import MonitorState
from .temp_tracker import TempTracker, TempEvent, TempEventType

UTC = timezone.utc
log = logging.getLogger(__name__)

# Type for callbacks
EventCallback = Callable[[MetarEvent, PollStats], None]
TempEventCallback = Callable[[TempEvent], None]


class Monitor:
    """Async polling engine that dispatches MetarEvents."""

    def __init__(
        self,
        client: MGMClient,
        detector: MetarDetector,
        state: MonitorState,
        scheduler: Scheduler,
        on_event: EventCallback | None = None,
        on_temp_event: TempEventCallback | None = None,
        hub: EventHub | None = None,
        db: Database | None = None,
        muted: bool = False,
    ) -> None:
        self.client = client
        self.detector = detector
        self.state = state
        self.scheduler = scheduler
        self.on_event = on_event
        self.on_temp_event = on_temp_event
        self.hub = hub
        self.db = db
        self.muted = muted
        self.stats = PollStats()
        self.temp_tracker = TempTracker()
        self._running = False
        self._inflight: asyncio.Task | None = None
        self._last_forecast_fetch: float = 0  # monotonic

    async def warmup(self, mode: str = "adopt-current") -> MetarEvent | None:
        """Initial fetch to establish connection and seed detector.

        Modes:
          adopt-current   — seed detector, no alert
          alert-if-fresh  — compare with persisted state, alert if different
          silent-warmup   — seed detector, suppress alerts for 10s
        """
        try:
            obs, latency = await self.client.fetch()
            self.stats.record_success(latency)
        except Exception as e:
            detail = str(e).strip()
            if detail:
                log.warning("Warmup fetch failed: %s: %s", type(e).__name__, detail)
            else:
                log.warning("Warmup fetch failed: %s", type(e).__name__)
            return None

        raw = obs.rasat_metar
        if raw == METAR_UNAVAILABLE:
            return None

        normalized = normalize_metar(raw)
        ddhhmmz = parse_ddhhmmz(normalized) or ""
        now = datetime.now(UTC)

        if mode == "alert-if-fresh":
            # Compare against persisted state
            if self.state.last_seen_metar and normalized != self.state.last_seen_metar:
                # Seed detector with the NEW metar so it becomes current
                event = self.detector.check(obs)
                if event.event_type in (EventType.NEW_METAR, EventType.CORRECTION):
                    fire_alert(event.event_type, muted=self.muted)
                    self.stats.record_new_metar()
                    self.scheduler.notify_detection()
                    self._persist_event(event)
                return event
            else:
                # Same as persisted — just adopt
                self.detector.current_metar = normalized
                self.detector.current_ddhhmmz = ddhhmmz
                self.detector.current_veri_zamani = obs.veri_zamani
                return None
        else:
            # adopt-current or silent-warmup: just seed, no alert
            self.detector.current_metar = normalized
            self.detector.current_ddhhmmz = ddhhmmz
            self.detector.current_veri_zamani = obs.veri_zamani
            if not self.state.last_seen_metar:
                # First ever run — persist adopted state
                self.state.last_seen_metar = normalized
                self.state.last_seen_ddhhmmz = ddhhmmz
                self.state.last_seen_at = now.isoformat()
                if self.db:
                    self.db.record_metar(
                        airport_icao=AIRPORT_ICAO,
                        source_provider="mgm",
                        source_external_id=str(MGM_OBS_ISTNO),
                        metar_raw=normalized,
                        normalized_metar=normalized,
                        ddhhmmz=ddhhmmz,
                        event_type="adopted",
                        detected_at=now,
                    )
                else:
                    self.state.adopt_current(
                        normalized, ddhhmmz, now.isoformat()
                    )
            return None

    async def _fetch_forecasts(self) -> None:
        """Fetch daily forecast and ankara shape, update tracker."""
        import time as _time
        fetched_at = datetime.now(UTC)
        daily_max: float | None = None
        shape: list[tuple[datetime, float]] = []

        try:
            daily_max = await self.client.fetch_ltac_daily_forecast()
            event = self.temp_tracker.update_forecast(daily_max, now_utc=fetched_at)
            if self.on_temp_event:
                self.on_temp_event(event)
            if self.hub:
                self.hub.publish_temp(event)
        except Exception as e:
            log.warning("Daily forecast fetch failed: %s", e)

        try:
            shape = await self.client.fetch_ankara_temp_shape()
            self.temp_tracker.update_ankara_shape(shape)
        except Exception as e:
            log.warning("Ankara shape fetch failed: %s", e)

        if daily_max is not None or shape:
            peak_temp: float | None = None
            peak_time_iso: str | None = None
            snapshot = {
                "fetched_at": fetched_at.isoformat(),
                "ltac_daily_max": daily_max,
                "ankara_peak_temp": None,
                "ankara_peak_time": None,
                "ankara_shape": [
                    {
                        "tarih": point_time.isoformat(),
                        "sicaklik": temp_c,
                    }
                    for point_time, temp_c in shape
                ],
            }
            if shape:
                peak_time, peak_temp = max(shape, key=lambda x: x[1])
                peak_time_iso = peak_time.isoformat()
                snapshot["ankara_peak_temp"] = peak_temp
                snapshot["ankara_peak_time"] = peak_time_iso
            if self.db:
                self.db.record_forecast_fetch(
                    airport_icao=AIRPORT_ICAO,
                    source_provider="mgm",
                    source_external_id=f"{MGM_DAILY_FORECAST_ISTNO}+{MGM_SHAPE_FORECAST_ISTNO}",
                    forecast_kind="combined",
                    fetched_at=fetched_at,
                    raw_json=snapshot,
                )
            else:
                self.state.record_forecast_update(
                    fetched_at_iso=fetched_at.isoformat(),
                    ltac_daily_max=daily_max,
                    ankara_peak_temp=peak_temp,
                    ankara_peak_time_iso=peak_time_iso,
                    ankara_shape=snapshot["ankara_shape"],
                )

        self._last_forecast_fetch = _time.monotonic()

    async def run(self) -> None:
        """Main polling loop. Runs until stop() is called."""
        import time as _time

        # Fetch forecasts on startup
        await self._fetch_forecasts()

        self._running = True
        while self._running:
            wait = self.scheduler.time_until_next_poll()
            if wait > 0:
                await asyncio.sleep(wait)

            if not self._running:
                break

            await self._poll_once()

            # Refresh forecasts every few minutes; cheap enough and keeps the
            # dashboard line/airport max reasonably fresh.
            if _time.monotonic() - self._last_forecast_fetch > FORECAST_REFRESH_S:
                await self._fetch_forecasts()

            # Check stale temp data
            stale_event = self.temp_tracker.check_stale()
            if stale_event and self.on_temp_event:
                self.on_temp_event(stale_event)

    async def _poll_once(self) -> None:
        """Execute a single poll cycle with deadline enforcement."""
        self.scheduler.mark_poll_started()
        deadline = self.scheduler.request_deadline

        # Cancel any somehow-still-running previous request
        if self._inflight and not self._inflight.done():
            self._inflight.cancel()

        try:
            obs, latency = await asyncio.wait_for(
                self.client.fetch(), timeout=deadline
            )
            self.stats.record_success(latency)
            event = self.detector.check(obs)
        except asyncio.TimeoutError:
            # Don't immediately retry inside the same cycle; it adds pressure and
            # often fails again on the tiny remaining budget. The next scheduled
            # poll is close enough and keeps the one-in-flight model cleaner.
            self.stats.record_failure()
            event = MetarEvent(
                event_type=EventType.FETCH_ERROR,
                error=f"timeout after {deadline:.1f}s",
            )
        except Exception as e:
            self.stats.record_failure()
            event = MetarEvent(
                event_type=EventType.FETCH_ERROR,
                error=f"{type(e).__name__}: {e}",
            )

        # Alert fires BEFORE UI update
        if event.event_type in (EventType.NEW_METAR, EventType.CORRECTION):
            fire_alert(event.event_type, muted=self.muted)
            self.stats.record_new_metar()
            self.scheduler.notify_detection()
            self._persist_event(event)

        # Canonical LTAC surface stream: one write + one tracker update per veriZamani.
        if event.observation:
            obs = event.observation
            if self._should_persist_surface_observation(obs):
                self._persist_surface_observation(obs, event.detected_at)

        if event.observation and event.observation.sicaklik != -9999:
            obs = event.observation
            if self.temp_tracker.is_unique(obs.veri_zamani, obs.sicaklik):
                temp_events = self.temp_tracker.record_observation(
                    obs, detected_at=event.detected_at
                )
                for te in temp_events:
                    if te.event_type in (
                        TempEventType.TEMP_PROVISIONAL_PEAK,
                        TempEventType.TEMP_CONFIRMED_PEAK,
                    ):
                        fire_temp_alert(te.event_type, muted=self.muted)
                    if self.on_temp_event:
                        self.on_temp_event(te)
                    if self.hub:
                        self.hub.publish_temp(te)

        # Callbacks first, then hub
        if self.on_event:
            self.on_event(event, self.stats)
        if self.hub:
            self.hub.publish_event(event, self.stats)

    def _persist_event(self, event: MetarEvent) -> None:
        """Persist alerting event and capture record to disk."""
        capture = self.detector.make_capture_record(event)
        self.state.last_seen_metar = event.metar_raw
        self.state.last_seen_ddhhmmz = event.ddhhmmz
        self.state.last_seen_at = event.detected_at.isoformat()

        if self.db:
            self.db.record_metar(
                airport_icao=AIRPORT_ICAO,
                source_provider="mgm",
                source_external_id=str(MGM_OBS_ISTNO),
                metar_raw=event.metar_raw,
                normalized_metar=normalize_metar(event.metar_raw),
                ddhhmmz=event.ddhhmmz,
                event_type=event.event_type.value,
                detected_at=event.detected_at,
                delay_from_bulletin_s=capture.delay_from_bulletin_s if capture else None,
            )
            if capture:
                self.db.record_capture(
                    airport_icao=AIRPORT_ICAO,
                    ddhhmmz=capture.ddhhmmz,
                    detection_utc=capture.detection_utc,
                    delay_from_bulletin_s=capture.delay_from_bulletin_s,
                    source=capture.source,
                    event_type=capture.event_type,
                )
        else:
            self.state.record_event(
                metar_raw=event.metar_raw,
                ddhhmmz=event.ddhhmmz,
                detected_at_iso=event.detected_at.isoformat(),
                event_type=event.event_type.value,
            )
            if capture:
                self.state.record_capture(capture)

    def _should_persist_surface_observation(self, obs: Observation) -> bool:
        return bool(obs.veri_zamani) and obs.veri_zamani != self.state.last_seen_veri_zamani

    def _persist_surface_observation(
        self,
        obs: Observation,
        detected_at: datetime,
    ) -> None:
        """Persist one canonical LTAC surface observation keyed by veriZamani."""
        if not obs.veri_zamani:
            return
        self.state.last_seen_veri_zamani = obs.veri_zamani
        if self.db:
            self.db.record_surface_observation(
                airport_icao=AIRPORT_ICAO,
                source_provider="mgm",
                source_external_id=str(MGM_OBS_ISTNO),
                veri_zamani=obs.veri_zamani,
                detected_at=detected_at,
                sicaklik=obs.sicaklik,
                hissedilen_sicaklik=obs.hissedilen_sicaklik,
                nem=obs.nem,
                ruzgar_hiz=obs.ruzgar_hiz,
                ruzgar_yon=obs.ruzgar_yon,
                aktuel_basinc=obs.aktuel_basinc,
                denize_indirgenmis_basinc=obs.denize_indirgenmis_basinc,
                gorus=obs.gorus,
                kapalilik=obs.kapalilik,
                hadise_kodu=obs.hadise_kodu,
                raw_json=asdict(obs),
            )
        else:
            self.state.record_aws_update(
                veri_zamani=obs.veri_zamani,
                detected_at_iso=detected_at.isoformat(),
                sicaklik=obs.sicaklik,
                nem=obs.nem,
                ruzgar_hiz=obs.ruzgar_hiz,
                gorus=obs.gorus,
                denize_indirgenmis_basinc=obs.denize_indirgenmis_basinc,
                ruzgar_yon=obs.ruzgar_yon,
                kapalilik=obs.kapalilik,
            )

    def stop(self) -> None:
        self._running = False
