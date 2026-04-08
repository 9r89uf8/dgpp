# Turkey MGM API (Meteoroloji Genel Müdürlüğü)

Discovered March 22, 2026. The Turkish State Meteorological Service operates
an undocumented but fully functional REST API at `servis.mgm.gov.tr/web/`.
No API key or token is needed. In live tests on March 23, 2026, requests
worked with `Origin: https://www.mgm.gov.tr`; our code also sends `Referer`
and `Accept`, but those were not required in curl tests.

## Authentication

No token or key required. In live tests, the server required the official
browser origin:

```
Origin: https://www.mgm.gov.tr
```

Our code also sends:

```
Referer: https://www.mgm.gov.tr/
Accept: application/json
```

Requests with no `Origin`, or with a different `Origin`, currently fail with
HTTP 500 and a JSON body like `{"error":"ServerError","message":"Not allowed by MGM"}`.
`Referer` and `Accept` were optional in March 23, 2026 tests.

## Base URL

```
https://servis.mgm.gov.tr/web/
```

## Station / Location IDs

MGM uses several identifier types:

| ID Type | Example | Used For |
|---|---|---|
| `istNo` (station number) | `17128` | Esenboğa Airport station — observations, station metadata |
| `sondurumIstNo` | `17130` | Center-station current conditions |
| `merkezId` (center ID) | `90601` | Location lookup and center-based queries |
| `saatlikTahminIstNo` | `17130` | Ankara center — 3-hourly forecast |
| `gunlukTahminIstNo` | `90601` | Ankara center — 5-day daily forecast |
| `ilPlaka` (province plate) | `6` | Ankara province — bulk queries |

To find IDs for other locations, query `/web/merkezler/iller` (all provinces)
or `/web/merkezler/ililcesi?il=Ankara` (districts within a province).

### Key IDs for Ankara / Esenboğa

- Airport observations / raw METAR: `istno=17128` (Ankara Esenboğa Airport, LTAC)
- Center observations: `istno=17130` (`?il=Ankara` and `?merkezid=90601` resolve here)
- 3-hourly forecast: `istno=17130` or `merkezid=90601` (`istno=17128` returns `[]`)
- 5-day daily forecast: `merkezid=90601`, `istno=90601`, or `il=Ankara`
- Province bulk queries: `ilPlaka=6`

## Endpoints

### Current Conditions

```
GET /web/sondurumlar?istno=17128
```

Returns the latest observation for a station. Airport stations can include the
raw METAR; center-station queries often return `rasatMetar: "-9999"`.

Response (single-element array):
```json
{
  "istNo": 17128,
  "veriZamani": "2026-03-22T23:30:00.000Z",
  "sicaklik": 4.5,
  "hissedilenSicaklik": 4.5,
  "nem": 94,
  "ruzgarHiz": 4.32,
  "ruzgarYon": 270,
  "aktuelBasinc": 900,
  "denizeIndirgenmisBasinc": 1008.2,
  "gorus": 10000,
  "kapalilik": 7,
  "hadiseKodu": "CB",
  "rasatMetar": "LTAC 222350Z VRB01KT 9999 SCT040 BKN100 05/04 Q1008 NOSIG ...",
  "rasatSinoptik": "-9999",
  "rasatTaf": "-9999",
  "yagis00Now": 0,
  "yagis1Saat": 0,
  "yagis6Saat": 0,
  "yagis12Saat": 0,
  "yagis24Saat": 0,
  "yagis10Dk": -9999,
  "karYukseklik": -9999,
  "denizSicaklik": -9999,
  "denizVeriZamani": "2026-03-22T06:00:00.000Z"
}
```

