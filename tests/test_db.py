"""Tests for the SQLite database backend."""

import json
import sqlite3
from datetime import date

from metar_monitor.db import Database


class TestDatabase:
    def _make_db(self, tmp_path):
        db = Database(db_path=str(tmp_path / "metar_monitor.db"))
        db.init_schema()
        db.ensure_airport(
            icao="LTAC",
            name="Ankara Esenboga",
            timezone_name="Europe/Istanbul",
            lat=40.115921,
            lon=32.986827,
            elevation_m=959,
            created_at="2026-04-08T00:00:00+00:00",
        )
        db.ensure_airport_source("LTAC", "mgm", "obs", "17128")
        db.ensure_airport_source("LTAC", "mgm", "daily_forecast", "90615")
        db.ensure_airport_source("LTAC", "mgm", "shape_forecast", "17130")
        return db

    def test_init_schema_creates_db_file(self, tmp_path):
        db = self._make_db(tmp_path)
        assert db.path.exists()

        conn = sqlite3.connect(str(db.path))
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()

        assert "airports" in tables
        assert "airport_sources" in tables
        assert "metar_events" in tables
        assert "surface_observations" in tables
        assert "forecast_fetches" in tables
        assert "capture_log" in tables

    def test_record_and_query_metar_round_trip(self, tmp_path):
        db = self._make_db(tmp_path)

        db.record_metar(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            metar_raw="LTAC 081200Z 18005KT 9999 15/05 Q1018",
            normalized_metar="LTAC 081200Z 18005KT 9999 15/05 Q1018",
            ddhhmmz="081200Z",
            event_type="new",
            detected_at="2026-04-08T12:01:00+00:00",
            delay_from_bulletin_s=60.0,
        )

        latest = db.get_latest_metar("LTAC")
        assert latest is not None
        assert latest["ddhhmmz"] == "081200Z"

        history = db.get_metar_history("LTAC")
        assert len(history) == 1
        assert history[0]["metar_raw"].startswith("LTAC 081200Z")

    def test_history_queries_support_until_bound(self, tmp_path):
        db = self._make_db(tmp_path)

        db.record_metar(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            metar_raw="LTAC 081200Z 18005KT 9999 15/05 Q1018",
            normalized_metar="LTAC 081200Z 18005KT 9999 15/05 Q1018",
            ddhhmmz="081200Z",
            event_type="new",
            detected_at="2026-04-08T12:01:00+00:00",
        )
        db.record_metar(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            metar_raw="LTAC 091200Z 18005KT 9999 17/05 Q1016",
            normalized_metar="LTAC 091200Z 18005KT 9999 17/05 Q1016",
            ddhhmmz="091200Z",
            event_type="new",
            detected_at="2026-04-09T12:01:00+00:00",
        )

        rows = db.get_metar_history("LTAC", until="2026-04-09T00:00:00+00:00")

        assert [row["ddhhmmz"] for row in rows] == ["081200Z"]

    def test_surface_observation_dedup_uses_insert_or_ignore(self, tmp_path):
        db = self._make_db(tmp_path)

        payload = {
            "istNo": 17128,
            "veriZamani": "2026-04-08T09:00:00.000Z",
            "sicaklik": 12.4,
        }

        kwargs = dict(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            veri_zamani="2026-04-08T09:00:00.000Z",
            detected_at="2026-04-08T09:00:10+00:00",
            sicaklik=12.4,
            hissedilen_sicaklik=12.4,
            nem=40,
            ruzgar_hiz=8.0,
            ruzgar_yon=180,
            aktuel_basinc=910.0,
            denize_indirgenmis_basinc=1018.2,
            gorus=10000,
            kapalilik=2,
            hadise_kodu="PB",
            raw_json=payload,
        )
        db.record_surface_observation(**kwargs)
        db.record_surface_observation(**kwargs)

        rows = db.get_surface_history("LTAC")
        assert len(rows) == 1
        assert json.loads(rows[0]["raw_json"])["sicaklik"] == 12.4

    def test_record_and_query_forecast_fetch(self, tmp_path):
        db = self._make_db(tmp_path)

        raw = {
            "fetched_at": "2026-04-08T10:00:00+00:00",
            "ltac_daily_max": 16,
            "ankara_shape": [
                {"tarih": "2026-04-08T12:00:00+00:00", "sicaklik": 12},
                {"tarih": "2026-04-08T15:00:00+00:00", "sicaklik": 15},
            ],
        }
        db.record_forecast_fetch(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17130",
            forecast_kind="shape_3h",
            fetched_at="2026-04-08T10:00:00+00:00",
            raw_json=raw,
        )

        rows = db.get_forecast_history("LTAC")
        assert len(rows) == 1
        stored = json.loads(rows[0]["raw_json"])
        assert stored["ankara_shape"][1]["sicaklik"] == 15

    def test_get_surface_observations_for_local_day_filters_by_timezone(self, tmp_path):
        db = self._make_db(tmp_path)

        db.record_surface_observation(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            veri_zamani="2026-04-07T20:30:00.000Z",
            detected_at="2026-04-07T20:30:10+00:00",
            sicaklik=9.0,
        )
        db.record_surface_observation(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            veri_zamani="2026-04-08T07:30:00.000Z",
            detected_at="2026-04-08T07:30:10+00:00",
            sicaklik=13.0,
        )

        rows = db.get_surface_observations_for_local_day(
            "LTAC",
            "Europe/Istanbul",
            local_day=date(2026, 4, 8),
        )
        assert len(rows) == 1
        assert rows[0]["sicaklik"] == 13.0

    def test_get_surface_observations_for_local_day_collapses_duplicate_veri_zamani(self, tmp_path):
        db = self._make_db(tmp_path)

        db.record_surface_observation(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            veri_zamani="2026-04-08T07:30:00.000Z",
            detected_at="2026-04-08T07:30:10+00:00",
            sicaklik=13.0,
        )
        db.record_surface_observation(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            veri_zamani="2026-04-08T07:30:00.000Z",
            detected_at="2026-04-08T07:30:20+00:00",
            sicaklik=13.2,
        )

        rows = db.get_surface_observations_for_local_day(
            "LTAC",
            "Europe/Istanbul",
            local_day=date(2026, 4, 8),
        )

        assert len(rows) == 1
        assert rows[0]["sicaklik"] == 13.2

    def test_clear_airport_history_removes_fact_rows_but_keeps_config(self, tmp_path):
        db = self._make_db(tmp_path)

        db.record_metar(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            metar_raw="LTAC 081200Z 18005KT 9999 15/05 Q1018",
            normalized_metar="LTAC 081200Z 18005KT 9999 15/05 Q1018",
            ddhhmmz="081200Z",
            event_type="new",
            detected_at="2026-04-08T12:01:00+00:00",
        )
        db.record_surface_observation(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            veri_zamani="2026-04-08T07:30:00.000Z",
            detected_at="2026-04-08T07:30:10+00:00",
            sicaklik=13.0,
        )
        db.record_forecast_fetch(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17130",
            forecast_kind="combined",
            fetched_at="2026-04-08T10:00:00+00:00",
            raw_json={"fetched_at": "2026-04-08T10:00:00+00:00"},
        )
        db.record_capture(
            airport_icao="LTAC",
            ddhhmmz="081200Z",
            detection_utc="2026-04-08T12:01:00+00:00",
            delay_from_bulletin_s=60.0,
            source="mgm",
            event_type="new",
        )

        counts = db.clear_airport_history("LTAC")

        assert counts == {
            "metar_events": 1,
            "surface_observations": 1,
            "forecast_fetches": 1,
            "capture_log": 1,
        }
        assert db.get_metar_history("LTAC") == []
        assert db.get_surface_history("LTAC") == []
        assert db.get_forecast_history("LTAC") == []

        conn = sqlite3.connect(str(db.path))
        try:
            airport_count = conn.execute("SELECT COUNT(*) FROM airports").fetchone()[0]
            source_count = conn.execute("SELECT COUNT(*) FROM airport_sources").fetchone()[0]
        finally:
            conn.close()

        assert airport_count == 1
        assert source_count == 3
