"""Temperature peak detection for LTAC.

Tracks unique AWOS observations, computes smoothed temperature and trend,
and detects when the daily max has been reached via a multi-state machine.
"""

from __future__ import annotations

import enum
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

UTC = timezone.utc
ISTANBUL = ZoneInfo("Europe/Istanbul")


class TempState(enum.Enum):
    RISING = "RISING"
    FLAT = "FLAT"
    NEAR_PEAK = "NEAR_PEAK"
    PROVISIONAL_PEAK = "PROVISIONAL_PEAK"
    CONFIRMED_PEAK = "CONFIRMED_PEAK"
    STALE = "STALE"


class TempEventType(enum.Enum):
    TEMP_NEW_DAILY_MAX = "temp_new_daily_max"
    TEMP_NEAR_PEAK = "temp_near_peak"
    TEMP_PROVISIONAL_PEAK = "temp_provisional_peak"
    TEMP_CONFIRMED_PEAK = "temp_confirmed_peak"
    TEMP_PEAK_REVOKED = "temp_peak_revoked"
    TEMP_DATA_STALE = "temp_data_stale"
    TEMP_DAY_RESET = "temp_day_reset"
    TEMP_FORECAST_UPDATED = "temp_forecast_updated"


@dataclass
class TempSample:
    source_time_utc: datetime
    source_time_local: datetime
    temp_c_raw: float
    temp_c_smooth: float | None = None


@dataclass
class TempEvent:
    event_type: TempEventType
    timestamp: datetime
    state: TempState
    observed_max_raw: float | None = None
    observed_max_time: datetime | None = None
    current_temp_raw: float | None = None
    trend_10m: float | None = None


