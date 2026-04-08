# SQLite + VPS Migration Plan

## Goal

Move persistence from the current JSON state file to SQLite so the monitor can
run 24/7 on a VPS without losing history, while keeping the current LTAC app
working during the migration.

Phase 1 is intentionally narrow:

- LTAC only
- SQLite becomes the primary long-term store
- JSON stays only as a temporary importer or explicit local fallback
- Textual remains supported for local development, but VPS deployment targets
  headless + web

Phase 2 comes later:

- airport/source parameterization
- multi-airport supervisor
- airport-aware web API and dashboard

## Current Constraints

The app is still LTAC-specific in several places:

- `src/metar_monitor/config.py`
- `src/metar_monitor/client.py`
- `src/metar_monitor/__main__.py`
- `src/metar_monitor/app.py`

The current runtime also replays temperature state from the JSON-backed
`aws_history` list in `src/metar_monitor/runtime.py`.

## Phase 1 Design

### Storage Strategy

- SQLite is the primary store
- JSON is not a permanent dual-write backend
- Existing `state.json` can be imported once into SQLite via a dedicated import
  path

### SQLite Schema

```sql
CREATE TABLE airports (
  icao TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  timezone TEXT NOT NULL,
  lat REAL,
  lon REAL,
  elevation_m INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE airport_sources (
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

CREATE TABLE metar_events (
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
CREATE INDEX idx_metar_airport_time
  ON metar_events(airport_icao, detected_at);

CREATE TABLE surface_observations (
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
CREATE INDEX idx_surface_airport_time
  ON surface_observations(airport_icao, veri_zamani);

CREATE TABLE forecast_fetches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  airport_icao TEXT NOT NULL REFERENCES airports(icao),
  source_provider TEXT NOT NULL,
  source_external_id TEXT NOT NULL,
  forecast_kind TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  raw_json TEXT NOT NULL
);
CREATE INDEX idx_forecast_airport_time
  ON forecast_fetches(airport_icao, fetched_at);

CREATE TABLE capture_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  airport_icao TEXT NOT NULL REFERENCES airports(icao),
  ddhhmmz TEXT,
  detection_utc TEXT NOT NULL,
  delay_from_bulletin_s REAL,
  source TEXT,
  event_type TEXT
);
CREATE INDEX idx_capture_airport_time
  ON capture_log(airport_icao, detection_utc);
```

### Why This Schema

- one shared schema for all airports
- airport-specific differences live in `airport_sources`, not separate schemas
- forecast snapshots are stored as raw JSON in phase 1 to keep the migration
  simple
- `surface_observations` uses `INSERT OR IGNORE` style dedup semantics

### LTAC Source Mapping

For LTAC, phase 1 should seed:

- `obs` -> MGM `17128`
- `daily_forecast` -> MGM `90615`
- `shape_forecast` -> MGM `17130`

## File-by-File Plan

### New Files

- `src/metar_monitor/db.py`
  - SQLite wrapper
  - schema init
  - record/query methods
  - WAL mode

- `src/metar_monitor/import_json.py`
  - imports existing `state.json` into SQLite

- `tests/test_db.py`
  - schema init
  - insert/query round-trip
  - dedup behavior
  - replay query for today's AWS observations

- `tests/test_import_json.py`
  - verifies JSON import mapping

- `deploy/metar-monitor.service`
  - systemd unit for the VPS

- `deploy/setup.sh`
  - first-pass Ubuntu setup

### Modified Files

- `src/metar_monitor/config.py`
  - add `DEFAULT_DB_PATH`

- `src/metar_monitor/monitor.py`
  - optional DB writes

- `src/metar_monitor/runtime.py`
  - replay from DB
  - DB-backed history access

- `src/metar_monitor/web/server.py`
  - history endpoints backed by SQLite

- `src/metar_monitor/__main__.py`
  - `--db-path`
  - `--import-json`

- `src/metar_monitor/app.py`
  - minimal change only if needed for local DB-backed runs

- `src/metar_monitor/state.py`
  - retained only as legacy fallback / import source during migration

## Rollout Order

1. Add `db.py` and DB tests
2. Add JSON importer
3. Add CLI flags and schema initialization
4. Write live data into SQLite from `Monitor`
5. Replay temp state from SQLite in `Runtime`
6. Move history endpoints to SQLite
7. Verify LTAC-only local run
8. Import existing JSON
9. Deploy to VPS

## VPS Target

Target box:

- provider: DigitalOcean
- region: FRA1
- OS: Ubuntu 24.04 LTS
- size: 1 GB RAM / 25 GB disk

Recommended service mode:

- `python -m metar_monitor --web --db-path /var/lib/metar-monitor/metar.db`

Recommended ops choices:

- systemd service
- SQLite WAL mode
- keep the dashboard behind auth or a private network layer

## Phase 2 After SQLite Is Stable

- parameterize airport/source config
- remove LTAC hard-coding
- supervisor for multiple airports
- airport-aware web API and dashboard
