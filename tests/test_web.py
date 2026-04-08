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
