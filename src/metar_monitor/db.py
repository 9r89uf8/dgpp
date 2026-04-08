"""SQLite persistence layer for long-running monitor history."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DEFAULT_DB_PATH

UTC = timezone.utc


def _as_iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _decode_json(value: str) -> object:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


class Database:
    """Small SQLite wrapper for LTAC history and future multi-airport use."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._path = Path(os.path.expanduser(db_path))

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def _connect(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS airports (
                  icao TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  timezone TEXT NOT NULL,
                  lat REAL,
                  lon REAL,
                  elevation_m INTEGER,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS airport_sources (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  airport_icao TEXT NOT NULL REFERENCES airports(icao),
                  provider TEXT NOT NULL,
                  product_kind TEXT NOT NULL,
                  external_id TEXT NOT NULL,
                  priority INTEGER NOT NULL DEFAULT 0,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  UNIQUE(airport_icao, provider, product_kind, external_id)
                );

                CREATE TABLE IF NOT EXISTS metar_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  airport_icao TEXT NOT NULL REFERENCES airports(icao),
                  source_provider TEXT NOT NULL,
                  source_external_id TEXT NOT NULL,
                  metar_raw TEXT NOT NULL,
                  normalized_metar TEXT NOT NULL,
                  ddhhmmz TEXT,
                  event_type TEXT NOT NULL,
                  detected_at TEXT NOT NULL,
                  delay_from_bulletin_s REAL
                );
                CREATE INDEX IF NOT EXISTS idx_metar_airport_time
                  ON metar_events(airport_icao, detected_at);

                CREATE TABLE IF NOT EXISTS surface_observations (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  airport_icao TEXT NOT NULL REFERENCES airports(icao),
                  source_provider TEXT NOT NULL,
                  source_external_id TEXT NOT NULL,
                  veri_zamani TEXT NOT NULL,
                  detected_at TEXT NOT NULL,
                  sicaklik REAL,
                  hissedilen_sicaklik REAL,
                  nem INTEGER,
                  ruzgar_hiz REAL,
                  ruzgar_yon INTEGER,
                  aktuel_basinc REAL,
                  denize_indirgenmis_basinc REAL,
                  gorus INTEGER,
                  kapalilik INTEGER,
                  hadise_kodu TEXT,
                  raw_json TEXT,
                  UNIQUE(airport_icao, source_provider, veri_zamani, sicaklik)
                );
                CREATE INDEX IF NOT EXISTS idx_surface_airport_time
                  ON surface_observations(airport_icao, veri_zamani);

                CREATE TABLE IF NOT EXISTS forecast_fetches (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  airport_icao TEXT NOT NULL REFERENCES airports(icao),
                  source_provider TEXT NOT NULL,
                  source_external_id TEXT NOT NULL,
                  forecast_kind TEXT NOT NULL,
                  fetched_at TEXT NOT NULL,
                  raw_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_forecast_airport_time
                  ON forecast_fetches(airport_icao, fetched_at);

                CREATE TABLE IF NOT EXISTS capture_log (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  airport_icao TEXT NOT NULL REFERENCES airports(icao),
                  ddhhmmz TEXT,
                  detection_utc TEXT NOT NULL,
                  delay_from_bulletin_s REAL,
                  source TEXT,
                  event_type TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_capture_airport_time
                  ON capture_log(airport_icao, detection_utc);
                """
            )

    def ensure_airport(
        self,
        icao: str,
        name: str,
        timezone_name: str,
        lat: float | None = None,
        lon: float | None = None,
        elevation_m: int | None = None,
        created_at: datetime | str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO airports (
                  icao, name, timezone, lat, lon, elevation_m, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    icao,
                    name,
                    timezone_name,
                    lat,
                    lon,
                    elevation_m,
                    _as_iso(created_at) or datetime.now(UTC).isoformat(),
                ),
            )

    def ensure_airport_source(
        self,
        airport_icao: str,
        provider: str,
        product_kind: str,
        external_id: str,
        priority: int = 0,
        enabled: bool = True,
        metadata: dict | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO airport_sources (
                  airport_icao, provider, product_kind, external_id,
                  priority, enabled, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    airport_icao,
                    provider,
                    product_kind,
                    external_id,
                    priority,
                    1 if enabled else 0,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )

    def record_metar(
        self,
        airport_icao: str,
        source_provider: str,
        source_external_id: str,
        metar_raw: str,
        normalized_metar: str,
        ddhhmmz: str | None,
        event_type: str,
        detected_at: datetime | str,
        delay_from_bulletin_s: float | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO metar_events (
                  airport_icao, source_provider, source_external_id,
                  metar_raw, normalized_metar, ddhhmmz, event_type,
                  detected_at, delay_from_bulletin_s
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    airport_icao,
                    source_provider,
                    source_external_id,
                    metar_raw,
                    normalized_metar,
                    ddhhmmz,
                    event_type,
                    _as_iso(detected_at),
                    delay_from_bulletin_s,
                ),
            )

    def record_surface_observation(
        self,
        airport_icao: str,
        source_provider: str,
        source_external_id: str,
        veri_zamani: str,
        detected_at: datetime | str,
        sicaklik: float | None,
        hissedilen_sicaklik: float | None = None,
        nem: int | None = None,
        ruzgar_hiz: float | None = None,
        ruzgar_yon: int | None = None,
        aktuel_basinc: float | None = None,
        denize_indirgenmis_basinc: float | None = None,
        gorus: int | None = None,
        kapalilik: int | None = None,
        hadise_kodu: str | None = None,
        raw_json: dict | str | None = None,
    ) -> None:
        raw_json_text = raw_json
        if isinstance(raw_json, dict):
            raw_json_text = json.dumps(raw_json, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO surface_observations (
                  airport_icao, source_provider, source_external_id,
                  veri_zamani, detected_at, sicaklik, hissedilen_sicaklik,
                  nem, ruzgar_hiz, ruzgar_yon, aktuel_basinc,
                  denize_indirgenmis_basinc, gorus, kapalilik,
                  hadise_kodu, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    airport_icao,
                    source_provider,
                    source_external_id,
                    veri_zamani,
                    _as_iso(detected_at),
                    sicaklik,
                    hissedilen_sicaklik,
                    nem,
                    ruzgar_hiz,
                    ruzgar_yon,
                    aktuel_basinc,
                    denize_indirgenmis_basinc,
                    gorus,
                    kapalilik,
                    hadise_kodu,
                    raw_json_text,
                ),
            )

    def record_forecast_fetch(
        self,
        airport_icao: str,
        source_provider: str,
        source_external_id: str,
        forecast_kind: str,
        fetched_at: datetime | str,
        raw_json: dict | list | str,
    ) -> None:
        raw_json_text = raw_json
        if not isinstance(raw_json, str):
            raw_json_text = json.dumps(raw_json, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO forecast_fetches (
                  airport_icao, source_provider, source_external_id,
                  forecast_kind, fetched_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    airport_icao,
                    source_provider,
                    source_external_id,
                    forecast_kind,
                    _as_iso(fetched_at),
                    raw_json_text,
                ),
            )

    def record_capture(
        self,
        airport_icao: str,
        ddhhmmz: str | None,
        detection_utc: datetime | str,
        delay_from_bulletin_s: float | None,
        source: str | None,
        event_type: str | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO capture_log (
                  airport_icao, ddhhmmz, detection_utc,
                  delay_from_bulletin_s, source, event_type
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    airport_icao,
                    ddhhmmz,
                    _as_iso(detection_utc),
                    delay_from_bulletin_s,
                    source,
                    event_type,
                ),
            )

    def get_metar_history(
        self,
        airport_icao: str,
        since: datetime | str | None = None,
        limit: int | None = None,
        event_types: tuple[str, ...] | None = None,
    ) -> list[dict]:
        sql = """
            SELECT metar_raw, ddhhmmz, event_type, detected_at
            FROM metar_events
            WHERE airport_icao = ?
        """
        params: list[object] = [airport_icao]
        if event_types:
            placeholders = ", ".join("?" for _ in event_types)
            sql += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        if since is not None:
            sql += " AND detected_at >= ?"
            params.append(_as_iso(since))
        sql += " ORDER BY detected_at ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_surface_history(
        self,
        airport_icao: str,
        since: datetime | str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        sql = """
            SELECT veri_zamani, detected_at, sicaklik, hissedilen_sicaklik,
                   nem, ruzgar_hiz, ruzgar_yon, aktuel_basinc,
                   denize_indirgenmis_basinc, gorus, kapalilik, hadise_kodu,
                   raw_json
            FROM surface_observations
            WHERE airport_icao = ?
        """
        params: list[object] = [airport_icao]
        if since is not None:
            sql += " AND veri_zamani >= ?"
            params.append(_as_iso(since))
        sql += " ORDER BY veri_zamani ASC, detected_at ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_forecast_history(
        self,
        airport_icao: str,
        since: datetime | str | None = None,
        limit: int | None = None,
        forecast_kind: str | None = None,
    ) -> list[dict]:
        sql = """
            SELECT source_provider, source_external_id, forecast_kind,
                   fetched_at, raw_json
            FROM forecast_fetches
            WHERE airport_icao = ?
        """
        params: list[object] = [airport_icao]
        if forecast_kind is not None:
            sql += " AND forecast_kind = ?"
            params.append(forecast_kind)
        if since is not None:
            sql += " AND fetched_at >= ?"
            params.append(_as_iso(since))
        sql += " ORDER BY fetched_at ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_latest_metar(self, airport_icao: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT metar_raw, ddhhmmz, event_type, detected_at
                FROM metar_events
                WHERE airport_icao = ?
                ORDER BY detected_at DESC
                LIMIT 1
                """,
                (airport_icao,),
            ).fetchone()
        return dict(row) if row else None

    def get_forecast_snapshots(
        self,
        airport_icao: str,
        since: datetime | str | None = None,
        limit: int | None = None,
        forecast_kind: str = "combined",
    ) -> list[dict]:
        rows = self.get_forecast_history(
            airport_icao=airport_icao,
            since=since,
            limit=limit,
            forecast_kind=forecast_kind,
        )
        snapshots: list[dict] = []
        for row in rows:
            payload = _decode_json(row["raw_json"])
            if isinstance(payload, dict):
                payload = dict(payload)
                payload.setdefault("fetched_at", row["fetched_at"])
                snapshots.append(payload)
        return snapshots

    def get_latest_forecast_snapshot(
        self,
        airport_icao: str,
        forecast_kind: str = "combined",
    ) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT fetched_at, raw_json
                FROM forecast_fetches
                WHERE airport_icao = ? AND forecast_kind = ?
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                (airport_icao, forecast_kind),
            ).fetchone()
        if not row:
            return None
        payload = _decode_json(row["raw_json"])
        if isinstance(payload, dict):
            payload = dict(payload)
            payload.setdefault("fetched_at", row["fetched_at"])
            return payload
        return None

    def get_latest_surface_observation(self, airport_icao: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT veri_zamani, detected_at, sicaklik, hissedilen_sicaklik,
                       nem, ruzgar_hiz, ruzgar_yon, aktuel_basinc,
                       denize_indirgenmis_basinc, gorus, kapalilik, hadise_kodu,
                       raw_json
                FROM surface_observations
                WHERE airport_icao = ?
                ORDER BY veri_zamani DESC
                LIMIT 1
                """,
                (airport_icao,),
            ).fetchone()
        return dict(row) if row else None

    def get_surface_observations_for_local_day(
        self,
        airport_icao: str,
        timezone_name: str,
        local_day: date | None = None,
    ) -> list[dict]:
        tz = ZoneInfo(timezone_name)
        day = local_day or datetime.now(tz).date()
        entries = self.get_surface_history(airport_icao)
        filtered_by_veri: dict[str, dict] = {}
        for entry in entries:
            veri = entry.get("veri_zamani")
            if not veri:
                continue
            try:
                dt = datetime.fromisoformat(str(veri).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
            except ValueError:
                continue
            if dt.astimezone(tz).date() == day:
                filtered_by_veri[str(veri)] = entry
        return sorted(
            filtered_by_veri.values(),
            key=lambda row: str(row.get("veri_zamani", "")),
        )
