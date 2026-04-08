"""Shared runtime object for the METAR monitor."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .client import MGMClient
from .config import AIRPORT_ICAO, AIRPORT_TIMEZONE, BASE_INTERVAL
from .db import Database
from .detector import MetarDetector
from .event_hub import EventHub
from .models import EventType, MetarEvent, Observation, PollStats
from .monitor import Monitor
from .schedule import Scheduler
from .state import MonitorState
from .temp_tracker import TempEvent

UTC = timezone.utc
ISTANBUL = ZoneInfo("Europe/Istanbul")


def _serialize_val(v: object) -> object:
    """Convert a single value to JSON-safe form."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (int, float)):
        if v == -9999:
            return None
        return v
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return v
    if hasattr(v, "value"):  # enum
        return v.value
    if isinstance(v, (list, tuple)):
        return [_serialize_val(i) for i in v]
    if isinstance(v, dict):
        return {k: _serialize_val(val) for k, val in v.items()}
    return str(v)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _filter_rows(
    rows: list[dict],
    *,
    time_key: str,
    since: datetime | str | None = None,
    limit: int | None = None,
) -> list[dict]:
    filtered = rows
    if since is not None:
        since_iso = _serialize_val(since)
        filtered = [
            row for row in filtered
            if str(row.get(time_key, "")) >= str(since_iso)
        ]
    if limit is not None:
        filtered = filtered[-limit:]
    return filtered


