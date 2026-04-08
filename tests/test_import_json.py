"""Tests for importing legacy state.json into SQLite."""

from metar_monitor.db import Database
from metar_monitor.import_json import import_monitor_state
from metar_monitor.state import MonitorState


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


def test_import_monitor_state_round_trip(tmp_path):
    state = MonitorState(state_dir=str(tmp_path / "state"))
    state.last_seen_metar = "LTAC 081230Z 18005KT 9999 14/05 Q1018"
    state.last_seen_ddhhmmz = "081230Z"
    state.last_seen_at = "2026-04-08T12:31:00+00:00"
    state.last_seen_veri_zamani = "2026-04-08T12:30:00.000Z"
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
            "veri_zamani": "2026-04-08T12:30:00.000Z",
            "detected_at": "2026-04-08T12:30:05+00:00",
            "sicaklik": 14.2,
            "nem": 41,
            "ruzgar_hiz": 7.0,
            "gorus": 10000,
            "denize_indirgenmis_basinc": 1018.0,
            "ruzgar_yon": 180,
            "kapalilik": 2,
        }
    ]
    state.capture_log = [
        {
            "ddhhmmz": "081200Z",
            "detection_utc": "2026-04-08T12:01:00+00:00",
            "delay_from_bulletin_s": 60.0,
            "source": "mgm",
            "event_type": "new_metar",
        }
    ]
    state.forecast_history = [
        {
            "fetched_at": "2026-04-08T10:00:00+00:00",
            "ltac_daily_max": 16,
            "ankara_peak_temp": 15,
            "ankara_peak_time": "2026-04-08T12:00:00+00:00",
            "ankara_shape": [
                {"tarih": "2026-04-08T12:00:00+00:00", "sicaklik": 15}
            ],
        }
    ]

    db = _make_db(tmp_path)
    counts = import_monitor_state(state, db)

    assert counts["metars"] == 1
    assert counts["adopted_metars"] == 1
    assert counts["surface_observations"] == 1
    assert counts["forecast_fetches"] == 1
    assert counts["captures"] == 1

    metars = db.get_metar_history("LTAC")
    aws_rows = db.get_surface_history("LTAC")
    forecasts = db.get_forecast_snapshots("LTAC")

    assert len(metars) == 2
    assert metars[0]["ddhhmmz"] == "081200Z"
    assert aws_rows[0]["sicaklik"] == 14.2
    assert forecasts[0]["ltac_daily_max"] == 16
