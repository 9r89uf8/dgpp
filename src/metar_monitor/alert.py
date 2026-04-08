"""Audio alerts for METAR detections and temperature peak events."""

from __future__ import annotations

import sys

from .models import EventType
from .temp_tracker import TempEventType


def _beep(frequency: int, duration_ms: int) -> None:
    """Platform-aware beep. winsound on Windows, terminal bell elsewhere."""
    try:
        import winsound
        winsound.Beep(frequency, duration_ms)
    except (ImportError, RuntimeError):
        sys.stdout.write("\a")
        sys.stdout.flush()


def alert_new_metar() -> None:
    """Single high-pitched beep for a new METAR bulletin."""
    _beep(1000, 300)


def alert_correction() -> None:
    """Two shorter beeps for a correction to the current METAR."""
    _beep(800, 150)
    _beep(800, 150)


def alert_provisional_peak() -> None:
    """Soft low tone for provisional peak detection."""
    _beep(600, 200)


def alert_confirmed_peak() -> None:
    """Stronger descending tones for confirmed peak."""
    _beep(800, 200)
    _beep(600, 200)
    _beep(400, 300)


def fire_alert(event_type: EventType, *, muted: bool = False) -> None:
    """Dispatch alert based on event type. Call BEFORE UI update."""
    if muted:
        return
    if event_type == EventType.NEW_METAR:
        alert_new_metar()
    elif event_type == EventType.CORRECTION:
        alert_correction()


def fire_temp_alert(event_type: TempEventType, *, muted: bool = False) -> None:
    """Dispatch temp peak alert. Call BEFORE UI update."""
    if muted:
        return
    if event_type == TempEventType.TEMP_PROVISIONAL_PEAK:
        alert_provisional_peak()
    elif event_type == TempEventType.TEMP_CONFIRMED_PEAK:
        alert_confirmed_peak()
