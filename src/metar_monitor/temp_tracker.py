"""Temperature tracking and lightweight nowcasting for LTAC.

Tracks unique LTAC observations keyed by ``veriZamani``, computes a small
deterministic nowcast, and derives peak states without adding complexity to
the collection path.
"""

from __future__ import annotations

import enum
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from .models import Observation

UTC = timezone.utc
ISTANBUL = ZoneInfo("Europe/Istanbul")
MIN_NOISE_C = 0.15
MAX_REASONABLE_REMAINING_GAIN_C = 4.0


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


class DownState(enum.Enum):
    NOT_DOWN = "NOT_DOWN"
    FLAT = "FLAT"
    PROBABLY_DOWN = "PROBABLY_DOWN"
    GOING_DOWN = "GOING_DOWN"


class ForecastState(enum.Enum):
    ABOVE = "ABOVE"
    BELOW = "BELOW"
    NEAR_FORECAST = "NEAR_FORECAST"
    UNKNOWN = "UNKNOWN"


@dataclass
class TempSample:
    source_time_utc: datetime
    source_time_local: datetime
    temp_c_raw: float
    detected_at_utc: datetime | None = None
    wind_speed_kmh: float | None = None
    wind_dir_deg: int | None = None
    sea_level_pressure_hpa: float | None = None
    cloud_cover_oktas: int | None = None
    weather_code: str | None = None
    temp_c_smooth: float | None = None


@dataclass
class TempNowcast:
    remaining_gain_c: float | None = None
    final_max_estimate_c: float | None = None
    p_reached_max: float | None = None
    p_going_down: float | None = None
    p_above_forecast: float | None = None
    p_below_forecast: float | None = None
    slope_30m: float | None = None
    slope_60m: float | None = None
    noise_30m: float | None = None
    shape_delta_remaining_c: float | None = None
    down_state: DownState = DownState.FLAT
    forecast_state: ForecastState = ForecastState.UNKNOWN


@dataclass
class TempEvent:
    event_type: TempEventType
    timestamp: datetime
    state: TempState
    observed_max_raw: float | None = None
    observed_max_time: datetime | None = None
    current_temp_raw: float | None = None
    trend_10m: float | None = None
    trend_30m: float | None = None
    trend_60m: float | None = None
    remaining_gain_c: float | None = None
    final_max_estimate_c: float | None = None
    p_reached_max: float | None = None
    p_going_down: float | None = None
    p_above_forecast: float | None = None
    p_below_forecast: float | None = None
    down_state: str | None = None
    forecast_state: str | None = None