Key fields:
- `sicaklik` — temperature in °C (0.1° precision from AWS, not METAR integer)
- `hissedilenSicaklik` — feels-like temperature °C
- `nem` — relative humidity %
- `ruzgarHiz` — wind speed; live values align with km/h, not m/s
- `ruzgarYon` — wind direction in degrees
- `gorus` — visibility in meters
- `denizeIndirgenmisBasinc` — sea-level pressure in hPa
- `aktuelBasinc` — station pressure in hPa
- `kapalilik` — cloud cover (oktas, 0–8)
- `hadiseKodu` — weather code (see weather codes below)
- `rasatMetar` — raw METAR string (airport stations only)
- `veriZamani` — observation timestamp (current live responses use UTC ISO strings with `Z`)
- `-9999` means "not available"

Other query forms:
- `?il=Ankara` — returns the Ankara center station (`istNo: 17130`), not the airport
- `?merkezid=90601` — same center-station response as `?il=Ankara`

### All Current Conditions for a Province

```
GET /web/sondurumlar/ilTumSondurum?ilPlaka=6
```

Returns current observations for every station in the province.

### 3-Hourly Forecast

```
GET /web/tahminler/saatlik?istno=17130
```

Returns the currently available future steps at 3-hour intervals. The live
Ankara response on March 23, 2026 returned 7 steps (~21 hours ahead), so do
not assume a fixed count. It works for center forecast stations, not airport
observation stations.

Response:
```json
{
  "baslangicZamani": "2026-03-22T12:00:00.000Z",
  "istNo": 17130,
  "merkez": "ANKARA",
  "tahmin": [
    {
      "tarih": "2026-03-23T00:00:00.000Z",
      "hadise": "CB",
      "sicaklik": 6,
      "hissedilenSicaklik": 6,
      "nem": 96,
      "ruzgarYonu": 59,
      "ruzgarHizi": 2,
      "maksimumRuzgarHizi": 6
    }
  ]
}
```

Forecast fields:
- `tarih` — forecast valid time (UTC)
- `sicaklik` — temperature °C (whole degrees)
- `hissedilenSicaklik` — feels-like °C
- `nem` — humidity %
- `ruzgarYonu` — wind direction degrees
- `ruzgarHizi` — average wind speed in km/h
- `maksimumRuzgarHizi` — max wind speed / gust in km/h
- `hadise` — weather code

Other query forms:
- `?merkezid=90601`
- `?il=Ankara`

### 5-Day Daily Forecast

```
GET /web/tahminler/gunluk?merkezid=90601
```

Returns min/max temperatures and weather for 5 forecast days.

Response fields per day (numbered 1–5):
- `enDusukGun1` through `enDusukGun5` — daily minimum °C
- `enYuksekGun1` through `enYuksekGun5` — daily maximum °C
- `hadiseGun1` through `hadiseGun5` — weather code
- `ruzgarHizGun1` through `ruzgarHizGun5` — wind speed in km/h
- `ruzgarYonGun1` through `ruzgarYonGun5` — wind direction
- `enDusukNemGun1` / `enYuksekNemGun1` through `...Gun5` — humidity ranges

Current live responses also include a duplicate `*Gun0` block for the current
day (`tarihGun0`, `hadiseGun0`, etc.). The Ankara code ignores `Gun0` and
stores only `Gun1` through `Gun5`.

Other query forms:
- `?istno=90601`
- `?il=Ankara`

### Station Metadata

```
GET /web/istasyonlar                     # All 1,930 stations
GET /web/istasyonlar?istno=17128         # Single station
GET /web/istasyonlar/il?plaka=6          # All stations in a province
```

### Location Lookup

```
GET /web/merkezler/iller                 # All 81 provinces
GET /web/merkezler/ililcesi?il=Ankara    # All districts in a province
GET /web/merkezler?il=Ankara             # Single province center
GET /web/merkezler?merkezid=90601        # By center ID
GET /web/merkezler?sorgu=Ankara          # Search by name
```

## Weather Codes

Used in `hadiseKodu` (observations) and `hadise` (forecasts):

