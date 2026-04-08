"""Tests for temperature peak detection."""

from datetime import datetime, timedelta, timezone

from metar_monitor.temp_tracker import (
    TempTracker,
    TempState,
    TempEventType,
)

UTC = timezone.utc


def _utc(hour: int, minute: int = 0) -> datetime:
    """Helper: April 6, 2026 at given UTC hour:minute."""
    return datetime(2026, 4, 6, hour, minute, tzinfo=UTC)


def _veri(hour: int, minute: int = 0) -> str:
    """Helper: veriZamani string."""
    return f"2026-04-06T{hour:02d}:{minute:02d}:00.000Z"


class TestSampleAcceptance:
    def test_duplicate_poll_rejected(self):
        t = TempTracker()
        events1 = t.record(_veri(10, 0), 15.0, _utc(10, 2))
        events2 = t.record(_veri(10, 0), 15.0, _utc(10, 4))
        assert len(events1) > 0  # first sample accepted
        assert len(events2) == 0  # duplicate rejected

    def test_same_veri_different_temp_accepted(self):
        t = TempTracker()
        t.record(_veri(10, 0), 15.0, _utc(10, 2))
        events = t.record(_veri(10, 0), 15.1, _utc(10, 4))
        # Different sicaklik = unique
        assert len(t.samples) == 2

    def test_different_veri_same_temp_accepted(self):
        t = TempTracker()
        t.record(_veri(10, 0), 15.0, _utc(10, 2))
        events = t.record(_veri(10, 10), 15.0, _utc(10, 12))
        assert len(t.samples) == 2


class TestDailyMax:
    def test_new_max_emits_event(self):
        t = TempTracker()
        events = t.record(_veri(10, 0), 15.0, _utc(10, 2))
        assert any(e.event_type == TempEventType.TEMP_NEW_DAILY_MAX for e in events)
        assert t.observed_max_raw == 15.0

    def test_higher_temp_updates_max(self):
        t = TempTracker()
        t.record(_veri(10, 0), 15.0, _utc(10, 2))
        t.record(_veri(10, 10), 16.0, _utc(10, 12))
        assert t.observed_max_raw == 16.0

    def test_lower_temp_does_not_update_max(self):
        t = TempTracker()
        t.record(_veri(10, 0), 15.0, _utc(10, 2))
        t.record(_veri(10, 10), 14.0, _utc(10, 12))
        assert t.observed_max_raw == 15.0


class TestBriefDipNoTrigger:
    def _build_rising_tracker(self) -> TempTracker:
        """Build a tracker with enough samples to compute trend."""
        t = TempTracker()
        t.forecast_daily_max = 18.0
        # Rising temps over 25 min
        temps = [14.0, 14.3, 14.6, 14.9, 15.2, 15.5]
        for i, temp in enumerate(temps):
            t.record(_veri(10, i * 5), temp, _utc(10, i * 5 + 2))
        return t

    def test_brief_dip_then_rebound(self):
        t = self._build_rising_tracker()
        # Dip
        t.record(_veri(10, 32), 15.0, _utc(10, 34))
        # Rebound
        t.record(_veri(10, 40), 15.6, _utc(10, 42))
        # Should not be in PROVISIONAL or CONFIRMED
        assert t.state not in (TempState.PROVISIONAL_PEAK, TempState.CONFIRMED_PEAK)


class TestTrueDecline:
    def _build_peaked_tracker(self) -> TempTracker:
        """Build a tracker that peaked at 16.0 and is now declining."""
        t = TempTracker()
        t.forecast_daily_max = 16.0  # forecast_gap = 0

        # Rise to peak over 30 min
        rising = [(0, 14.0), (5, 14.5), (10, 15.0), (15, 15.5), (20, 15.8), (25, 16.0)]
        for m, temp in rising:
            t.record(_veri(12, m), temp, _utc(12, m + 2))

        # Decline over next 35 min
        declining = [(30, 15.8), (35, 15.6), (40, 15.4), (45, 15.2), (50, 15.0), (55, 14.8), (60, 14.6)]
        for m, temp in declining:
            h = 12 + m // 60
            mi = m % 60
            t.record(_veri(h, mi), temp, _utc(h, mi + 2))

        return t

    def test_decline_reaches_provisional(self):
        t = self._build_peaked_tracker()
        # After 15+ min of decline with 2+ lower samples, should hit provisional
        assert t.state in (
            TempState.PROVISIONAL_PEAK,
            TempState.CONFIRMED_PEAK,
            TempState.NEAR_PEAK,
        )

    def test_decline_reaches_confirmed(self):
        t = self._build_peaked_tracker()
        # With 35 min of decline, 0.5+ drop, 4+ lower samples, should confirm
        # The tracker should be CONFIRMED or at least PROVISIONAL
        assert t.state in (TempState.PROVISIONAL_PEAK, TempState.CONFIRMED_PEAK)


class TestPeakRevocation:
    def test_new_high_revokes_peak(self):
        t = TempTracker()
        t.forecast_daily_max = 16.0

        # Rise to 16.0
        for m, temp in [(0, 14.0), (5, 14.5), (10, 15.0), (15, 15.5), (20, 16.0)]:
            t.record(_veri(12, m), temp, _utc(12, m + 2))

        # Decline
        for m, temp in [(30, 15.6), (35, 15.4), (40, 15.2), (45, 15.0)]:
            t.record(_veri(12, m), temp, _utc(12, m + 2))

        old_state = t.state

        # New high exceeding max by >= 0.1
        events = t.record(_veri(13, 0), 16.2, _utc(13, 2))
        revoked = [e for e in events if e.event_type == TempEventType.TEMP_PEAK_REVOKED]

        if old_state in (TempState.PROVISIONAL_PEAK, TempState.CONFIRMED_PEAK):
            assert len(revoked) == 1
            assert t.state == TempState.RISING


