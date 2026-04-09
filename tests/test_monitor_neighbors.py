"""Tests for the low-frequency Ankara neighbor ring snapshot."""

from datetime import datetime, timezone

import pytest

from metar_monitor.detector import MetarDetector
from metar_monitor.models import Observation
from metar_monitor.monitor import Monitor
from metar_monitor.schedule import Scheduler
from metar_monitor.state import MonitorState

UTC = timezone.utc


def _make_obs(station_id: int, veri_zamani: str, temp_c: float) -> Observation:
    return Observation(
        ist_no=station_id,
        veri_zamani=veri_zamani,
        sicaklik=temp_c,
        hissedilen_sicaklik=temp_c,
        nem=50,
        ruzgar_hiz=6.0,
        ruzgar_yon=180,
        aktuel_basinc=900.0,
        denize_indirgenmis_basinc=1018.0,
        gorus=10000,
        kapalilik=2,
        hadise_kodu="PB",
        rasat_metar="-9999",
        yagis_24_saat=0.0,
    )


class _FakeForecastClient:
    async def fetch_ltac_daily_forecast(self) -> float | None:
        return 16.0

    async def fetch_ankara_temp_shape(self) -> list[tuple[datetime, float]]:
        return [
            (datetime(2026, 4, 8, 12, 0, tzinfo=UTC), 12.0),
            (datetime(2026, 4, 8, 15, 0, tzinfo=UTC), 15.0),
            (datetime(2026, 4, 8, 18, 0, tzinfo=UTC), 14.0),
        ]

    async def fetch_ankara_station_ring(
        self,
        station_ids: tuple[int, ...],
    ) -> list[Observation]:
        assert station_ids == (17128, 18240, 18243, 18242, 17130)
        return [
            _make_obs(17128, "2026-04-08T07:30:00.000Z", 0.1),
            _make_obs(18240, "2026-04-08T07:30:00.000Z", -1.2),
            _make_obs(18243, "2026-04-08T07:30:00.000Z", -0.1),
            _make_obs(18242, "2026-04-08T07:30:00.000Z", -3.4),
            _make_obs(17130, "2026-04-08T07:30:00.000Z", 1.5),
        ]

    async def fetch_ankara_context_locations(
        self,
        station_ids: tuple[int, ...],
    ) -> list[dict]:
        assert station_ids == (17128, 18240, 18243, 18242, 17130)
        return [
            {
                "station_id": 17128,
                "daily_forecast_id": 90615,
                "hourly_forecast_id": None,
                "merkez_id": 90626,
                "district_name": "Esenboğa Havalimanı",
                "province_name": "Ankara",
                "lat": 40.1240,
                "lon": 32.9992,
                "elevation_m": 959,
            },
            {
                "station_id": 18240,
                "daily_forecast_id": 90605,
                "hourly_forecast_id": None,
                "merkez_id": 90605,
                "district_name": "Akyurt",
                "province_name": "Ankara",
                "lat": 40.1408,
                "lon": 33.1081,
                "elevation_m": 1114,
            },
            {
                "station_id": 18243,
                "daily_forecast_id": 90608,
                "hourly_forecast_id": None,
                "merkez_id": 90608,
                "district_name": "Pursaklar",
                "province_name": "Ankara",
                "lat": 40.0317,
                "lon": 32.8933,
                "elevation_m": 1065,
            },
            {
                "station_id": 18242,
                "daily_forecast_id": 90615,
                "hourly_forecast_id": None,
                "merkez_id": 90615,
                "district_name": "Çubuk",
                "province_name": "Ankara",
                "lat": 40.2867,
                "lon": 33.0108,
                "elevation_m": 1174,
            },
            {
                "station_id": 17130,
                "daily_forecast_id": 90601,
                "hourly_forecast_id": 17130,
                "merkez_id": 90609,
                "district_name": "Altındağ",
                "province_name": "Ankara",
                "lat": 39.943679,
                "lon": 32.872558,
                "elevation_m": 891,
            },
        ]

    async def fetch_daily_forecast_by_merkez_id(self, merkez_id: int) -> dict | None:
        values = {
            90605: 9,
            90608: 10,
            90615: 8,
            90601: 11,
        }
        return {
            "tarihGun1": "2026-04-09T00:00:00.000Z",
            "enYuksekGun1": values[merkez_id],
            "enDusukGun1": 0,
            "hadiseGun1": "PB",
            "ruzgarYonGun1": 180,
            "ruzgarHizGun1": 12,
        }


@pytest.mark.asyncio
async def test_fetch_forecasts_records_neighbor_ring_snapshot(tmp_path):
    state = MonitorState(state_dir=str(tmp_path / "state"))
    state.load()

    monitor = Monitor(
        client=_FakeForecastClient(),
        detector=MetarDetector(),
        state=state,
        scheduler=Scheduler(base_interval=5.0),
    )

    await monitor._fetch_forecasts()

    assert len(state.forecast_history) == 1
    snapshot = state.forecast_history[0]
    assert snapshot["ltac_daily_max"] == 16.0
    assert snapshot["ankara_peak_temp"] == 15.0
    assert snapshot["ankara_peak_time"] == "2026-04-08T15:00:00+00:00"
    assert [entry["label"] for entry in snapshot["neighbor_ring"]] == [
        "ESENBOGA",
        "AKYURT",
        "PURSAKLAR",
        "CUBUK",
        "ANKARA",
    ]
    assert [entry["sicaklik"] for entry in snapshot["neighbor_ring"]] == [
        0.1,
        -1.2,
        -0.1,
        -3.4,
        1.5,
    ]
    assert [entry["label"] for entry in snapshot["regional_daily_context"]] == [
        "AKYURT",
        "PURSAKLAR",
        "CUBUK",
        "ANKARA",
    ]
    assert [entry["forecast_daily_max"] for entry in snapshot["regional_daily_context"]] == [
        9,
        10,
        8,
        11,
    ]
    assert snapshot["context_stations"][0]["label"] == "ESENBOGA"