| Code | Meaning | Icon |
|---|---|---|
| `A` | Clear (Açık) | `A.svg` |
| `AB` | Partly clear | `AB.svg` |
| `PB` | Partly cloudy (Parçalı bulutlu) | `PB.svg` |
| `CB` | Cloudy (Çok bulutlu) | `CB.svg` |
| `HY` | Light rain (Hafif yağmur) | `HY.svg` |
| `Y` | Rain (Yağmur) | `Y.svg` |
| `SY` | Shower (Sağanak yağış) | `SY.svg` |
| `HSY` | Light shower (Hafif sağanak) | `HSY.svg` |
| `KY` | Snow (Kar yağışı) | `KY.svg` |
| `KKY` | Sleet (Karla karışık yağmur) | `KKY.svg` |
| `GSY` | Thunderstorm (Gök gürültülü sağanak) | `GSY.svg` |
| `S` | Fog (Sis) | `S.svg` |
| `PUS` | Haze (Puslu) | `PUS.svg` |

Icons available at `https://www.mgm.gov.tr/Images_Sys/hadiseler/{CODE}.svg`

## Daily XML Bulletin

A public XML file with daily forecasts for all provinces (no headers needed):

```
GET https://www.mgm.gov.tr/FTPDATA/analiz/sonSOA.xml
```

Contains region-grouped city forecasts with max/min temps and Turkish-language
weather descriptions. Updated once or twice daily.

## Rate Limits

No documented rate limits. We have only used light polling against these
endpoints and there is no published quota. Keep requests modest and avoid
bursts.

## Publish Race Results (March 23, 2026)

Tested with METAR `LTAC 230020Z` (observation taken at 00:20Z):

| Source | First seen | Latency from obs |
|---|---|---|
| **MGM sondurumlar** | **00:22Z** | **~2 minutes** |
| NOAA tgftp | 00:25Z | ~5 minutes |
| AviationWeather.gov | 00:26Z | ~6 minutes |

MGM is the fastest source for LTAC METARs, beating NOAA by ~3 minutes.
AviationWeather.gov is a downstream of NOAA, about 1 minute slower.

## `sondurumlar` Update Frequency

`sondurumlar` is clearly more than a pure METAR mirror: `sicaklik` carries
0.1°C precision and `veriZamani` can move independently of `rasatMetar`.
Treat it as a near-live AWS/current-conditions feed, not just METAR
repackaging.

Also, do not assume `sicaklik` belongs to the same observation minute as the
raw `rasatMetar`. In live LTAC responses, we observed a newer `rasatMetar`
(`230220Z`) while `veriZamani` and `sicaklik` were still at `02:06Z`.

The exact cadence is not fixed in the API. On March 23, 2026 we observed:

```
LTAC airport query (`istno=17128`): veriZamani=2026-03-23T01:50:00.000Z
Ankara center query (`il=Ankara`): veriZamani=2026-03-23T02:06:00.000Z
```

That supports sub-hourly updates, but not a guaranteed exact `:00/:10/:20/...`
schedule. LTAC `rasatMetar` currently includes the full runway-wind `RMK`
text.

## Known Limitations

- Hourly (3-hourly) forecast only available for city center stations, not
  airport stations. For Ankara, use `istno=17130` instead of `17128`.
- Forecast temperatures are whole degrees only.
- The API is undocumented and could change without notice.
- `-9999` is the sentinel value for unavailable/missing data.
- `denizSicaklik` (sea temperature) and `karYukseklik` (snow depth) are
  typically `-9999` for inland stations.
- `sondurumlar` looks sub-hourly, but the exact publish cadence is still not
  documented and should not be treated as a fixed clock schedule.

## Convex Environment Variables

No environment variables needed. `convex/ankara.js` sets the official `Origin`
header and also sends `Referer` and `Accept`.

## Useful Links

- MGM website: https://www.mgm.gov.tr
- MGM Ankara forecast page: https://www.mgm.gov.tr/tahmin/il-ve-ilceler.aspx?m=ANKARA
- Hezarfen aviation portal: https://hezarfen.mgm.gov.tr
- Observation viewer: https://rasat.mgm.gov.tr
- Turkish AIP MET section: https://www.dhmi.gov.tr/AIPDocuments/LT_GEN_3_5_en.pdf
