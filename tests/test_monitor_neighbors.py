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
        assert station_ids == (17128, 18240, 18243, 18242)
        return [
            _make_obs(17128, "2026-04-08T07:30:00.000Z", 0.1),
            _make_obs(18240, "2026-04-08T07:30:00.000Z", -1.2),
            _make_obs(18243, "2026-04-08T07:30:00.000Z", -0.1),
            _make_obs(18242, "2026-04-08T07:30:00.000Z", -3.4),
        ]


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
    ]
    assert [entry["sicaklik"] for entry in snapshot["neighbor_ring"]] == [
        0.1,
        -1.2,
        -0.1,
        -3.4,
    ]
