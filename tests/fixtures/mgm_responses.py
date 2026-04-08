"""Canned MGM API responses for testing."""

NORMAL_RESPONSE = [
    {
        "istNo": 17128,
        "veriZamani": "2026-03-23T01:50:00.000Z",
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
        "rasatMetar": "LTAC 230150Z VRB01KT 9999 SCT040 BKN100 05/04 Q1008 NOSIG",
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
        "denizVeriZamani": "2026-03-22T06:00:00.000Z",
    }
]

# Same observation time, different body (correction)
CORRECTION_RESPONSE = [
    {
        **NORMAL_RESPONSE[0],
        "rasatMetar": "LTAC 230150Z COR VRB01KT 9999 FEW040 BKN100 05/04 Q1008 NOSIG",
    }
]

# New METAR at a different time
NEW_METAR_RESPONSE = [
    {
        **NORMAL_RESPONSE[0],
        "veriZamani": "2026-03-23T02:20:00.000Z",
        "rasatMetar": "LTAC 230220Z 18005KT 9999 FEW035 08/05 Q1009 NOSIG",
        "sicaklik": 8.2,
    }
]

# METAR unavailable
UNAVAILABLE_RESPONSE = [
    {
        **NORMAL_RESPONSE[0],
        "rasatMetar": "-9999",
    }
]

# Month rollover: DD=31 but we're on the 1st of next month
MONTH_ROLLOVER_RESPONSE = [
    {
        **NORMAL_RESPONSE[0],
        "rasatMetar": "LTAC 312350Z 18005KT 9999 FEW035 08/05 Q1009 NOSIG",
    }
]

# Empty array
EMPTY_RESPONSE: list = []

# Malformed JSON (as string, for client testing)
MALFORMED_JSON = b'{"broken": true'

# Duplicate stale bulletin (same as NORMAL but with extra whitespace)
STALE_WHITESPACE_RESPONSE = [
    {
        **NORMAL_RESPONSE[0],
        "rasatMetar": "LTAC  230150Z  VRB01KT  9999  SCT040  BKN100  05/04  Q1008  NOSIG",
    }
]
