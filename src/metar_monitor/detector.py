"""METAR change detection with DDHHMMZ parsing."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .config import METAR_UNAVAILABLE
from .models import EventType, Observation, MetarEvent, CaptureRecord

UTC = timezone.utc

# Matches the DDHHMMZ group in a METAR string, e.g. "051420Z"
_DDHHMMZ_RE = re.compile(r"\b(\d{2})(\d{2})(\d{2})Z\b")


def normalize_metar(raw: str) -> str:
    """Collapse runs of whitespace to single space and strip."""
    return " ".join(raw.split())


def parse_ddhhmmz(metar: str) -> str | None:
    """Extract the DDHHMMZ group from a METAR string.

    Returns the matched group like '051420Z', or None if not found.
    """
    m = _DDHHMMZ_RE.search(metar)
    if m:
        return m.group(0)
    return None


def compute_delay_from_bulletin(ddhhmmz: str, detected_at: datetime) -> float | None:
    """Compute seconds between bulletin observation time and our detection time.

    Uses the current UTC date for context, handling month rollover
    (e.g., DD=31 detected on the 1st means the previous month's last day).
    Returns None if the DDHHMMZ can't be resolved.
    """
    m = _DDHHMMZ_RE.match(ddhhmmz)
    if not m:
        return None

    dd, hh, mm = int(m.group(1)), int(m.group(2)), int(m.group(3))
    now = detected_at

    # Try to build the observation datetime.
    # Start with current month, fall back to previous month for rollover.
    for month_offset in (0, -1):
        try:
            year = now.year
            month = now.month + month_offset
            if month < 1:
                month = 12
                year -= 1
            obs_time = datetime(year, month, dd, hh, mm, tzinfo=UTC)
            # Observation should be in the past or very near future (clock skew)
            diff = (detected_at - obs_time).total_seconds()
            if -300 <= diff <= 86400:  # allow 5 min future, 24h past
                return diff
        except ValueError:
            continue

    return None


class MetarDetector:
    """Compares incoming METARs against last known state.

    Classifies each observation as NewMetar, Correction, Same, or Unavailable.
    """

    def __init__(
        self,
        last_seen_metar: str | None = None,
        last_seen_ddhhmmz: str | None = None,
        last_seen_veri_zamani: str | None = None,
    ) -> None:
        self.current_metar: str | None = last_seen_metar
        self.current_ddhhmmz: str | None = last_seen_ddhhmmz
        self.current_veri_zamani: str | None = last_seen_veri_zamani

    def check(self, observation: Observation) -> MetarEvent:
        """Classify an observation and update internal state.

        Checks both rasatMetar (METAR changes) and veriZamani (AWS updates).
        METAR changes take priority — if both change in the same poll,
        the event is NEW_METAR or CORRECTION, not AWS_UPDATE.
        """
        raw = observation.rasat_metar
        now = datetime.now(UTC)
        veri_zamani = observation.veri_zamani

        if raw == METAR_UNAVAILABLE:
            # Still track AWS changes even when METAR is unavailable
            aws_changed = veri_zamani and veri_zamani != self.current_veri_zamani
            if aws_changed:
                self.current_veri_zamani = veri_zamani
                return MetarEvent(
                    event_type=EventType.AWS_UPDATE,
                    observation=observation,
                    detected_at=now,
                )
            return MetarEvent(
                event_type=EventType.UNAVAILABLE,
                observation=observation,
                detected_at=now,
            )

        normalized = normalize_metar(raw)
        ddhhmmz = parse_ddhhmmz(normalized)
        aws_changed = veri_zamani and veri_zamani != self.current_veri_zamani

        # Same METAR as last seen
        if normalized == self.current_metar:
            if aws_changed:
                self.current_veri_zamani = veri_zamani
                return MetarEvent(
                    event_type=EventType.AWS_UPDATE,
                    observation=observation,
                    metar_raw=normalized,
                    ddhhmmz=ddhhmmz or "",
                    detected_at=now,
                )
            return MetarEvent(
                event_type=EventType.SAME,
                observation=observation,
                metar_raw=normalized,
                ddhhmmz=ddhhmmz or "",
                detected_at=now,
            )

        # METAR changed — classify as new or correction
        if ddhhmmz and ddhhmmz == self.current_ddhhmmz:
            event_type = EventType.CORRECTION
        else:
            event_type = EventType.NEW_METAR

        # Update all state
        self.current_metar = normalized
        self.current_ddhhmmz = ddhhmmz
        self.current_veri_zamani = veri_zamani

        event = MetarEvent(
            event_type=event_type,
            observation=observation,
            metar_raw=normalized,
            ddhhmmz=ddhhmmz or "",
            detected_at=now,
        )

        return event

    def make_capture_record(self, event: MetarEvent) -> CaptureRecord | None:
        """Build a capture record for alerting events only."""
        if event.event_type not in (EventType.NEW_METAR, EventType.CORRECTION):
            return None
        delay = None
        if event.ddhhmmz:
            delay = compute_delay_from_bulletin(event.ddhhmmz, event.detected_at)
        return CaptureRecord(
            ddhhmmz=event.ddhhmmz,
            detection_utc=event.detected_at.isoformat(),
            delay_from_bulletin_s=delay,
            source="mgm",
            event_type=event.event_type.value,
        )