class Runtime:
    """Shared runtime owning all components. Created once in __main__."""

    def __init__(
        self,
        client: MGMClient,
        state: MonitorState,
        db: Database | None = None,
        startup_mode: str = "adopt-current",
        base_interval: float | None = None,
    ) -> None:
        self.client = client
        self.state = state
        self.db = db
        self.hub = EventHub()

        if self.db:
            self._seed_state_from_db()

        self.detector = MetarDetector(
            last_seen_metar=state.last_seen_metar,
            last_seen_ddhhmmz=state.last_seen_ddhhmmz,
            last_seen_veri_zamani=state.last_seen_veri_zamani,
        )
        self.scheduler = Scheduler(base_interval=base_interval or BASE_INTERVAL)
        self.monitor = Monitor(
            client=client,
            detector=self.detector,
            state=state,
            scheduler=self.scheduler,
            on_event=self.handle_event,
            on_temp_event=self.handle_temp_event,
            hub=self.hub,
            db=self.db,
        )
        # Single source of truth for temperature state: the monitor-owned tracker.
        self.temp_tracker = self.monitor.temp_tracker

        # Mutable snapshot fields
        self.current_observation: Observation | None = None
        self.current_metar: str | None = state.last_seen_metar
        self.current_metar_detected_at: datetime | None = _parse_iso(state.last_seen_at)
        self.last_error: str | None = None
        self.last_error_at: datetime | None = None
        self.last_success_at: datetime | None = None

        self._startup_mode = startup_mode

        # Replay temp tracker from persisted AWS history
        self._replay_temp_from_history()

    def _seed_state_from_db(self) -> None:
        """Seed last-seen detector state from SQLite if present."""
        latest_metar = self.db.get_latest_metar(AIRPORT_ICAO) if self.db else None
        if latest_metar:
            self.state.last_seen_metar = latest_metar.get("metar_raw")
            self.state.last_seen_ddhhmmz = latest_metar.get("ddhhmmz")
            self.state.last_seen_at = latest_metar.get("detected_at")

        latest_obs = self.db.get_latest_surface_observation(AIRPORT_ICAO) if self.db else None
        if latest_obs:
            self.state.last_seen_veri_zamani = latest_obs.get("veri_zamani")

    def _replay_temp_from_history(self) -> None:
        """Rebuild TempTracker state from persisted aws_history.

        Filters to current Istanbul day, sorts chronologically,
        replays through record() with sample-derived timestamps.
        """
        if self.db:
            source_entries = self.db.get_surface_observations_for_local_day(
                AIRPORT_ICAO,
                AIRPORT_TIMEZONE,
            )
        else:
            source_entries = self.state.aws_history

        if not source_entries:
            return

        now_istanbul = datetime.now(ISTANBUL).date()

        entries = []
        for entry in source_entries:
            veri = entry.get("veri_zamani", "")
            sicaklik = entry.get("sicaklik", -9999)
            if sicaklik == -9999 or not veri:
                continue
            try:
                dt = datetime.fromisoformat(veri.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                local_date = dt.astimezone(ISTANBUL).date()
                if local_date == now_istanbul:
                    entries.append((dt, veri, sicaklik))
            except (ValueError, TypeError):
                continue

        entries.sort(key=lambda x: x[0])

        for dt, veri, sicaklik in entries:
            self.temp_tracker.record(veri, sicaklik, now_utc=dt)

    def metar_history(
        self,
        since: datetime | str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        if not self.db:
            return _filter_rows(
                self.state.history,
                time_key="detected_at",
                since=since,
                limit=limit,
            )
        rows = self.db.get_metar_history(
            AIRPORT_ICAO,
            since=since,
            limit=limit,
            event_types=("new", "correction"),
        )
        history = [
            {
                "metar": row["metar_raw"],
                "ddhhmmz": row["ddhhmmz"],
                "detected_at": row["detected_at"],
                "event_type": row["event_type"],
            }
            for row in rows
        ]
        if history:
            return history
        return _filter_rows(
            self.state.history,
            time_key="detected_at",
            since=since,
            limit=limit,
        )

    def aws_history(
        self,
        since: datetime | str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        if not self.db:
            return _filter_rows(
                self.state.aws_history,
                time_key="veri_zamani",
                since=since,
                limit=limit,
            )
        rows = self.db.get_surface_history(
            AIRPORT_ICAO,
            since=since,
            limit=limit,
        )
        history = [
            {
                "veri_zamani": row["veri_zamani"],
                "detected_at": row["detected_at"],
                "sicaklik": row["sicaklik"],
                "hissedilen_sicaklik": row["hissedilen_sicaklik"],
                "nem": row["nem"],
                "ruzgar_hiz": row["ruzgar_hiz"],
                "ruzgar_yon": row["ruzgar_yon"],
                "aktuel_basinc": row["aktuel_basinc"],
                "denize_indirgenmis_basinc": row["denize_indirgenmis_basinc"],
                "gorus": row["gorus"],
                "kapalilik": row["kapalilik"],
                "hadise_kodu": row["hadise_kodu"],
            }
            for row in rows
        ]
        if history:
            return history
        return _filter_rows(
            self.state.aws_history,
            time_key="veri_zamani",
            since=since,
            limit=limit,
        )

    def forecast_history(
        self,
        since: datetime | str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        if not self.db:
            return _filter_rows(
                self.state.forecast_history,
                time_key="fetched_at",
                since=since,
                limit=limit,
            )
        history = self.db.get_forecast_snapshots(
            AIRPORT_ICAO,
            since=since,
            limit=limit,
        )
        if history:
            return history
        return _filter_rows(
            self.state.forecast_history,
            time_key="fetched_at",
            since=since,
            limit=limit,
        )

    def latest_forecast_snapshot(self) -> dict | None:
        if not self.db:
            return self.state.forecast_history[-1] if self.state.forecast_history else None
        snapshot = self.db.get_latest_forecast_snapshot(AIRPORT_ICAO)
        if snapshot:
            return snapshot
        return self.state.forecast_history[-1] if self.state.forecast_history else None

    def handle_event(self, event: MetarEvent, stats: PollStats) -> None:
        """Update mutable fields from monitor events."""
        if event.event_type == EventType.FETCH_ERROR:
            self.last_error = event.error
            self.last_error_at = event.detected_at
        elif event.observation:
            self.current_observation = event.observation
            self.last_success_at = event.detected_at
            self.last_error = None

            if event.event_type in (EventType.NEW_METAR, EventType.CORRECTION):
                self.current_metar = event.metar_raw
                self.current_metar_detected_at = event.detected_at
        elif event.event_type == EventType.UNAVAILABLE:
            self.current_observation = None
            self.last_success_at = event.detected_at

    def handle_temp_event(self, event: TempEvent) -> None:
        """Forward temp events. (Hub publishing handled by monitor.)"""
        pass  # Hub publish is done by monitor. This is for Textual callback compat.

    def snapshot(self) -> dict:
        """Return a consistent, JSON-serializable state snapshot."""
        obs = self.current_observation
        tracker = self.temp_tracker
        stats = self.monitor.stats

        return {
            "current_observation": {
                "sicaklik": _serialize_val(obs.sicaklik),
                "hissedilen_sicaklik": _serialize_val(obs.hissedilen_sicaklik),
                "nem": _serialize_val(obs.nem),
                "ruzgar_hiz": _serialize_val(obs.ruzgar_hiz),
                "ruzgar_yon": _serialize_val(obs.ruzgar_yon),
                "denize_indirgenmis_basinc": _serialize_val(obs.denize_indirgenmis_basinc),
                "aktuel_basinc": _serialize_val(obs.aktuel_basinc),
                "gorus": _serialize_val(obs.gorus),
                "kapalilik": _serialize_val(obs.kapalilik),
                "hadise_kodu": obs.hadise_kodu,
                "veri_zamani": obs.veri_zamani,
                "rasat_metar": obs.rasat_metar,
            } if obs else None,
            "current_metar": self.current_metar,
            "current_metar_detected_at": _serialize_val(self.current_metar_detected_at),
            "last_error": self.last_error,
            "last_error_at": _serialize_val(self.last_error_at),
            "last_success_at": _serialize_val(self.last_success_at),
            "is_healthy": self.last_error is None,
            "poll_stats": {
                "total_polls": stats.total_polls,
                "successful_polls": stats.successful_polls,
                "failed_polls": stats.failed_polls,
                "last_poll_latency_ms": stats.last_poll_latency_ms,
                "avg_latency_ms": stats.avg_latency_ms,
                "min_latency_ms": _serialize_val(stats.min_latency_ms),
                "max_latency_ms": stats.max_latency_ms,
                "success_rate": stats.success_rate,
                "metars_detected": stats.metars_detected,
                "last_metar_detected_at": _serialize_val(stats.last_metar_detected_at),
                "uptime_s": stats.uptime_s,
            },
            "temp_tracker": {
                "state": tracker.state.value,
                "observed_max_raw": tracker.observed_max_raw,
                "observed_max_time": _serialize_val(tracker.observed_max_raw_time),
                "forecast_daily_max": tracker.forecast_daily_max,
                "ankara_shape": [
                    {
                        "tarih": _serialize_val(point_dt),
                        "sicaklik": temp_c,
                    }
                    for point_dt, temp_c in (tracker.ankara_shape or [])
                ],
                "forecast_gap": tracker.forecast_gap,
                "trend_10m": tracker.trend_10m,
                "minutes_since_max": tracker.minutes_since_max,
                "drop_from_max": tracker.drop_from_max,
                "current_temp": tracker.samples[-1].temp_c_raw if tracker.samples else None,
            },
            "latest_forecast_snapshot": self.latest_forecast_snapshot(),
            "scheduler": {
                "interval": self.scheduler.get_interval(),
                "interval_label": self.scheduler.interval_label,
            },
        }