class TempTracker:
    """Tracks LTAC observations and derives a lightweight deterministic nowcast."""

    def __init__(self) -> None:
        self.state: TempState = TempState.RISING
        self.samples: list[TempSample] = []
        self.observed_max_raw: float | None = None
        self.observed_max_smooth: float | None = None
        self.observed_max_time: datetime | None = None
        self.observed_max_raw_time: datetime | None = None

        # Last accepted veriZamani for canonical LTAC observation dedupe
        self._last_veri_zamani: str | None = None
        self._last_sicaklik: float | None = None

        # Track samples after the max for counting
        self._samples_after_max: int = 0
        # Track if any rebound to within 0.2 of max after entering provisional
        self._rebound_after_provisional: bool = False
        self._provisional_entered_at: datetime | None = None
        self._down_negative_streak = 0
        self._down_positive_streak = 0

        # Forecast data
        self.forecast_daily_max: float | None = None
        self.ankara_shape: list[tuple[datetime, float]] | None = None
        self.nowcast = TempNowcast()

        # Current local date for day boundary detection
        self._current_local_date = None

    def _to_local(self, utc_dt: datetime) -> datetime:
        return utc_dt.astimezone(ISTANBUL)

    def _check_day_reset(self, source_utc: datetime) -> TempEvent | None:
        """Reset tracker on Istanbul day boundary."""
        local_now = self._to_local(source_utc)
        local_date = local_now.date()

        if self._current_local_date is None:
            self._current_local_date = local_date
            return None

        if local_date != self._current_local_date:
            self._current_local_date = local_date
            self._reset()
            return TempEvent(
                event_type=TempEventType.TEMP_DAY_RESET,
                timestamp=source_utc,
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
        self._down_negative_streak = 0
        self._down_positive_streak = 0
        self.nowcast = TempNowcast()

    def is_unique(self, veri_zamani: str, sicaklik: float | None = None) -> bool:
        """Treat ``veriZamani`` as the canonical LTAC observation key."""
        return bool(veri_zamani) and veri_zamani != self._last_veri_zamani

    def record(
        self, veri_zamani: str, sicaklik: float, now_utc: datetime | None = None
    ) -> list[TempEvent]:
        """Compatibility wrapper for simple temperature-only tests/replay."""
        if now_utc is None:
            now_utc = datetime.now(UTC)
        observation = Observation(
            ist_no=0,
            veri_zamani=veri_zamani,
            sicaklik=sicaklik,
            hissedilen_sicaklik=-9999,
            nem=-9999,
            ruzgar_hiz=-9999,
            ruzgar_yon=-9999,
            aktuel_basinc=-9999,
            denize_indirgenmis_basinc=-9999,
            gorus=-9999,
            kapalilik=-9999,
            hadise_kodu="",
            rasat_metar="-9999",
            yagis_24_saat=-9999,
        )
        return self.record_observation(observation, detected_at=now_utc)

    def record_observation(
        self,
        observation: Observation,
        detected_at: datetime | None = None,
    ) -> list[TempEvent]:
        """Record one canonical LTAC observation keyed by ``veriZamani``."""
        if detected_at is None:
            detected_at = datetime.now(UTC)

        events: list[TempEvent] = []
        source_utc = self._parse_source_time(observation.veri_zamani, detected_at)
        source_local = self._to_local(source_utc)

        # Reset by source time, not poll time.
        day_event = self._check_day_reset(source_utc)
        if day_event:
            events.append(day_event)

        if not self.is_unique(observation.veri_zamani, observation.sicaklik):
            return events

        self._last_veri_zamani = observation.veri_zamani
        self._last_sicaklik = observation.sicaklik

        smooth = self._compute_smooth(observation.sicaklik)

        sample = TempSample(
            source_time_utc=source_utc,
            source_time_local=source_local,
            detected_at_utc=detected_at,
            temp_c_raw=observation.sicaklik,
            wind_speed_kmh=None if observation.ruzgar_hiz == -9999 else observation.ruzgar_hiz,
            wind_dir_deg=None if observation.ruzgar_yon == -9999 else observation.ruzgar_yon,
            sea_level_pressure_hpa=(
                None
                if observation.denize_indirgenmis_basinc == -9999
                else observation.denize_indirgenmis_basinc
            ),
            cloud_cover_oktas=None if observation.kapalilik == -9999 else observation.kapalilik,
            weather_code=observation.hadise_kodu or None,
            temp_c_smooth=smooth,
        )
        self.samples.append(sample)

        is_new_max = False
        if self.observed_max_raw is None or observation.sicaklik > self.observed_max_raw:
            if (
                self.state in (TempState.PROVISIONAL_PEAK, TempState.CONFIRMED_PEAK)
                and self.observed_max_raw is not None
                and observation.sicaklik >= self.observed_max_raw + 0.1
            ):
                self.state = TempState.RISING
                self._samples_after_max = 0
                self._rebound_after_provisional = False
                self._provisional_entered_at = None
                self._update_nowcast()
                events.append(
                    self._make_temp_event(
                        TempEventType.TEMP_PEAK_REVOKED,
                        sample=sample,
                    )
                )

            self.observed_max_raw = observation.sicaklik
            self.observed_max_raw_time = source_utc
            if smooth is not None:
                self.observed_max_smooth = smooth
                self.observed_max_time = source_utc
            self._samples_after_max = 0
            is_new_max = True

            self._update_nowcast()
            events.append(
                self._make_temp_event(
                    TempEventType.TEMP_NEW_DAILY_MAX,
                    sample=sample,
                )
            )
        else:
            self._samples_after_max += 1

        if (
            self.state == TempState.PROVISIONAL_PEAK
            and self.observed_max_smooth is not None
            and smooth is not None
            and smooth >= self.observed_max_smooth - 0.2
        ):
            self._rebound_after_provisional = True

        if not is_new_max:
            self._update_nowcast()

        state_event = self._evaluate_state(sample)
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
        self._update_nowcast()
        return self._make_temp_event(
            TempEventType.TEMP_FORECAST_UPDATED,
            timestamp=now_utc,
        )

    def update_ankara_shape(self, shape: list[tuple[datetime, float]]) -> None:
        self.ankara_shape = shape
        self._update_nowcast()

    @staticmethod
    def _parse_source_time(veri_zamani: str, fallback: datetime) -> datetime:
        try:
            source_utc = datetime.fromisoformat(veri_zamani.replace("Z", "+00:00"))
            if source_utc.tzinfo is None:
                source_utc = source_utc.replace(tzinfo=UTC)
            return source_utc
        except (ValueError, TypeError, AttributeError):
            return fallback

    def _compute_smooth(self, current_raw: float) -> float | None:
        """Median of last 3 unique samples (including current)."""
        recent = [s.temp_c_raw for s in self.samples[-2:]] + [current_raw]
        if len(recent) < 1:
            return None
        return statistics.median(recent)

    def _window_trend(self, minutes: int) -> float | None:
        """Median of the last window minus the previous equal-sized window."""
        if len(self.samples) < 2:
            return None
        now = self.samples[-1].source_time_utc
        recent_start = now - timedelta(minutes=minutes)
        previous_start = now - timedelta(minutes=minutes * 2)

        recent = [s.temp_c_raw for s in self.samples if s.source_time_utc >= recent_start]
        previous = [
            s.temp_c_raw
            for s in self.samples
            if previous_start <= s.source_time_utc < recent_start
        ]
        if not recent or not previous:
            return None
        return statistics.median(recent) - statistics.median(previous)

    @property
    def trend_10m(self) -> float | None:
        return self._window_trend(10)

    @property
    def trend_30m(self) -> float | None:
        return self._window_trend(30)

    @property
    def trend_60m(self) -> float | None:
        return self._window_trend(60)

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

    @property
    def noise_30m(self) -> float | None:
        if not self.samples:
            return None
        now = self.samples[-1].source_time_utc
        recent = [
            s.temp_c_raw
            for s in self.samples
            if s.source_time_utc >= now - timedelta(minutes=30)
        ]
        if len(recent) < 2:
            return MIN_NOISE_C
        return max(MIN_NOISE_C, statistics.pstdev(recent))

    @property
    def shape_delta_remaining(self) -> float | None:
        """Future Ankara-shape delta from the current bucket, not absolute LTAC temp."""
        if not self.ankara_shape or not self.samples:
            return None
        as_of = self.samples[-1].source_time_utc
        future = [(t, temp) for t, temp in self.ankara_shape if t > as_of]
        if not future:
            return 0.0
        past = [(t, temp) for t, temp in self.ankara_shape if t <= as_of]
        anchor_temp = past[-1][1] if past else future[0][1]
        future_peak = max(temp for _, temp in future)
        return max(0.0, future_peak - anchor_temp)

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _compute_remaining_gain(self) -> float | None:
        if self.observed_max_raw is None or not self.samples:
            return None
        shape_gain = self.shape_delta_remaining or 0.0
        slope_10m = self.trend_10m or 0.0
        slope_30m = self.trend_30m or 0.0
        drop = self.drop_from_max or 0.0
        noise = self.noise_30m or MIN_NOISE_C

        positive_push = max(slope_10m, 0.0) * 0.4 + max(slope_30m, 0.0) * 0.6
        rebound_penalty = max(drop - noise, 0.0) * 0.9
        remaining = shape_gain * 0.65 + positive_push - rebound_penalty

        mins = self.minutes_since_max
        if mins is not None and mins >= 30 and slope_30m <= 0:
            remaining = min(remaining, noise)

        return max(0.0, min(remaining, MAX_REASONABLE_REMAINING_GAIN_C))

    def _update_down_state(self) -> DownState:
        slope_10m = self.trend_10m
        slope_30m = self.trend_30m
        noise = self.noise_30m or MIN_NOISE_C

        negative_confirm = (
            slope_10m is not None
            and slope_30m is not None
            and slope_10m < 0
            and slope_30m <= 0
            and (slope_10m <= -noise or slope_30m <= -noise)
        )
        positive_confirm = (
            slope_10m is not None
            and (
                slope_10m >= noise
                or (slope_30m is not None and slope_30m >= noise)
            )
        )

        if negative_confirm:
            self._down_negative_streak += 1
            self._down_positive_streak = 0
        elif positive_confirm:
            self._down_positive_streak += 1
            self._down_negative_streak = 0
        else:
            self._down_negative_streak = 0
            self._down_positive_streak = 0

        if self._down_negative_streak >= 2:
            return DownState.GOING_DOWN
        if negative_confirm:
            return DownState.PROBABLY_DOWN
        if self._down_positive_streak >= 1:
            return DownState.NOT_DOWN
        return DownState.FLAT

    def _update_nowcast(self) -> None:
        nowcast = TempNowcast()
        if not self.samples or self.observed_max_raw is None:
            self.nowcast = nowcast
            return

        noise = self.noise_30m or MIN_NOISE_C
        shape_gain = self.shape_delta_remaining or 0.0
        remaining_gain = self._compute_remaining_gain()
        final_max_est = (
            self.observed_max_raw + remaining_gain
            if remaining_gain is not None and self.observed_max_raw is not None
            else None
        )
        slope_10m = self.trend_10m or 0.0
        slope_30m = self.trend_30m or 0.0
        slope_60m = self.trend_60m
        drop = self.drop_from_max or 0.0
        mins = self.minutes_since_max or 0.0

        p_reached = 0.45
        p_reached += min(mins / 60.0, 1.0) * 0.15
        p_reached += max(drop - noise, 0.0) * 0.30
        p_reached += max(-slope_10m, 0.0) / max(noise, MIN_NOISE_C) * 0.10
        p_reached += max(-slope_30m, 0.0) / max(noise, MIN_NOISE_C) * 0.15
        p_reached -= max(shape_gain, 0.0) / 1.5 * 0.25
        if mins <= 10:
            p_reached -= 0.10

        p_down = 0.50
        p_down += (-slope_10m / max(noise, MIN_NOISE_C)) * 0.18
        p_down += (-slope_30m / max(noise, MIN_NOISE_C)) * 0.22
        if mins < 10:
            p_down -= 0.10
        if self.samples[-1].temp_c_raw >= self.observed_max_raw:
            p_down -= 0.15

        forecast_state = ForecastState.UNKNOWN
        p_above = None
        p_below = None
        if self.forecast_daily_max is not None and final_max_est is not None:
            band = max(0.4, noise * 1.5)
            gap = final_max_est - self.forecast_daily_max
            p_above = self._clamp01(0.5 + gap / max(band * 2.0, 0.8))
            p_below = self._clamp01(0.5 - gap / max(band * 2.0, 0.8))
            if gap > band:
                forecast_state = ForecastState.ABOVE
            elif gap < -band:
                forecast_state = ForecastState.BELOW
            else:
                forecast_state = ForecastState.NEAR_FORECAST

        nowcast.remaining_gain_c = remaining_gain
        nowcast.final_max_estimate_c = final_max_est
        nowcast.p_reached_max = self._clamp01(p_reached)
        nowcast.p_going_down = self._clamp01(p_down)
        nowcast.p_above_forecast = p_above
        nowcast.p_below_forecast = p_below
        nowcast.slope_30m = self.trend_30m
        nowcast.slope_60m = slope_60m
        nowcast.noise_30m = noise
        nowcast.shape_delta_remaining_c = self.shape_delta_remaining
        nowcast.down_state = self._update_down_state()
        nowcast.forecast_state = forecast_state
        self.nowcast = nowcast

    @staticmethod
    def _is_daytime(source_utc: datetime) -> bool:
        """Check if it's daytime in Istanbul (08:00-20:00 local).

        Peak detection is only meaningful during daytime. Overnight
        temperature drops are just nighttime cooling, not a peak.
        """
        local = source_utc.astimezone(ISTANBUL)
        return 8 <= local.hour < 20

    def _make_temp_event(
        self,
        event_type: TempEventType,
        sample: TempSample | None = None,
        timestamp: datetime | None = None,
    ) -> TempEvent:
        sample = sample or (self.samples[-1] if self.samples else None)
        return TempEvent(
            event_type=event_type,
            timestamp=timestamp or (sample.source_time_utc if sample else datetime.now(UTC)),
            state=self.state,
            observed_max_raw=self.observed_max_raw,
            observed_max_time=self.observed_max_raw_time,
            current_temp_raw=(sample.temp_c_raw if sample else None),
            trend_10m=self.trend_10m,
            trend_30m=self.nowcast.slope_30m,
            trend_60m=self.nowcast.slope_60m,
            remaining_gain_c=self.nowcast.remaining_gain_c,
            final_max_estimate_c=self.nowcast.final_max_estimate_c,
            p_reached_max=self.nowcast.p_reached_max,
            p_going_down=self.nowcast.p_going_down,
            p_above_forecast=self.nowcast.p_above_forecast,
            p_below_forecast=self.nowcast.p_below_forecast,
            down_state=self.nowcast.down_state.value,
            forecast_state=self.nowcast.forecast_state.value,
        )

    def _evaluate_state(self, sample: TempSample) -> TempEvent | None:
        if self.state == TempState.STALE:
            self.state = TempState.RISING

        if not self._is_daytime(sample.source_time_utc):
            return None

        drop = self.drop_from_max
        mins = self.minutes_since_max
        noise = self.noise_30m or MIN_NOISE_C
        p_reached = self.nowcast.p_reached_max or 0.0
        down_state = self.nowcast.down_state

        if self.state == TempState.PROVISIONAL_PEAK:
            if (
                drop is not None
                and drop >= max(0.5, noise)
                and mins is not None
                and mins >= 30
                and self._samples_after_max >= 4
                and not self._rebound_after_provisional
                and p_reached >= 0.85
                and down_state == DownState.GOING_DOWN
            ):
                self.state = TempState.CONFIRMED_PEAK
                return self._make_temp_event(
                    TempEventType.TEMP_CONFIRMED_PEAK,
                    sample=sample,
                )

        if self.state in (TempState.NEAR_PEAK, TempState.FLAT, TempState.RISING):
            if (
                mins is not None
                and mins >= 15
                and self._samples_after_max >= 2
                and p_reached >= 0.70
                and (self.trend_10m is None or self.trend_10m <= noise)
            ):
                self.state = TempState.PROVISIONAL_PEAK
                self._provisional_entered_at = sample.source_time_utc
                self._rebound_after_provisional = False
                return self._make_temp_event(
                    TempEventType.TEMP_PROVISIONAL_PEAK,
                    sample=sample,
                )

        if self.state in (TempState.FLAT, TempState.RISING):
            if p_reached >= 0.55:
                self.state = TempState.NEAR_PEAK
                return self._make_temp_event(
                    TempEventType.TEMP_NEAR_PEAK,
                    sample=sample,
                )

        if self.state == TempState.RISING:
            trend_10m = self.trend_10m
            trend_30m = self.trend_30m
            if (
                trend_10m is not None
                and -noise <= trend_10m <= noise
                and (trend_30m is None or -noise <= trend_30m <= noise)
            ):
                self.state = TempState.FLAT
                return None

        return None
