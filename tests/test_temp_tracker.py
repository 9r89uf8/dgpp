"""Tests for temperature tracking and lightweight LTAC nowcasting."""

from datetime import datetime, timezone

from metar_monitor.temp_tracker import (
    DownState,
    ForecastState,
    TempEventType,
    TempState,
    TempTracker,
)

UTC = timezone.utc


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 4, 6, hour, minute, tzinfo=UTC)


def _veri(hour: int, minute: int = 0) -> str:
    return f"2026-04-06T{hour:02d}:{minute:02d}:00.000Z"


class TestCanonicalObservations:
    def test_duplicate_poll_rejected(self):
        tracker = TempTracker()
        events1 = tracker.record(_veri(10, 0), 15.0, _utc(10, 2))
        events2 = tracker.record(_veri(10, 0), 15.0, _utc(10, 4))
        assert len(events1) > 0
        assert len(events2) == 0
        assert len(tracker.samples) == 1

    def test_same_veri_different_temp_rejected(self):
        tracker = TempTracker()
        tracker.record(_veri(10, 0), 15.0, _utc(10, 2))
        events = tracker.record(_veri(10, 0), 15.1, _utc(10, 4))
        assert len(events) == 0
        assert len(tracker.samples) == 1
        assert tracker.observed_max_raw == 15.0

    def test_different_veri_same_temp_accepted(self):
        tracker = TempTracker()
        tracker.record(_veri(10, 0), 15.0, _utc(10, 2))
        tracker.record(_veri(10, 10), 15.0, _utc(10, 12))
        assert len(tracker.samples) == 2


class TestDailyMaxAndReset:
    def test_new_max_emits_event(self):
        tracker = TempTracker()
        events = tracker.record(_veri(10, 0), 15.0, _utc(10, 2))
        assert any(e.event_type == TempEventType.TEMP_NEW_DAILY_MAX for e in events)
        assert tracker.observed_max_raw == 15.0

    def test_source_time_controls_midnight_reset(self):
        tracker = TempTracker()
        tracker.record(_veri(20, 59), 12.0, _utc(20, 59))

        # Poll happens after midnight local, but source observation is still prior day.
        events = tracker.record(_veri(20, 59), 12.0, _utc(21, 1))
        assert not any(e.event_type == TempEventType.TEMP_DAY_RESET for e in events)

        # New-day source time should reset.
        events = tracker.record("2026-04-06T21:01:00.000Z", 10.0, _utc(21, 2))
        assert any(e.event_type == TempEventType.TEMP_DAY_RESET for e in events)
        assert tracker.observed_max_raw == 10.0
        assert tracker.state == TempState.RISING


class TestPeakLifecycle:
    def _build_declining_tracker(self) -> TempTracker:
        tracker = TempTracker()
        tracker.update_forecast(16.0)
        tracker.update_ankara_shape([
            (_utc(12, 0), 14.0),
            (_utc(15, 0), 16.0),
            (_utc(18, 0), 15.0),
        ])

        for minute, temp in [
            (0, 14.0),
            (10, 14.5),
            (20, 15.0),
            (30, 15.5),
            (40, 16.0),
            (50, 15.7),
            (60, 15.4),
            (70, 15.1),
            (80, 14.8),
        ]:
            hour = 12 + minute // 60
            tracker.record(_veri(hour, minute % 60), temp, _utc(hour, (minute % 60) + 1))
        return tracker

    def test_decline_reaches_peak_state(self):
        tracker = self._build_declining_tracker()
        assert tracker.state in (TempState.PROVISIONAL_PEAK, TempState.CONFIRMED_PEAK)
        assert tracker.nowcast.p_reached_max is not None
        assert tracker.nowcast.p_reached_max >= 0.55

    def test_new_high_revokes_peak(self):
        tracker = self._build_declining_tracker()
        old_state = tracker.state
        events = tracker.record(_veri(13, 30), 16.2, _utc(13, 31))
        revoked = [e for e in events if e.event_type == TempEventType.TEMP_PEAK_REVOKED]

        if old_state in (TempState.PROVISIONAL_PEAK, TempState.CONFIRMED_PEAK):
            assert revoked
            assert tracker.state == TempState.RISING
        assert tracker.observed_max_raw == 16.2


class TestNowcastOutputs:
    def test_nowcast_uses_shape_as_delta_only(self):
        tracker = TempTracker()
        tracker.update_forecast(18.0)
        tracker.update_ankara_shape([
            (_utc(12, 0), 10.0),
            (_utc(15, 0), 14.0),
            (_utc(18, 0), 12.0),
        ])
        for minute, temp in [(0, 14.0), (10, 14.5), (20, 15.0), (30, 15.2)]:
            tracker.record(_veri(12, minute), temp, _utc(12, minute + 1))

        assert tracker.shape_delta_remaining is not None
        assert tracker.shape_delta_remaining >= 0.0
        assert tracker.nowcast.remaining_gain_c is not None
        assert tracker.nowcast.final_max_estimate_c is not None

    def test_forecast_state_is_exposed(self):
        tracker = TempTracker()
        tracker.update_forecast(14.0)
        tracker.update_ankara_shape([
            (_utc(12, 0), 12.0),
            (_utc(15, 0), 12.5),
        ])
        for minute, temp in [(0, 14.0), (10, 14.4), (20, 14.8), (30, 15.0)]:
            tracker.record(_veri(12, minute), temp, _utc(12, minute + 1))

        assert tracker.nowcast.forecast_state in (
            ForecastState.ABOVE,
            ForecastState.NEAR_FORECAST,
            ForecastState.BELOW,
        )
        assert tracker.nowcast.down_state in (
            DownState.NOT_DOWN,
            DownState.FLAT,
            DownState.PROBABLY_DOWN,
            DownState.GOING_DOWN,
        )
        assert tracker.nowcast.state_reasons
        assert tracker.nowcast.forecast_reasons
        assert any("Trend" in reason or "Remaining warming" in reason for reason in tracker.nowcast.state_reasons)
        assert any("forecast" in reason.lower() for reason in tracker.nowcast.forecast_reasons)


class TestStaleData:
    def test_stale_after_15_min(self):
        tracker = TempTracker()
        tracker.record(_veri(10, 0), 15.0, _utc(10, 2))
        event = tracker.check_stale(_utc(10, 17))
        assert event is not None
        assert event.event_type == TempEventType.TEMP_DATA_STALE
        assert tracker.state == TempState.STALE

    def test_new_sample_exits_stale(self):
        tracker = TempTracker()
        tracker.record(_veri(10, 0), 15.0, _utc(10, 2))
        tracker.check_stale(_utc(10, 17))
        tracker.record(_veri(10, 20), 15.5, _utc(10, 22))
        assert tracker.state != TempState.STALE