class TempTracker:
    """Tracks LTAC temperature observations and detects daily peak."""

    def __init__(self) -> None:
        self.state: TempState = TempState.RISING
        self.samples: list[TempSample] = []
        self.observed_max_raw: float | None = None
        self.observed_max_smooth: float | None = None
        self.observed_max_time: datetime | None = None
        self.observed_max_raw_time: datetime | None = None

        # Last accepted (veriZamani, sicaklik) to deduplicate
        self._last_veri_zamani: str | None = None
        self._last_sicaklik: float | None = None

        # Track samples after the max for counting
        self._samples_after_max: int = 0
        # Track if any rebound to within 0.2 of max after entering provisional
        self._rebound_after_provisional: bool = False
        self._provisional_entered_at: datetime | None = None

        # Forecast data
        self.forecast_daily_max: float | None = None
        self.ankara_shape: list[tuple[datetime, float]] | None = None

        # Current local date for day boundary detection
        self._current_local_date: datetime | None = None

    def _to_local(self, utc_dt: datetime) -> datetime:
        return utc_dt.astimezone(ISTANBUL)

    def _check_day_reset(self, now_utc: datetime) -> TempEvent | None:
        """Reset tracker on Istanbul day boundary."""
        local_now = self._to_local(now_utc)
        local_date = local_now.date()

        if self._current_local_date is None:
            self._current_local_date = local_date
            return None

        if local_date != self._current_local_date:
            self._current_local_date = local_date
            self._reset()
            return TempEvent(
                event_type=TempEventType.TEMP_DAY_RESET,
                timestamp=now_utc,
                state=self.state,
            )
        return None

    def _reset(self) -> None:
        """Clear all state for a new day."""
        self.state = TempState.RISING
        self.samples.clear()
        self.observed_max_raw = None
        self.observed_max_smooth = None
        self.observed_max_time = None
        self.observed_max_raw_time = None
        self._last_veri_zamani = None
        self._last_sicaklik = None
        self._samples_after_max = 0
        self._rebound_after_provisional = False
        self._provisional_entered_at = None

    def is_unique(self, veri_zamani: str, sicaklik: float) -> bool:
        """Check if this observation is unique (not a duplicate poll)."""
        if veri_zamani == self._last_veri_zamani and sicaklik == self._last_sicaklik:
            return False
        return True

    def record(
        self, veri_zamani: str, sicaklik: float, now_utc: datetime | None = None
    ) -> list[TempEvent]:
        """Record a unique LTAC observation. Returns any triggered events."""
        if now_utc is None:
            now_utc = datetime.now(UTC)

        events: list[TempEvent] = []

        # Check day reset
        day_event = self._check_day_reset(now_utc)
        if day_event:
            events.append(day_event)

        # Deduplicate
        if not self.is_unique(veri_zamani, sicaklik):
            return events

        self._last_veri_zamani = veri_zamani
        self._last_sicaklik = sicaklik

        # Parse source time
        try:
            source_utc = datetime.fromisoformat(veri_zamani.replace("Z", "+00:00"))
            if source_utc.tzinfo is None:
                source_utc = source_utc.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            source_utc = now_utc

        source_local = self._to_local(source_utc)

        # Compute smoothed temp
        smooth = self._compute_smooth(sicaklik)

        sample = TempSample(
            source_time_utc=source_utc,
            source_time_local=source_local,
            temp_c_raw=sicaklik,
            temp_c_smooth=smooth,
        )
        self.samples.append(sample)

        # Check for new daily max (raw)
        is_new_max = False
        if self.observed_max_raw is None or sicaklik > self.observed_max_raw:
            # Check for peak revocation
            if (
                self.state in (TempState.PROVISIONAL_PEAK, TempState.CONFIRMED_PEAK)
                and self.observed_max_raw is not None
                and sicaklik >= self.observed_max_raw + 0.1
            ):
                events.append(TempEvent(
                    event_type=TempEventType.TEMP_PEAK_REVOKED,
                    timestamp=now_utc,
                    state=TempState.RISING,
                    observed_max_raw=sicaklik,
                    observed_max_time=source_utc,
                    current_temp_raw=sicaklik,
                ))
                self.state = TempState.RISING
                self._samples_after_max = 0
                self._rebound_after_provisional = False
                self._provisional_entered_at = None

            self.observed_max_raw = sicaklik
            self.observed_max_raw_time = source_utc
            if smooth is not None:
                self.observed_max_smooth = smooth
                self.observed_max_time = source_utc
            self._samples_after_max = 0
            is_new_max = True

            events.append(TempEvent(
                event_type=TempEventType.TEMP_NEW_DAILY_MAX,
                timestamp=now_utc,
                state=self.state,
                observed_max_raw=sicaklik,
                observed_max_time=source_utc,
                current_temp_raw=sicaklik,
            ))
        else:
            self._samples_after_max += 1

        # Track rebound after provisional
        if (
            self.state == TempState.PROVISIONAL_PEAK
            and self.observed_max_smooth is not None
            and smooth is not None
            and smooth >= self.observed_max_smooth - 0.2
        ):
            self._rebound_after_provisional = True

        # Evaluate state transitions
        state_event = self._evaluate_state(sample, now_utc)
        if state_event:
            events.append(state_event)

        return events

    def check_stale(self, now_utc: datetime | None = None) -> TempEvent | None:
        """Check if data has gone stale (no unique sample for 15 min)."""
        if now_utc is None:
            now_utc = datetime.now(UTC)
        if self.state == TempState.STALE:
            return None
        if not self.samples:
            return None
        last_time = self.samples[-1].source_time_utc
        if (now_utc - last_time).total_seconds() >= 900:  # 15 min
            self.state = TempState.STALE
            return TempEvent(
                event_type=TempEventType.TEMP_DATA_STALE,
                timestamp=now_utc,
                state=self.state,
            )
        return None

    def update_forecast(
        self, daily_max: float | None, now_utc: datetime | None = None
    ) -> TempEvent:
        if now_utc is None:
            now_utc = datetime.now(UTC)
        self.forecast_daily_max = daily_max
        return TempEvent(
            event_type=TempEventType.TEMP_FORECAST_UPDATED,
            timestamp=now_utc,
            state=self.state,
        )

    def update_ankara_shape(self, shape: list[tuple[datetime, float]]) -> None:
        self.ankara_shape = shape

    def _compute_smooth(self, current_raw: float) -> float | None:
        """Median of last 3 unique samples (including current)."""
        recent = [s.temp_c_raw for s in self.samples[-2:]] + [current_raw]
        if len(recent) < 1:
            return None
        return statistics.median(recent)

    @property
    def trend_10m(self) -> float | None:
        """Median of last 10 min - median of previous 10 min."""
        if len(self.samples) < 2:
            return None

        now = self.samples[-1].source_time_utc
        t_10 = now - timedelta(minutes=10)
        t_20 = now - timedelta(minutes=20)

        recent = [s.temp_c_raw for s in self.samples if s.source_time_utc >= t_10]
        previous = [
            s.temp_c_raw
            for s in self.samples
            if t_20 <= s.source_time_utc < t_10
        ]

        if not recent or not previous:
            return None

        return statistics.median(recent) - statistics.median(previous)

    @property
    def forecast_gap(self) -> float | None:
        """forecast_daily_max - observed_max_raw."""
        if self.forecast_daily_max is None or self.observed_max_raw is None:
            return None
        return self.forecast_daily_max - self.observed_max_raw

    @property
    def minutes_since_max(self) -> float | None:
        if self.observed_max_time is None or not self.samples:
            return None
        last = self.samples[-1].source_time_utc
        return (last - self.observed_max_time).total_seconds() / 60

    @property
    def drop_from_max(self) -> float | None:
        """How far current smoothed temp is below smoothed max."""
        if not self.samples or self.observed_max_smooth is None:
            return None
        current = self.samples[-1].temp_c_smooth
        if current is None:
            return None
        return self.observed_max_smooth - current

    def _ankara_shape_past_peak(self, as_of: datetime) -> bool:
        """Check if Ankara 3-hourly peak bucket is now or in the past."""
        if not self.ankara_shape:
            return False
        peak_time = max(self.ankara_shape, key=lambda x: x[1])[0]
        return as_of >= peak_time

    def _ankara_shape_rising(self, as_of: datetime) -> bool:
        """Check if Ankara 3-hourly shape is still rising."""
        if not self.ankara_shape:
            return False
        future_buckets = [
            (t, temp) for t, temp in self.ankara_shape if t > as_of
        ]
        past_buckets = [
            (t, temp) for t, temp in self.ankara_shape if t <= as_of
        ]
        if not future_buckets or not past_buckets:
            return False
        last_past_temp = past_buckets[-1][1]
        next_future_temp = future_buckets[0][1]
        return next_future_temp > last_past_temp

    @staticmethod
    def _is_daytime(now_utc: datetime) -> bool:
        """Check if it's daytime in Istanbul (08:00-20:00 local).

        Peak detection is only meaningful during daytime. Overnight
        temperature drops are just nighttime cooling, not a peak.
        """
        local = now_utc.astimezone(ISTANBUL)
        return 8 <= local.hour < 20

    def _evaluate_state(
        self, sample: TempSample, now_utc: datetime
    ) -> TempEvent | None:
        """Evaluate state transitions based on current sample."""
        if self.state == TempState.STALE:
            # Received new data — exit stale
            self.state = TempState.RISING

        # No peak detection outside daytime hours (08:00-20:00 Istanbul)
        if not self._is_daytime(now_utc):
            return None

        smooth = sample.temp_c_smooth
        trend = self.trend_10m
        drop = self.drop_from_max
        mins = self.minutes_since_max
        gap = self.forecast_gap

        # Early peak guardrail
        guardrail_active = (
            gap is not None
            and gap > 1.0
            and self._ankara_shape_rising(now_utc)
        )
        # Override uses raw drop, not smoothed
        raw_drop = (
            (self.observed_max_raw - sample.temp_c_raw)
            if self.observed_max_raw is not None else 0.0
        )
        guardrail_override = (
            raw_drop >= 0.8
            and mins is not None
            and mins >= 30
        )

        # CONFIRMED_PEAK
        if self.state == TempState.PROVISIONAL_PEAK:
            if (
                drop is not None and drop >= 0.5
                and mins is not None and mins >= 30
                and self._samples_after_max >= 4
                and not self._rebound_after_provisional
            ):
                if not guardrail_active or guardrail_override:
                    self.state = TempState.CONFIRMED_PEAK
                    return TempEvent(
                        event_type=TempEventType.TEMP_CONFIRMED_PEAK,
                        timestamp=now_utc,
                        state=self.state,
                        observed_max_raw=self.observed_max_raw,
                        observed_max_time=self.observed_max_raw_time,
                        current_temp_raw=sample.temp_c_raw,
                        trend_10m=trend,
                    )

        # PROVISIONAL_PEAK
        if self.state in (TempState.NEAR_PEAK, TempState.FLAT, TempState.RISING):
            if (
                drop is not None and drop >= 0.3
                and mins is not None and mins >= 15
                and self._samples_after_max >= 2
                and trend is not None and trend <= -0.2
            ):
                if not guardrail_active or guardrail_override:
                    self.state = TempState.PROVISIONAL_PEAK
                    self._provisional_entered_at = now_utc
                    self._rebound_after_provisional = False
                    return TempEvent(
                        event_type=TempEventType.TEMP_PROVISIONAL_PEAK,
                        timestamp=now_utc,
                        state=self.state,
                        observed_max_raw=self.observed_max_raw,
                        observed_max_time=self.observed_max_raw_time,
                        current_temp_raw=sample.temp_c_raw,
                        trend_10m=trend,
                    )

        # NEAR_PEAK
        if self.state in (TempState.FLAT, TempState.RISING):
            near_max = (
                smooth is not None
                and self.observed_max_smooth is not None
                and smooth >= self.observed_max_smooth - 0.3
            )
            trend_flat = trend is not None and trend <= 0.1
            forecast_close = gap is not None and gap <= 0.7
            ankara_past = self._ankara_shape_past_peak(now_utc)

            if near_max and trend_flat and (forecast_close or ankara_past):
                self.state = TempState.NEAR_PEAK
                return TempEvent(
                    event_type=TempEventType.TEMP_NEAR_PEAK,
                    timestamp=now_utc,
                    state=self.state,
                    observed_max_raw=self.observed_max_raw,
                    observed_max_time=self.observed_max_raw_time,
                    current_temp_raw=sample.temp_c_raw,
                    trend_10m=trend,
                )

        # FLAT
        if self.state == TempState.RISING:
            if trend is not None and -0.1 <= trend <= 0.1:
                self.state = TempState.FLAT
                return None  # No event for FLAT, just internal state

        return None
