# Monitor Infrastructure Overview

This document summarizes the current production setup for the LTAC monitor:

- where it is hosted
- what database it uses
- which airport is currently being collected
- how the current design supports future airport expansion

## Current Host

The monitor is currently deployed on:

- provider: `DigitalOcean`
- region: `FRA1`
- machine size: `1 GB RAM / 25 GB disk`
- OS: `Ubuntu 24.04 LTS x64`

Current deployment model:

- one VPS
- one long-running `systemd` service
- one SQLite database on local disk
- one public web dashboard

## Runtime Layout

Current production paths:

- app directory: `/opt/metar-monitor`
- Python venv: `/opt/metar-monitor/.venv`
- SQLite database: `/var/lib/metar-monitor/metar.db`
- service name: `metar-monitor`

Current service entrypoint:

```bash
python -m metar_monitor --web --db-path /var/lib/metar-monitor/metar.db
```

The deployed service is headless + web:

- continuous polling
- SQLite persistence
- public dashboard on port `8080`

## Database

Primary database:

- engine: `SQLite`
- mode: `WAL`
- file path: `/var/lib/metar-monitor/metar.db`

SQLite is the primary long-term store for the monitor.

### Current Tables

#### `airports`

Stores airport identity:

- ICAO
- name
- timezone
- coordinates
- elevation

#### `airport_sources`

Stores source mapping per airport:

- provider
- product kind
- external source id
- priority
- enabled flag

This is the table that makes multi-airport support possible without separate schemas.

#### `metar_events`

Stores detected METAR events:

- raw METAR
- normalized METAR
- `ddhhmmz`
- event type
- detection time
- delay from bulletin time

#### `surface_observations`

Stores unique AWS/surface observation updates:

- `veri_zamani`
- detection time
- temperature
- humidity
- wind speed
- wind direction
- pressure
- visibility
- cloud cover
- weather code
- raw JSON payload

#### `forecast_fetches`

Stores forecast snapshots:

- fetch time
- forecast type
- raw JSON payload

This currently includes:

- LTAC airport daily max forecast
- Ankara center 3-hour forecast shape snapshots

#### `capture_log`

Stores METAR capture timing data:

- bulletin time group
- detection time
- computed delay
- source
- event type

## Current Airport Coverage

The current deployed monitor is **LTAC only**.

### Airport

- ICAO: `LTAC`
- name: `Ankara Esenboga`
- timezone: `Europe/Istanbul`
- coordinates: `40.115921, 32.986827`
- elevation: `959 m`

### Current LTAC Source Mapping

#### Live observation + raw METAR

- provider: `MGM`
- product kind: `obs`
- source id: `17128`

Endpoint:

- `https://servis.mgm.gov.tr/web/sondurumlar?istno=17128`

#### LTAC daily forecast

- provider: `MGM`
- product kind: `daily_forecast`
- source id: `90615`

Endpoint:

- `https://servis.mgm.gov.tr/web/tahminler/gunluk?istno=90615`

#### Ankara center 3-hour forecast shape

- provider: `MGM`
- product kind: `shape_forecast`
- source id: `17130`

Endpoint:

- `https://servis.mgm.gov.tr/web/tahminler/saatlik?istno=17130`

## What Production Currently Collects

For LTAC, the deployed system currently persists:

- detected METAR events
- METAR corrections
- unique AWS/surface observation updates
- temperature peak tracking inputs
- forecast revision history
- METAR capture timing records

For a detailed LTAC field-level breakdown, see:

- [LTAC_DATA_REPORT.md](LTAC_DATA_REPORT.md)

## Multi-Airport Direction

The database schema is already designed to support more than one airport.

What is already ready:

- `airports` table
- `airport_sources` table
- per-airport fact tables keyed by `airport_icao`

What is **not** fully generalized yet:

- polling config is still LTAC-specific
- source selection is still LTAC-specific
- scheduler assumptions are still LTAC-specific
- web UI is still effectively LTAC-first

So the database supports multiple airports now, but the application logic is still in phase 1:

- one airport deployed
- more airports planned

## Planned Next Step For More Airports

To add more airports cleanly, phase 2 needs:

- airport/source config instead of LTAC hard-coding
- one runtime per airport
- airport-aware API routes
- airport-aware dashboard views

The intended direction is:

- one shared schema
- many airports
- per-airport source mapping
- no separate schema per airport

## Related Docs

- LTAC data summary: [LTAC_DATA_REPORT.md](LTAC_DATA_REPORT.md)
- SQLite/VPS migration plan: [sqlite_vps_migration_plan.md](sqlite_vps_migration_plan.md)
- deployment and redeploy notes: [deploy/README.md](deploy/README.md)
