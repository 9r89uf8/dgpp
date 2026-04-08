"""Polling scheduler with monotonic clock and deadline enforcement."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from .config import (
    AGGRESSIVE_INTERVAL,
    ALL_PUBLISH_MINUTES,
    APPROACH_WINDOW_S,
    BASE_INTERVAL,
    METAR_HOT_WINDOWS_UTC,
    POST_PUBLISH_HOLD_S,
    IDLE_INTERVAL,
)


class Scheduler:
    """Determines the next poll interval based on time since last detection.

    Hard rule: request_deadline < poll_interval.
    One in-flight request max — caller is responsible for cancelling overruns.
    """

    def __init__(self, base_interval: float = BASE_INTERVAL) -> None:
        self._base = base_interval
        self._last_detection_mono: float | None = None
        self._last_poll_mono: float = 0.0

    def notify_detection(self) -> None:
        """Call when a new METAR or correction is detected."""
        self._last_detection_mono = time.monotonic()

    @staticmethod
    def _in_hot_window() -> bool:
        """Check if current UTC time is inside a METAR aggressive window."""
        now = datetime.now(timezone.utc)
        now_total = now.minute * 60 + now.second
        return any(
            (start_minute * 60) <= now_total < (end_minute * 60)
            for start_minute, end_minute in METAR_HOT_WINDOWS_UTC
        )

    @staticmethod
    def _seconds_until_next_publish() -> int:
        """Return seconds until the next scheduled LTAC publish minute."""
        now = datetime.now(timezone.utc)
        now_total = now.minute * 60 + now.second
        for minute in ALL_PUBLISH_MINUTES:
            target = minute * 60
            if target > now_total:
                return target - now_total
        return (ALL_PUBLISH_MINUTES[0] * 60 + 3600) - now_total

    def get_interval(self) -> float:
        """Return the current poll interval in seconds.

        METAR hot windows always win — return
        AGGRESSIVE_INTERVAL.
        Outside hot windows:
          within POST_PUBLISH_HOLD_S of a real detection → base interval
          next scheduled publish within APPROACH_WINDOW_S → base interval
          otherwise                                       → idle interval
        """
        if self._in_hot_window():
            return AGGRESSIVE_INTERVAL

        if self._last_detection_mono is not None:
            elapsed = time.monotonic() - self._last_detection_mono
            if elapsed < POST_PUBLISH_HOLD_S:
                return self._base

        if self._seconds_until_next_publish() <= APPROACH_WINDOW_S:
            return self._base

        return IDLE_INTERVAL

    @property
    def request_deadline(self) -> float:
        """Maximum time a single request may take.

        Must be strictly less than the current interval so we never
        queue polls behind slow requests.
        """
        interval = self.get_interval()
        # Leave 0.2s headroom for scheduling jitter
        return max(interval - 0.2, 0.5)

    def time_until_next_poll(self) -> float:
        """Seconds until the next poll should fire."""
        if self._last_poll_mono == 0.0:
            return 0.0
        elapsed = time.monotonic() - self._last_poll_mono
        remaining = self.get_interval() - elapsed
        return max(remaining, 0.0)

    def mark_poll_started(self) -> None:
        """Record that a poll is starting now."""
        self._last_poll_mono = time.monotonic()

    @property
    def interval_label(self) -> str:
        """Human-readable label for the current interval mode."""
        if self._in_hot_window():
            return "AGGRESSIVE"
        interval = self.get_interval()
        if interval <= self._base:
            return "ACTIVE"
        else:
            return "IDLE"
