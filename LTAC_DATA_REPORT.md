# LTAC Data Collection Report

This report describes what the current system collects for **LTAC (Ankara Esenboga)**, where that data comes from, what is monitored, and how often the monitor polls.

## Airport Identity

- ICAO: `LTAC`
- Name: `Ankara Esenboga`
- Timezone: `Europe/Istanbul`
- Coordinates: `40.115921, 32.986827`
- Elevation: `959 m`

## Data Sources

### 1. Live LTAC Observation + Raw METAR

Primary source:

- `https://servis.mgm.gov.tr/web/sondurumlar?istno=17128`

Purpose:

- current LTAC surface observation
- current raw METAR string

Source mapping:

- MGM observation station id: `17128`

### 2. LTAC Airport Daily Forecast

Source:

- `https://servis.mgm.gov.tr/web/tahminler/gunluk?istno=90615`

Purpose:

- LTAC daily forecast maximum temperature

Source mapping:

- MGM daily forecast id: `90615`

### 3. Ankara Center 3-Hour Forecast Shape

Source:

- `https://servis.mgm.gov.tr/web/tahminler/saatlik?istno=17130`

Purpose:

- Ankara center 3-hour forecast temperature steps
- used as forecast shape guidance, not airport truth

Source mapping:

- MGM center forecast id: `17130`

## Request Headers

All MGM requests use:

- `Origin: https://www.mgm.gov.tr`
- `Referer: https://www.mgm.gov.tr/`
- `Accept: application/json`

## What We Collect

### Live Observation Fields

From `sondurumlar` we collect:

- `veriZamani`
- `rasatMetar`
- `sicaklik`
- `hissedilenSicaklik`
- `nem`
- `ruzgarHiz`
- `ruzgarYon`
- `aktuelBasinc`
- `denizeIndirgenmisBasinc`
- `gorus`
- `kapalilik`
- `hadiseKodu`
- `yagis24Saat`

### METAR Monitoring

We track:

- new METARs
- METAR corrections
- repeated same METARs
- METAR unavailable states
- fetch errors

Detection rules:

- `NEW_METAR`: METAR text changed and `DDHHMMZ` changed
- `CORRECTION`: METAR text changed but `DDHHMMZ` stayed the same
- `AWS_UPDATE`: `veriZamani` changed while METAR text did not
- `UNAVAILABLE`: `rasatMetar == "-9999"`

### AWS / Surface Observation Monitoring

We persist each unique surface observation update and track movement in:

- temperature
- humidity
- wind speed
- wind direction
- sea-level pressure
- station pressure
- visibility
- cloud cover
- weather code

### Temperature Peak Tracking

We track:

- current temperature
- observed daily max
- temperature trend
- time since max
- forecast max gap

Peak states:

- `RISING`
- `FLAT`
- `NEAR_PEAK`
- `PROVISIONAL_PEAK`
- `CONFIRMED_PEAK`
- `STALE`

### Forecast Monitoring

We collect and store:

- LTAC daily max forecast
- Ankara center 3-hour temperature shape
- forecast revision history over time

### Capture Timing

For each alerting METAR event we store:

- `ddhhmmz`
- detection time
- computed delay from bulletin time
- source
- event type

## Polling Cadence

### Live LTAC Polling

The monitor polls the live LTAC observation endpoint using three modes:

- `AGGRESSIVE`: `1.5s`
- `ACTIVE`: `5.0s`
- `IDLE`: `15.0s`

### Aggressive METAR Windows

Aggressive METAR polling is active only during:

- `:20:00` through `:26:59` UTC
- `:50:00` through `:56:59` UTC

### Known Publish Schedule Used By The Monitor

METAR publish minutes:

- `:20`
- `:50`

AWS publish minutes:

- `:06`
- `:15`
- `:27`
- `:39`
- `:48`
- `:54`

### Non-Hot-Window Behavior

Outside aggressive METAR windows:

- `ACTIVE (5s)` when the next scheduled publish is within `5 minutes`
- `ACTIVE (5s)` for `2 minutes` after a real new/correction METAR
- `IDLE (15s)` otherwise

### Forecast Polling

Forecast products are refreshed every:

- `5 minutes`

## Timeouts

Live LTAC poll timeouts:

- connect timeout: `0.6s`
- read timeout: `0.6s`

Forecast fetches use a longer timeout path because they are not latency-critical.

## Storage

The current deployed system stores LTAC data in SQLite.

Primary tables:

- `airports`
- `airport_sources`
- `metar_events`
- `surface_observations`
- `forecast_fetches`
- `capture_log`

Stored facts include:

- each detected METAR event
- each unique AWS observation update
- each forecast fetch snapshot
- each METAR capture timing record

## Operational Notes

- Terminal is still the fastest view.
- Web now updates METAR, AWS, and temperature events immediately over WebSocket.
- The VPS runs the monitor continuously, so LTAC history keeps growing until you stop the service.
- The Ankara 3-hour forecast is not airport truth; it is guidance for forecast shape only.