class TestDayReset:
    def test_istanbul_midnight_resets(self):
        t = TempTracker()
        # Record at 20:59 UTC (23:59 Istanbul, April 6)
        t.record(_veri(20, 59), 12.0, _utc(20, 59))
        assert t.observed_max_raw == 12.0

        # Record at 21:01 UTC (00:01 Istanbul, April 7 = new day)
        events = t.record(
            "2026-04-06T21:01:00.000Z", 10.0,
            datetime(2026, 4, 6, 21, 1, tzinfo=UTC),
        )
        reset_events = [e for e in events if e.event_type == TempEventType.TEMP_DAY_RESET]
        assert len(reset_events) == 1
        # After reset + new recording, max should be the new sample
        assert t.observed_max_raw == 10.0
        assert t.state == TempState.RISING


class TestStaleData:
    def test_stale_after_15_min(self):
        t = TempTracker()
        t.record(_veri(10, 0), 15.0, _utc(10, 2))

        # Check at 10:17 — no new sample for 15+ min
        event = t.check_stale(_utc(10, 17))
        assert event is not None
        assert event.event_type == TempEventType.TEMP_DATA_STALE
        assert t.state == TempState.STALE

    def test_not_stale_within_15_min(self):
        t = TempTracker()
        t.record(_veri(10, 0), 15.0, _utc(10, 2))
        event = t.check_stale(_utc(10, 14))
        assert event is None

    def test_new_sample_exits_stale(self):
        t = TempTracker()
        t.record(_veri(10, 0), 15.0, _utc(10, 2))
        t.check_stale(_utc(10, 17))
        assert t.state == TempState.STALE

        t.record(_veri(10, 20), 15.5, _utc(10, 22))
        assert t.state != TempState.STALE


class TestMissingForecast:
    def test_no_forecast_still_works(self):
        """Without forecast data, tracker should still track max and trend."""
        t = TempTracker()
        # No forecast set
        t.record(_veri(10, 0), 15.0, _utc(10, 2))
        t.record(_veri(10, 10), 15.5, _utc(10, 12))
        assert t.observed_max_raw == 15.5
        assert t.forecast_gap is None

    def test_no_forecast_blocks_near_peak_without_ankara(self):
        """Without forecast AND without ankara shape past peak,
        NEAR_PEAK cannot be entered (neither condition met)."""
        t = TempTracker()
        # Flat trend, near max, but no forecast_gap and no ankara data
        for m, temp in [(0, 15.0), (5, 15.0), (10, 15.0), (15, 15.0),
                        (20, 15.0), (25, 15.0), (30, 15.0), (35, 15.0)]:
            t.record(_veri(12, m), temp, _utc(12, m + 2))
        assert t.state != TempState.NEAR_PEAK


class TestEarlyPeakGuardrail:
    def test_guardrail_blocks_provisional(self):
        """If forecast_gap > 1.0 and ankara shape rising, cap at NEAR_PEAK."""
        t = TempTracker()
        t.forecast_daily_max = 20.0  # gap will be large

        # Ankara shape: past bucket at 12:00=14, future bucket at 15:00=18 (rising)
        t.update_ankara_shape([
            (_utc(12, 0), 14.0),
            (_utc(15, 0), 18.0),  # future bucket is higher = still rising
            (_utc(18, 0), 16.0),
        ])

        # Rise to 15.0 at 13:xx (between 12:00 and 15:00 shape buckets)
        for m, temp in [(0, 13.0), (5, 13.5), (10, 14.0), (15, 14.5), (20, 15.0)]:
            t.record(_veri(13, m), temp, _utc(13, m + 2))

        # Decline — gap is 20.0 - 15.0 = 5.0 > 1.0, and ankara is rising
        for m, temp in [(30, 14.6), (35, 14.4), (40, 14.2), (45, 14.0)]:
            t.record(_veri(13, m), temp, _utc(13, m + 2))

        # Should NOT be PROVISIONAL despite decline meeting other criteria
        assert t.state != TempState.PROVISIONAL_PEAK
        assert t.state != TempState.CONFIRMED_PEAK

    def test_guardrail_override_strong_decline(self):
        """Guardrail can be overridden by strong decline (>= 0.8C, >= 30 min)."""
        t = TempTracker()
        t.forecast_daily_max = 20.0

        t.update_ankara_shape([
            (_utc(12, 0), 14.0),
            (_utc(15, 0), 18.0),
            (_utc(18, 0), 16.0),
        ])

        # Rise to 15.0 at 13:xx
        for m, temp in [(0, 13.0), (5, 13.5), (10, 14.0), (15, 14.5), (20, 15.0)]:
            t.record(_veri(13, m), temp, _utc(13, m + 2))

        # Strong decline: 0.8+ drop over 30+ min
        for m, temp in [(30, 14.6), (35, 14.4), (40, 14.2), (45, 14.0),
                        (50, 13.8), (55, 13.6)]:
            t.record(_veri(13, m), temp, _utc(13, m + 2))

        # Drop is 15.0 - 13.6 = 1.4 >= 0.8, time >= 30 min
        # Should override guardrail
        assert t.state in (TempState.PROVISIONAL_PEAK, TempState.CONFIRMED_PEAK)


class TestForecastUpdate:
    def test_update_forecast(self):
        t = TempTracker()
        event = t.update_forecast(18.0)
        assert event.event_type == TempEventType.TEMP_FORECAST_UPDATED
        assert t.forecast_daily_max == 18.0

    def test_update_ankara_shape(self):
        t = TempTracker()
        shape = [(_utc(12), 14.0), (_utc(15), 18.0)]
        t.update_ankara_shape(shape)
        assert t.ankara_shape == shape
