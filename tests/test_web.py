"""Tests for DB-backed web history endpoints."""

from fastapi.testclient import TestClient

from metar_monitor.client import MGMClient
from metar_monitor.db import Database
from metar_monitor.runtime import Runtime
from metar_monitor.state import MonitorState
from metar_monitor.web.server import create_app


def _make_runtime(tmp_path) -> Runtime:
    state = MonitorState(state_dir=str(tmp_path / "state"))
    state.load()

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
    db.record_surface_observation(
        airport_icao="LTAC",
        source_provider="mgm",
        source_external_id="17128",
        veri_zamani="2026-04-09T07:30:00.000Z",
        detected_at="2026-04-09T07:30:10+00:00",
        sicaklik=17.0,
        hissedilen_sicaklik=17.0,
        nem=35,
        ruzgar_hiz=6.0,
        ruzgar_yon=190,
        denize_indirgenmis_basinc=1016.0,
        aktuel_basinc=899.0,
        gorus=10000,
        kapalilik=1,
        hadise_kodu="PB",
    )
    db.record_metar(
        airport_icao="LTAC",
        source_provider="mgm",
        source_external_id="17128",
        metar_raw="LTAC 091200Z 19006KT 9999 17/06 Q1016",
        normalized_metar="LTAC 091200Z 19006KT 9999 17/06 Q1016",
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

    client = MGMClient()
    return Runtime(client=client, state=state, db=db)


def test_history_endpoints_use_runtime_db_helpers(tmp_path):
    rt = _make_runtime(tmp_path)
    app = create_app(rt)
    client = TestClient(app)

    metar_response = client.get("/api/history/metar")
    aws_response = client.get("/api/history/aws")

    assert metar_response.status_code == 200
    assert aws_response.status_code == 200
    assert metar_response.json()[0]["ddhhmmz"] == "081200Z"
    assert aws_response.json()[0]["sicaklik"] == 13.0


def test_history_endpoints_accept_local_day_query(tmp_path):
    rt = _make_runtime(tmp_path)
    app = create_app(rt)
    client = TestClient(app)

    metar_response = client.get("/api/history/metar", params={"local_day": "2026-04-09"})
    aws_response = client.get("/api/history/aws", params={"local_day": "2026-04-09"})
    forecast_response = client.get("/api/history/forecast", params={"local_day": "2026-04-09"})

    assert metar_response.status_code == 200
    assert aws_response.status_code == 200
    assert forecast_response.status_code == 200
    assert [row["ddhhmmz"] for row in metar_response.json()] == ["091200Z"]
    assert [row["sicaklik"] for row in aws_response.json()] == [17.0]
    assert [row["ltac_daily_max"] for row in forecast_response.json()] == [20]


def test_clear_history_endpoint_wipes_sqlite_history(tmp_path):
    rt = _make_runtime(tmp_path)
    app = create_app(rt)
    client = TestClient(app)

    response = client.post("/api/admin/clear-history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["cleared"]["sqlite_total_rows"] == 2
    assert payload["metar_history"] == []
    assert payload["aws_history"] == []
    assert payload["forecast_history"] == []
    assert client.get("/api/history/metar").json() == []
    assert client.get("/api/history/aws").json() == []
