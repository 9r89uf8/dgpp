"""Tests for the shared runtime."""

import json
from datetime import datetime, timezone

from metar_monitor.models import EventType, MetarEvent, Observation, PollStats
from metar_monitor.runtime import Runtime
from metar_monitor.client import MGMClient
from metar_monitor.db import Database
from metar_monitor.state import MonitorState


UTC = timezone.utc


def _make_runtime(tmp_path) -> Runtime:
    state = MonitorState(state_dir=str(tmp_path))
    state.load()
    client = MGMClient()
    return Runtime(client=client, state=state)


def _make_db(tmp_path) -> Database:
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


def _make_obs(**overrides) -> Observation:
    defaults = dict(
        ist_no=17128, veri_zamani="2026-04-07T10:00:00.000Z",
        sicaklik=15.0, hissedilen_sicaklik=15.0, nem=40,
        ruzgar_hiz=5.0, ruzgar_yon=180, aktuel_basinc=900,
        denize_indirgenmis_basinc=1018.0, gorus=10000, kapalilik=4,
        hadise_kodu="PB", rasat_metar="LTAC 071000Z 18005KT 9999 15/05 Q1018",
        yagis_24_saat=0,
    )
    defaults.update(overrides)
    return Observation(**defaults)


class TestSnapshotSerialization:
    def test_snapshot_is_json_safe(self, tmp_path):
        rt = _make_runtime(tmp_path)
        snap = rt.snapshot()
        # Must not raise
        serialized = json.dumps(snap)
        assert isinstance(serialized, str)

    def test_snapshot_with_observation(self, tmp_path):
        rt = _make_runtime(tmp_path)
        rt.current_observation = _make_obs()
        snap = rt.snapshot()
        assert snap["current_observation"]["sicaklik"] == 15.0
        assert snap["is_healthy"] is True

    def test_minus_9999_becomes_null(self, tmp_path):
        rt = _make_runtime(tmp_path)
        rt.current_observation = _make_obs(sicaklik=-9999)
        snap = rt.snapshot()
        assert snap["current_observation"]["sicaklik"] is None

    def test_snapshot_without_observation(self, tmp_path):
        rt = _make_runtime(tmp_path)
        snap = rt.snapshot()
        assert snap["current_observation"] is None


class TestErrorStateFields:
    def test_error_updates_fields(self, tmp_path):
        rt = _make_runtime(tmp_path)
        now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
        event = MetarEvent(
            event_type=EventType.FETCH_ERROR,
            error="ConnectTimeout",
            detected_at=now,
        )
        rt.handle_event(event, PollStats())
        assert rt.last_error == "ConnectTimeout"
        assert rt.last_error_at == now
        snap = rt.snapshot()
        assert snap["is_healthy"] is False
        assert snap["last_error"] == "ConnectTimeout"

    def test_success_clears_error(self, tmp_path):
        rt = _make_runtime(tmp_path)
        now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
        # Error first
        rt.handle_event(
            MetarEvent(event_type=EventType.FETCH_ERROR, error="fail", detected_at=now),
            PollStats(),
        )
        assert rt.last_error is not None
        # Then success
        obs = _make_obs()
        rt.handle_event(
            MetarEvent(event_type=EventType.SAME, observation=obs, detected_at=now),
            PollStats(),
        )
        assert rt.last_error is None
        assert rt.last_success_at == now

    def test_new_metar_updates_current(self, tmp_path):
        rt = _make_runtime(tmp_path)
        now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
        obs = _make_obs()
        rt.handle_event(
            MetarEvent(
                event_type=EventType.NEW_METAR,
                observation=obs,
                metar_raw="LTAC 071200Z ...",
                detected_at=now,
            ),
            PollStats(),
        )
        assert rt.current_metar == "LTAC 071200Z ..."
        assert rt.current_metar_detected_at == now

    def test_unavailable_clears_observation(self, tmp_path):
        rt = _make_runtime(tmp_path)
        rt.current_observation = _make_obs()
        now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
        rt.handle_event(
            MetarEvent(event_type=EventType.UNAVAILABLE, detected_at=now),
            PollStats(),
        )
        assert rt.current_observation is None


