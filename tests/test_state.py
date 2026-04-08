"""Tests for state persistence."""

import json
import os
import tempfile
from pathlib import Path

from metar_monitor.state import MonitorState
from metar_monitor.models import CaptureRecord


class TestStatePersistence:
    def _make_state(self, tmp: str) -> MonitorState:
        return MonitorState(state_dir=tmp)

    def test_save_and_load_round_trip(self, tmp_path):
        s = self._make_state(str(tmp_path))
        s.record_event(
            metar_raw="LTAC 230150Z VRB01KT 9999",
            ddhhmmz="230150Z",
            detected_at_iso="2026-03-23T01:52:00Z",
            event_type="new",
        )

        s2 = self._make_state(str(tmp_path))
        s2.load()
        assert s2.last_seen_metar == "LTAC 230150Z VRB01KT 9999"
        assert s2.last_seen_ddhhmmz == "230150Z"
        assert len(s2.history) == 1
        assert s2.history[0]["event_type"] == "new"

    def test_atomic_write_creates_no_tmp_files(self, tmp_path):
        s = self._make_state(str(tmp_path))
        s.record_event("LTAC 230150Z", "230150Z", "2026-03-23T01:52:00Z", "new")
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "state.json"

    def test_corrupt_file_starts_fresh(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("{broken json", encoding="utf-8")
        s = self._make_state(str(tmp_path))
        s.load()
        assert s.last_seen_metar is None
        assert s.history == []

    def test_missing_file_starts_fresh(self, tmp_path):
        s = self._make_state(str(tmp_path))
        s.load()
        assert s.last_seen_metar is None

    def test_adopt_current(self, tmp_path):
        s = self._make_state(str(tmp_path))
        s.adopt_current("LTAC 230150Z", "230150Z", "2026-03-23T01:52:00Z")

        s2 = self._make_state(str(tmp_path))
        s2.load()
        assert s2.last_seen_metar == "LTAC 230150Z"
        assert len(s2.history) == 0  # adopt doesn't add to history

    def test_capture_record_persists(self, tmp_path):
        s = self._make_state(str(tmp_path))
        record = CaptureRecord(
            ddhhmmz="230150Z",
            detection_utc="2026-03-23T01:52:00Z",
            delay_from_bulletin_s=120.0,
            source="mgm",
            event_type="new",
        )
        s.record_capture(record)

        s2 = self._make_state(str(tmp_path))
        s2.load()
        assert len(s2.capture_log) == 1
        assert s2.capture_log[0]["ddhhmmz"] == "230150Z"

    def test_history_trimmed(self, tmp_path):
        s = self._make_state(str(tmp_path))
        for i in range(60):
            s.record_event(f"METAR_{i}", f"23{i:04d}Z", "2026-03-23T00:00:00Z", "new")

        s2 = self._make_state(str(tmp_path))
        s2.load()
        assert len(s2.history) == 50  # MAX_HISTORY
