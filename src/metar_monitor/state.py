"""Atomic state persistence to disk."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import (
    DEFAULT_STATE_DIR,
    STATE_FILENAME,
    MAX_HISTORY,
    MAX_AWS_HISTORY,
    MAX_CAPTURE_LOG,
    MAX_FORECAST_HISTORY,
)
from .models import CaptureRecord


class MonitorState:
    """Persisted monitor state: last seen METAR, history, capture log."""

    def __init__(self, state_dir: str | None = None) -> None:
        self._dir = Path(os.path.expanduser(state_dir or DEFAULT_STATE_DIR))
        self._path = self._dir / STATE_FILENAME

        self.last_seen_metar: str | None = None
        self.last_seen_ddhhmmz: str | None = None
        self.last_seen_at: str | None = None  # ISO string
        self.last_seen_veri_zamani: str | None = None
        self.history: list[dict] = []  # last N METAR events
        self.aws_history: list[dict] = []  # last N AWS observation updates
        self.capture_log: list[dict] = []  # per-publish records
        self.forecast_history: list[dict] = []  # forecast revisions over time

    def load(self) -> None:
        """Load state from disk. Silently starts fresh on any error."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.last_seen_metar = data.get("last_seen_metar")
            self.last_seen_ddhhmmz = data.get("last_seen_ddhhmmz")
            self.last_seen_at = data.get("last_seen_at")
            self.last_seen_veri_zamani = data.get("last_seen_veri_zamani")
            self.history = data.get("history", [])[-MAX_HISTORY:]
            self.aws_history = data.get("aws_history", [])[-MAX_AWS_HISTORY:]
            self.capture_log = data.get("capture_log", [])[-MAX_CAPTURE_LOG:]
            self.forecast_history = data.get("forecast_history", [])[-MAX_FORECAST_HISTORY:]
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            # Corrupt file — start fresh
            pass

    def save(self) -> None:
        """Atomically write state to disk (write tmp + os.replace)."""
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {
            "last_seen_metar": self.last_seen_metar,
            "last_seen_ddhhmmz": self.last_seen_ddhhmmz,
            "last_seen_at": self.last_seen_at,
            "last_seen_veri_zamani": self.last_seen_veri_zamani,
            "history": self.history[-MAX_HISTORY:],
            "aws_history": self.aws_history[-MAX_AWS_HISTORY:],
            "capture_log": self.capture_log[-MAX_CAPTURE_LOG:],
            "forecast_history": self.forecast_history[-MAX_FORECAST_HISTORY:],
        }
        # Write to temp file in the same directory, then atomic rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._dir), suffix=".tmp", prefix="state_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, str(self._path))
        except BaseException:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def record_event(
        self,
        metar_raw: str,
        ddhhmmz: str,
        detected_at_iso: str,
        event_type: str,
    ) -> None:
        """Record an alerting event (new or correction) and persist."""
        self.last_seen_metar = metar_raw
        self.last_seen_ddhhmmz = ddhhmmz
        self.last_seen_at = detected_at_iso

        self.history.append({
            "metar": metar_raw,
            "ddhhmmz": ddhhmmz,
            "detected_at": detected_at_iso,
            "event_type": event_type,
        })
        # Trim in memory too
        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]

        self.save()

    def record_capture(self, record: CaptureRecord) -> None:
        """Append a capture record and persist."""
        self.capture_log.append(record.to_dict())
        if len(self.capture_log) > MAX_CAPTURE_LOG:
            self.capture_log = self.capture_log[-MAX_CAPTURE_LOG:]
        self.save()

    def record_aws_update(
        self,
        veri_zamani: str,
        detected_at_iso: str,
        sicaklik: float,
        nem: int,
        ruzgar_hiz: float,
        gorus: int,
        denize_indirgenmis_basinc: float = -9999,
        ruzgar_yon: int = -9999,
        kapalilik: int = -9999,
    ) -> None:
        """Record an AWS observation update (veriZamani changed)."""
        self.last_seen_veri_zamani = veri_zamani
        self.aws_history.append({
            "veri_zamani": veri_zamani,
            "detected_at": detected_at_iso,
            "sicaklik": sicaklik,
            "nem": nem,
            "ruzgar_hiz": ruzgar_hiz,
            "gorus": gorus,
            "denize_indirgenmis_basinc": denize_indirgenmis_basinc,
            "ruzgar_yon": ruzgar_yon,
            "kapalilik": kapalilik,
        })
        if len(self.aws_history) > MAX_AWS_HISTORY:
            self.aws_history = self.aws_history[-MAX_AWS_HISTORY:]
        self.save()

    def record_forecast_update(
        self,
        fetched_at_iso: str,
        ltac_daily_max: float | None,
        ankara_peak_temp: float | None,
        ankara_peak_time_iso: str | None,
        ankara_shape: list[dict] | None = None,
    ) -> None:
        """Record a forecast revision snapshot and persist."""
        self.forecast_history.append({
            "fetched_at": fetched_at_iso,
            "ltac_daily_max": ltac_daily_max,
            "ankara_peak_temp": ankara_peak_temp,
            "ankara_peak_time": ankara_peak_time_iso,
            "ankara_shape": ankara_shape or [],
        })
        if len(self.forecast_history) > MAX_FORECAST_HISTORY:
            self.forecast_history = self.forecast_history[-MAX_FORECAST_HISTORY:]
        self.save()

    def adopt_current(self, metar_raw: str, ddhhmmz: str, detected_at_iso: str) -> None:
        """Adopt a METAR on startup without recording it as a detection event."""
        self.last_seen_metar = metar_raw
        self.last_seen_ddhhmmz = ddhhmmz
        self.last_seen_at = detected_at_iso
        self.save()