class TestTempReplay:
    def test_replay_from_aws_history(self, tmp_path):
        """Temp tracker should reconstruct state from persisted aws_history."""
        from zoneinfo import ZoneInfo
        ISTANBUL = ZoneInfo("Europe/Istanbul")

        state = MonitorState(state_dir=str(tmp_path))

        # Use current Istanbul day, morning hours (guaranteed to be "today")
        now_istanbul = datetime.now(ISTANBUL)
        today = now_istanbul.date()
        # Build samples at 06:00-07:00 Istanbul time (always in the past on any test run)
        # Convert to UTC for veri_zamani
        for i, temp in enumerate([14.0, 14.5, 15.0, 15.5, 16.0, 15.8, 15.5]):
            total_min = 6 * 60 + i * 8  # 06:00, 06:08, 06:16, ... (within one hour)
            istanbul_hour = total_min // 60
            istanbul_min = total_min % 60
            local_dt = datetime(today.year, today.month, today.day,
                               istanbul_hour, istanbul_min, tzinfo=ISTANBUL)
            utc_dt = local_dt.astimezone(UTC)
            veri = utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            state.aws_history.append({
                "veri_zamani": veri,
                "detected_at": veri,
                "sicaklik": temp,
                "nem": 40,
                "ruzgar_hiz": 5.0,
                "gorus": 10000,
            })
        state.save()

        # Reload and create runtime — should replay
        state2 = MonitorState(state_dir=str(tmp_path))
        state2.load()
        client = MGMClient()
        rt = Runtime(client=client, state=state2)

        # Tracker should have observed the max
        assert rt.temp_tracker.observed_max_raw is not None
        assert rt.temp_tracker.observed_max_raw >= 15.5
        assert len(rt.temp_tracker.samples) > 0

    def test_runtime_uses_db_for_replay_and_history(self, tmp_path):
        state = MonitorState(state_dir=str(tmp_path / "state"))
        state.load()
        db = _make_db(tmp_path)

        db.record_surface_observation(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            veri_zamani="2026-04-08T07:30:00.000Z",
            detected_at="2026-04-08T07:30:10+00:00",
            sicaklik=13.0,
            hissedilen_sicaklik=13.0,
            nem=40,
            ruzgar_hiz=5.0,
            ruzgar_yon=180,
            denize_indirgenmis_basinc=1018.0,
            aktuel_basinc=900.0,
            gorus=10000,
            kapalilik=2,
            hadise_kodu="PB",
        )
        db.record_metar(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            metar_raw="LTAC 081200Z 18005KT 9999 13/05 Q1018",
            normalized_metar="LTAC 081200Z 18005KT 9999 13/05 Q1018",
            ddhhmmz="081200Z",
            event_type="new",
            detected_at="2026-04-08T12:01:00+00:00",
        )
        db.record_forecast_fetch(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="90615+17130",
            forecast_kind="combined",
            fetched_at="2026-04-08T10:00:00+00:00",
            raw_json={
                "fetched_at": "2026-04-08T10:00:00+00:00",
                "ltac_daily_max": 16,
                "ankara_peak_temp": 15,
                "ankara_peak_time": "2026-04-08T12:00:00+00:00",
                "ankara_shape": [],
                "neighbor_ring": [
                    {
                        "station_id": 17128,
                        "label": "ESENBOGA",
                        "veri_zamani": "2026-04-08T07:30:00.000Z",
                        "sicaklik": 13.0,
                    }
                ],
                "context_stations": [
                    {
                        "station_id": 17128,
                        "label": "ESENBOGA",
                        "district_name": "Esenboğa Havalimanı",
                        "lat": 40.1240,
                        "lon": 32.9992,
                    }
                ],
                "regional_daily_context": [
                    {
                        "station_id": 17130,
                        "label": "ANKARA",
                        "forecast_daily_max": 11,
                    }
                ],
            },
        )

        client = MGMClient()
        rt = Runtime(client=client, state=state, db=db)

        assert rt.temp_tracker.samples
        assert rt.aws_history()[-1]["sicaklik"] == 13.0
        assert rt.metar_history()[-1]["ddhhmmz"] == "081200Z"
        assert rt.latest_forecast_snapshot()["ltac_daily_max"] == 16
        assert rt.latest_forecast_snapshot()["neighbor_ring"][0]["label"] == "ESENBOGA"
        assert rt.latest_forecast_snapshot()["regional_daily_context"][0]["label"] == "ANKARA"

    def test_runtime_history_filters_by_local_day_with_db(self, tmp_path):
        state = MonitorState(state_dir=str(tmp_path / "state"))
        state.load()
        db = _make_db(tmp_path)

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
            veri_zamani="2026-04-09T07:30:00.000Z",
            detected_at="2026-04-09T07:30:10+00:00",
            sicaklik=17.0,
        )
        db.record_metar(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            metar_raw="LTAC 081200Z 18005KT 9999 13/05 Q1018",
            normalized_metar="LTAC 081200Z 18005KT 9999 13/05 Q1018",
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
        db.record_forecast_fetch(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="90615+17130",
            forecast_kind="combined",
            fetched_at="2026-04-08T10:00:00+00:00",
            raw_json={"fetched_at": "2026-04-08T10:00:00+00:00", "ltac_daily_max": 16},
        )
        db.record_forecast_fetch(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="90615+17130",
            forecast_kind="combined",
            fetched_at="2026-04-09T10:00:00+00:00",
            raw_json={"fetched_at": "2026-04-09T10:00:00+00:00", "ltac_daily_max": 20},
        )

        rt = Runtime(client=MGMClient(), state=state, db=db)

        assert [row["sicaklik"] for row in rt.aws_history(local_day="2026-04-08")] == [13.0]
        assert [row["sicaklik"] for row in rt.aws_history(local_day="2026-04-09")] == [17.0]
        assert [row["ddhhmmz"] for row in rt.metar_history(local_day="2026-04-08")] == ["081200Z"]
        assert [row["ddhhmmz"] for row in rt.metar_history(local_day="2026-04-09")] == ["091200Z"]
        assert [row["ltac_daily_max"] for row in rt.forecast_history(local_day="2026-04-08")] == [16]
        assert [row["ltac_daily_max"] for row in rt.forecast_history(local_day="2026-04-09")] == [20]

    def test_runtime_falls_back_to_json_history_when_db_is_empty(self, tmp_path):
        state = MonitorState(state_dir=str(tmp_path / "state"))
        state.history = [
            {
                "metar": "LTAC 081200Z 18005KT 9999 13/05 Q1018",
                "ddhhmmz": "081200Z",
                "detected_at": "2026-04-08T12:01:00+00:00",
                "event_type": "new",
            }
        ]
        state.aws_history = [
            {
                "veri_zamani": "2026-04-08T07:30:00.000Z",
                "detected_at": "2026-04-08T07:30:10+00:00",
                "sicaklik": 13.0,
                "nem": 40,
                "ruzgar_hiz": 5.0,
                "gorus": 10000,
            }
        ]
        state.forecast_history = [
            {
                "fetched_at": "2026-04-08T10:00:00+00:00",
                "ltac_daily_max": 16,
                "ankara_peak_temp": 15,
                "ankara_peak_time": "2026-04-08T12:00:00+00:00",
                "ankara_shape": [],
            }
        ]
        db = _make_db(tmp_path)
        client = MGMClient()

        rt = Runtime(client=client, state=state, db=db)

        assert rt.metar_history()[-1]["ddhhmmz"] == "081200Z"
        assert rt.aws_history()[-1]["sicaklik"] == 13.0
        assert rt.latest_forecast_snapshot()["ltac_daily_max"] == 16

    def test_runtime_history_filters_by_local_day_without_db(self, tmp_path):
        state = MonitorState(state_dir=str(tmp_path / "state"))
        state.history = [
            {
                "metar": "LTAC 081200Z 18005KT 9999 13/05 Q1018",
                "ddhhmmz": "081200Z",
                "detected_at": "2026-04-08T12:01:00+00:00",
                "event_type": "new",
            },
            {
                "metar": "LTAC 091200Z 18005KT 9999 17/05 Q1016",
                "ddhhmmz": "091200Z",
                "detected_at": "2026-04-09T12:01:00+00:00",
                "event_type": "new",
            },
        ]
        state.aws_history = [
            {
                "veri_zamani": "2026-04-08T07:30:00.000Z",
                "detected_at": "2026-04-08T07:30:10+00:00",
                "sicaklik": 13.0,
            },
            {
                "veri_zamani": "2026-04-09T07:30:00.000Z",
                "detected_at": "2026-04-09T07:30:10+00:00",
                "sicaklik": 17.0,
            },
        ]
        state.forecast_history = [
            {
                "fetched_at": "2026-04-08T10:00:00+00:00",
                "ltac_daily_max": 16,
            },
            {
                "fetched_at": "2026-04-09T10:00:00+00:00",
                "ltac_daily_max": 20,
            },
        ]

        rt = Runtime(client=MGMClient(), state=state)

        assert [row["sicaklik"] for row in rt.aws_history(local_day="2026-04-08")] == [13.0]
        assert [row["sicaklik"] for row in rt.aws_history(local_day="2026-04-09")] == [17.0]
        assert [row["ddhhmmz"] for row in rt.metar_history(local_day="2026-04-08")] == ["081200Z"]
        assert [row["ddhhmmz"] for row in rt.metar_history(local_day="2026-04-09")] == ["091200Z"]
        assert [row["ltac_daily_max"] for row in rt.forecast_history(local_day="2026-04-08")] == [16]
        assert [row["ltac_daily_max"] for row in rt.forecast_history(local_day="2026-04-09")] == [20]

    def test_clear_persisted_history_clears_db_state_and_tracker(self, tmp_path):
        state = MonitorState(state_dir=str(tmp_path / "state"))
        state.history = [
            {
                "metar": "LTAC 081200Z 18005KT 9999 13/05 Q1018",
                "ddhhmmz": "081200Z",
                "detected_at": "2026-04-08T12:01:00+00:00",
                "event_type": "new",
            }
        ]
        state.aws_history = [
            {
                "veri_zamani": "2026-04-08T07:30:00.000Z",
                "detected_at": "2026-04-08T07:30:10+00:00",
                "sicaklik": 13.0,
                "nem": 40,
                "ruzgar_hiz": 5.0,
                "gorus": 10000,
            }
        ]
        state.forecast_history = [
            {
                "fetched_at": "2026-04-08T10:00:00+00:00",
                "ltac_daily_max": 16,
                "ankara_peak_temp": 15,
                "ankara_peak_time": "2026-04-08T12:00:00+00:00",
                "ankara_shape": [],
            }
        ]
        state.last_seen_metar = "LTAC 081200Z 18005KT 9999 13/05 Q1018"
        state.last_seen_ddhhmmz = "081200Z"
        state.last_seen_at = "2026-04-08T12:01:00+00:00"
        state.last_seen_veri_zamani = "2026-04-08T07:30:00.000Z"
        state.save()

        db = _make_db(tmp_path)
        db.record_metar(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="17128",
            metar_raw="LTAC 081200Z 18005KT 9999 13/05 Q1018",
            normalized_metar="LTAC 081200Z 18005KT 9999 13/05 Q1018",
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
            hissedilen_sicaklik=13.0,
            nem=40,
            ruzgar_hiz=5.0,
            ruzgar_yon=180,
            denize_indirgenmis_basinc=1018.0,
            aktuel_basinc=900.0,
            gorus=10000,
            kapalilik=2,
            hadise_kodu="PB",
        )
        db.record_forecast_fetch(
            airport_icao="LTAC",
            source_provider="mgm",
            source_external_id="90615+17130",
            forecast_kind="combined",
            fetched_at="2026-04-08T10:00:00+00:00",
            raw_json={
                "fetched_at": "2026-04-08T10:00:00+00:00",
                "ltac_daily_max": 16,
                "ankara_peak_temp": 15,
                "ankara_peak_time": "2026-04-08T12:00:00+00:00",
                "ankara_shape": [],
            },
        )

        client = MGMClient()
        rt = Runtime(client=client, state=state, db=db)

        cleared = rt.clear_persisted_history()

        assert cleared["ok"] is True
        assert cleared["cleared"]["sqlite_total_rows"] == 3
        assert cleared["metar_history"] == []
        assert cleared["aws_history"] == []
        assert cleared["forecast_history"] == []
        assert rt.metar_history() == []
        assert rt.aws_history() == []
        assert rt.forecast_history() == []
        assert rt.latest_forecast_snapshot() is None
        assert rt.temp_tracker.samples == []
        assert rt.temp_tracker.observed_max_raw is None
        assert rt.temp_tracker.forecast_daily_max is None
        assert rt.state.history == []
        assert rt.state.aws_history == []
        assert rt.state.forecast_history == []
        assert rt.state.last_seen_metar == "LTAC 081200Z 18005KT 9999 13/05 Q1018"
