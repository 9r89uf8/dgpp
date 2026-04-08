"""Tests for the polling scheduler."""

import time
from datetime import datetime, timezone
from unittest.mock import patch

from metar_monitor.schedule import Scheduler
from metar_monitor.config import (
    AGGRESSIVE_INTERVAL,
    APPROACH_WINDOW_S,
    BASE_INTERVAL,
    IDLE_INTERVAL,
    POST_PUBLISH_HOLD_S,
)


def _patch_hot(val: bool):
    """Patch _in_hot_window to return a fixed value."""
    return patch.object(Scheduler, "_in_hot_window", staticmethod(lambda: val))


def _patch_next_publish(seconds: int):
    """Patch _seconds_until_next_publish to return a fixed value."""
    return patch.object(
        Scheduler,
        "_seconds_until_next_publish",
        staticmethod(lambda: seconds),
    )


def _patch_now(dt: datetime):
    """Patch schedule.datetime.now() to return a fixed UTC time."""
    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return dt.astimezone(tz)
            return dt

    return patch("metar_monitor.schedule.datetime", _FakeDateTime)


class TestSchedulerIntervals:
    def test_far_from_publish_is_idle(self):
        with _patch_hot(False), _patch_next_publish(APPROACH_WINDOW_S + 1):
            s = Scheduler()
            assert s.get_interval() == IDLE_INTERVAL

    def test_post_publish_holds_base(self):
        with _patch_hot(False), _patch_next_publish(APPROACH_WINDOW_S + 1):
            s = Scheduler()
            s.notify_detection()
            assert s.get_interval() == BASE_INTERVAL

    def test_within_approach_window_uses_base(self):
        with _patch_hot(False), _patch_next_publish(APPROACH_WINDOW_S):
            s = Scheduler()
            assert s.get_interval() == BASE_INTERVAL

    def test_idles_after_post_publish_hold_if_publish_is_far(self):
        with _patch_hot(False), _patch_next_publish(APPROACH_WINDOW_S + 1):
            s = Scheduler()
            s.notify_detection()
            s._last_detection_mono = time.monotonic() - (POST_PUBLISH_HOLD_S + 1)
            assert s.get_interval() == IDLE_INTERVAL

    def test_returns_to_base_when_publish_approaches(self):
        with _patch_hot(False), _patch_next_publish(60):
            s = Scheduler()
            s.notify_detection()
            s._last_detection_mono = time.monotonic() - (POST_PUBLISH_HOLD_S + 1)
            assert s.get_interval() == BASE_INTERVAL


class TestHotWindows:
    def test_aggressive_in_hot_window(self):
        with _patch_hot(True):
            s = Scheduler()
            assert s.get_interval() == AGGRESSIVE_INTERVAL

    def test_normal_outside_hot_window(self):
        with _patch_hot(False), _patch_next_publish(APPROACH_WINDOW_S + 1):
            s = Scheduler()
            assert s.get_interval() == IDLE_INTERVAL

    def test_hot_window_overrides_idle(self):
        with _patch_hot(True):
            s = Scheduler()
            s.notify_detection()
            s._last_detection_mono = time.monotonic() - (POST_PUBLISH_HOLD_S + 1)
            assert s.get_interval() == AGGRESSIVE_INTERVAL

    def test_aggressive_label(self):
        with _patch_hot(True):
            s = Scheduler()
            assert s.interval_label == "AGGRESSIVE"

    def test_deadline_less_than_aggressive(self):
        with _patch_hot(True):
            s = Scheduler()
            assert s.request_deadline < AGGRESSIVE_INTERVAL

    def test_aggressive_at_metar_window_start(self):
        with _patch_now(datetime(2026, 4, 6, 0, 20, 0, tzinfo=timezone.utc)):
            assert Scheduler._in_hot_window() is True

    def test_aggressive_inside_first_metar_window(self):
        with _patch_now(datetime(2026, 4, 6, 0, 24, 7, tzinfo=timezone.utc)):
            assert Scheduler._in_hot_window() is True

    def test_aggressive_until_end_of_first_metar_window(self):
        with _patch_now(datetime(2026, 4, 6, 0, 26, 59, tzinfo=timezone.utc)):
            assert Scheduler._in_hot_window() is True

    def test_not_aggressive_after_first_metar_window(self):
        with _patch_now(datetime(2026, 4, 6, 0, 27, 0, tzinfo=timezone.utc)):
            assert Scheduler._in_hot_window() is False

    def test_aggressive_inside_second_metar_window(self):
        with _patch_now(datetime(2026, 4, 6, 0, 53, 30, tzinfo=timezone.utc)):
            assert Scheduler._in_hot_window() is True

    def test_not_aggressive_during_aws_only_minute(self):
        with _patch_now(datetime(2026, 4, 6, 0, 48, 30, tzinfo=timezone.utc)):
            assert Scheduler._in_hot_window() is False


class TestDeadlineRule:
    def test_deadline_less_than_interval(self):
        with _patch_hot(False), _patch_next_publish(APPROACH_WINDOW_S + 1):
            s = Scheduler()
            assert s.request_deadline < s.get_interval()

    def test_deadline_less_than_active(self):
        with _patch_hot(False), _patch_next_publish(APPROACH_WINDOW_S):
            s = Scheduler()
            assert s.request_deadline < BASE_INTERVAL

    def test_deadline_less_than_idle(self):
        with _patch_hot(False), _patch_next_publish(APPROACH_WINDOW_S + 1):
            s = Scheduler()
            assert s.request_deadline < IDLE_INTERVAL

    def test_deadline_minimum(self):
        """Deadline never goes below 0.5s even with tiny intervals."""
        with _patch_hot(False):
            s = Scheduler(base_interval=0.3)
            assert s.request_deadline >= 0.5


class TestTimingMethods:
    def test_first_poll_immediate(self):
        s = Scheduler()
        assert s.time_until_next_poll() == 0.0

    def test_after_poll_waits(self):
        with _patch_hot(False), _patch_next_publish(APPROACH_WINDOW_S):
            s = Scheduler()
            s.mark_poll_started()
            remaining = s.time_until_next_poll()
            assert remaining > 0
            assert remaining <= BASE_INTERVAL

    def test_interval_label_outside_hot(self):
        with _patch_hot(False), _patch_next_publish(APPROACH_WINDOW_S):
            s = Scheduler()
            assert s.interval_label == "ACTIVE"
        with _patch_hot(False), _patch_next_publish(APPROACH_WINDOW_S + 1):
            s = Scheduler()
            assert s.interval_label == "IDLE"
