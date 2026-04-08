"""Import legacy JSON state into the SQLite database."""

from __future__ import annotations


from .config import (
    AIRPORT_ICAO,
    MGM_DAILY_FORECAST_ISTNO,
    MGM_OBS_ISTNO,
    MGM_SHAPE_FORECAST_ISTNO,
)
from .db import Database
from .state import MonitorState


def import_monitor_state(state: MonitorState, db: Database) -> dict[str, int]:
    """Import the current MonitorState contents into SQLite.

    The import is intentionally append-oriented. Surface observations already
    dedupe via INSERT OR IGNORE in SQLite. METAR and capture rows are imported
    as historical facts from the legacy JSON file.
    """
    counts = {
        "metars": 0,
        "surface_observations": 0,
        "forecast_fetches": 0,
        "captures": 0,
        "adopted_metars": 0,
    }

    for entry in state.history:
        metar_raw = entry.get("metar")
        detected_at = entry.get("detected_at")
        if not metar_raw or not detected_at:
            continue
        db.record_metar(
            airport_icao=AIRPORT_ICAO,
            source_provider="mgm",
            source_external_id=str(MGM_OBS_ISTNO),
            metar_raw=metar_raw,
            normalized_metar=metar_raw,
            ddhhmmz=entry.get("ddhhmmz"),
            event_type=str(entry.get("event_type") or "new"),
            detected_at=detected_at,
        )
        counts["metars"] += 1

    history_keys = {
        (
            entry.get("metar"),
            entry.get("detected_at"),
            entry.get("event_type"),
        )
        for entry in state.history
    }
    if (
        state.last_seen_metar
        and state.last_seen_at
        and (state.last_seen_metar, state.last_seen_at, "adopted") not in history_keys
    ):
        db.record_metar(
            airport_icao=AIRPORT_ICAO,
            source_provider="mgm",
            source_external_id=str(MGM_OBS_ISTNO),
            metar_raw=state.last_seen_metar,
            normalized_metar=state.last_seen_metar,
            ddhhmmz=state.last_seen_ddhhmmz,
            event_type="adopted",
            detected_at=state.last_seen_at,
        )
        counts["adopted_metars"] += 1

    for entry in state.aws_history:
        veri_zamani = entry.get("veri_zamani")
        detected_at = entry.get("detected_at") or veri_zamani
        if not veri_zamani:
            continue
        raw_json = {
            "veri_zamani": veri_zamani,
            "detected_at": detected_at,
            "sicaklik": entry.get("sicaklik"),
            "nem": entry.get("nem"),
            "ruzgar_hiz": entry.get("ruzgar_hiz"),
            "gorus": entry.get("gorus"),
            "denize_indirgenmis_basinc": entry.get("denize_indirgenmis_basinc"),
            "ruzgar_yon": entry.get("ruzgar_yon"),
            "kapalilik": entry.get("kapalilik"),
        }
        db.record_surface_observation(
            airport_icao=AIRPORT_ICAO,
            source_provider="mgm",
            source_external_id=str(MGM_OBS_ISTNO),
            veri_zamani=veri_zamani,
            detected_at=detected_at,
            sicaklik=entry.get("sicaklik"),
            nem=entry.get("nem"),
            ruzgar_hiz=entry.get("ruzgar_hiz"),
            gorus=entry.get("gorus"),
            denize_indirgenmis_basinc=entry.get("denize_indirgenmis_basinc"),
            ruzgar_yon=entry.get("ruzgar_yon"),
            kapalilik=entry.get("kapalilik"),
            raw_json=raw_json,
        )
        counts["surface_observations"] += 1

    for entry in state.forecast_history:
        fetched_at = entry.get("fetched_at")
        if not fetched_at:
            continue
        db.record_forecast_fetch(
            airport_icao=AIRPORT_ICAO,
            source_provider="mgm",
            source_external_id=f"{MGM_DAILY_FORECAST_ISTNO}+{MGM_SHAPE_FORECAST_ISTNO}",
            forecast_kind="combined",
            fetched_at=fetched_at,
            raw_json={
                "fetched_at": fetched_at,
                "ltac_daily_max": entry.get("ltac_daily_max"),
                "ankara_peak_temp": entry.get("ankara_peak_temp"),
                "ankara_peak_time": entry.get("ankara_peak_time"),
                "ankara_shape": entry.get("ankara_shape") or [],
            },
        )
        counts["forecast_fetches"] += 1

    for entry in state.capture_log:
        detection_utc = entry.get("detection_utc")
        if not detection_utc:
            continue
        db.record_capture(
            airport_icao=AIRPORT_ICAO,
            ddhhmmz=entry.get("ddhhmmz"),
            detection_utc=detection_utc,
            delay_from_bulletin_s=entry.get("delay_from_bulletin_s"),
            source=entry.get("source"),
            event_type=entry.get("event_type"),
        )
        counts["captures"] += 1

    return counts
