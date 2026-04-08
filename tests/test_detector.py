"""Tests for METAR change detection."""

from datetime import datetime, timezone

from metar_monitor.detector import (
    MetarDetector,
    normalize_metar,
    parse_ddhhmmz,
    compute_delay_from_bulletin,
)
from metar_monitor.models import EventType, Observation
from tests.fixtures.mgm_responses import (
    NORMAL_RESPONSE,
    CORRECTION_RESPONSE,
    NEW_METAR_RESPONSE,
    UNAVAILABLE_RESPONSE,
    MONTH_ROLLOVER_RESPONSE,
    STALE_WHITESPACE_RESPONSE,
)

UTC = timezone.utc


def _obs(data: list[dict]) -> Observation:
    return Observation.from_dict(data[0])


class TestNormalizeMetar:
    def test_collapses_whitespace(self):
        assert normalize_metar("LTAC  230150Z  VRB01KT") == "LTAC 230150Z VRB01KT"

    def test_strips_edges(self):
        assert normalize_metar("  LTAC 230150Z  ") == "LTAC 230150Z"

    def test_identity(self):
        s = "LTAC 230150Z VRB01KT"
        assert normalize_metar(s) == s


class TestParseDDHHMMZ:
    def test_normal(self):
        assert parse_ddhhmmz("LTAC 230150Z VRB01KT") == "230150Z"

    def test_no_match(self):
        assert parse_ddhhmmz("no metar here") is None

    def test_multiple_takes_first(self):
        assert parse_ddhhmmz("LTAC 230150Z 230220Z") == "230150Z"


class TestComputeDelay:
    def test_normal_delay(self):
        detected = datetime(2026, 3, 23, 1, 52, 0, tzinfo=UTC)
        delay = compute_delay_from_bulletin("230150Z", detected)
        assert delay is not None
        assert abs(delay - 120.0) < 1.0  # 2 minutes

    def test_month_rollover(self):
        # DD=31, detected on April 1st
        detected = datetime(2026, 4, 1, 0, 5, 0, tzinfo=UTC)
        delay = compute_delay_from_bulletin("312350Z", detected)
        assert delay is not None
        assert delay > 0

    def test_unparseable(self):
        assert compute_delay_from_bulletin("XXXXX", datetime.now(UTC)) is None


class TestDetector:
    def test_first_observation_is_new(self):
        detector = MetarDetector()
        event = detector.check(_obs(NORMAL_RESPONSE))
        assert event.event_type == EventType.NEW_METAR
        assert "230150Z" in event.metar_raw

    def test_same_metar_is_same(self):
        detector = MetarDetector()
        detector.check(_obs(NORMAL_RESPONSE))
        event = detector.check(_obs(NORMAL_RESPONSE))
        assert event.event_type == EventType.SAME

    def test_correction_detected(self):
        detector = MetarDetector()
        detector.check(_obs(NORMAL_RESPONSE))
        event = detector.check(_obs(CORRECTION_RESPONSE))
        assert event.event_type == EventType.CORRECTION
        assert event.ddhhmmz == "230150Z"

    def test_new_metar_different_time(self):
        detector = MetarDetector()
        detector.check(_obs(NORMAL_RESPONSE))
        event = detector.check(_obs(NEW_METAR_RESPONSE))
        assert event.event_type == EventType.NEW_METAR
        assert event.ddhhmmz == "230220Z"

    def test_unavailable(self):
        # Seed veri_zamani so the first check doesn't trigger AWS_UPDATE
        detector = MetarDetector(last_seen_veri_zamani=UNAVAILABLE_RESPONSE[0]["veriZamani"])
        event = detector.check(_obs(UNAVAILABLE_RESPONSE))
        assert event.event_type == EventType.UNAVAILABLE

    def test_unavailable_with_new_veri_zamani_is_aws_update(self):
        """If veriZamani changes but METAR is -9999, it's an AWS update."""
        detector = MetarDetector(last_seen_veri_zamani="2026-03-22T00:00:00.000Z")
        event = detector.check(_obs(UNAVAILABLE_RESPONSE))
        assert event.event_type == EventType.AWS_UPDATE

    def test_unavailable_after_valid_does_not_clear_state(self):
        detector = MetarDetector()
        detector.check(_obs(NORMAL_RESPONSE))
        detector.check(_obs(UNAVAILABLE_RESPONSE))
        # State should still hold the last valid METAR
        assert detector.current_metar is not None

    def test_whitespace_normalized_is_same(self):
        """Extra whitespace in rasatMetar should normalize to Same, not New."""
        detector = MetarDetector()
        detector.check(_obs(NORMAL_RESPONSE))
        event = detector.check(_obs(STALE_WHITESPACE_RESPONSE))
        assert event.event_type == EventType.SAME

    def test_seeded_detector_no_false_new(self):
        """Detector seeded from persisted state should not fire New on first check."""
        metar = normalize_metar(NORMAL_RESPONSE[0]["rasatMetar"])
        detector = MetarDetector(
            last_seen_metar=metar,
            last_seen_ddhhmmz="230150Z",
            last_seen_veri_zamani=NORMAL_RESPONSE[0]["veriZamani"],
        )
        event = detector.check(_obs(NORMAL_RESPONSE))
        assert event.event_type == EventType.SAME

    def test_aws_update_when_veri_zamani_changes(self):
        """Same METAR but new veriZamani → AWS_UPDATE."""
        detector = MetarDetector()
        detector.check(_obs(NORMAL_RESPONSE))  # seed
        # Same METAR text, but different veriZamani
        modified = [{**NORMAL_RESPONSE[0], "veriZamani": "2026-03-23T02:06:00.000Z"}]
        event = detector.check(_obs(modified))
        assert event.event_type == EventType.AWS_UPDATE

    def test_month_rollover_is_new(self):
        detector = MetarDetector()
        detector.check(_obs(NORMAL_RESPONSE))
        event = detector.check(_obs(MONTH_ROLLOVER_RESPONSE))
        assert event.event_type == EventType.NEW_METAR
        assert event.ddhhmmz == "312350Z"

    def test_capture_record_for_new(self):
        detector = MetarDetector()
        event = detector.check(_obs(NORMAL_RESPONSE))
        record = detector.make_capture_record(event)
        assert record is not None
        assert record.event_type == "new"
        assert record.source == "mgm"
        assert record.ddhhmmz == "230150Z"

    def test_capture_record_none_for_same(self):
        detector = MetarDetector()
        detector.check(_obs(NORMAL_RESPONSE))
        event = detector.check(_obs(NORMAL_RESPONSE))
        assert detector.make_capture_record(event) is None
