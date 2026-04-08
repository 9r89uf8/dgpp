"""Data models for the METAR monitor."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import deque

UTC = timezone.utc


class EventType(enum.Enum):
    NEW_METAR = "new"
    CORRECTION = "correction"
    SAME = "same"
    UNAVAILABLE = "unavailable"
    FETCH_ERROR = "fetch_error"
    AWS_UPDATE = "aws_update"  # veriZamani changed but rasatMetar didn't


@dataclass(frozen=True, slots=True)
class Observation:
    """Parsed MGM sondurumlar response for one station."""

    ist_no: int
    veri_zamani: str  # ISO timestamp string from API
    sicaklik: float
    hissedilen_sicaklik: float
    nem: int
    ruzgar_hiz: float
    ruzgar_yon: int
    aktuel_basinc: float
    denize_indirgenmis_basinc: float
    gorus: int
    kapalilik: int
    hadise_kodu: str
    rasat_metar: str  # raw METAR string or "-9999"
    yagis_24_saat: float

    @staticmethod
    def from_dict(d: dict) -> Observation:
        return Observation(
            ist_no=d.get("istNo", 0),
            veri_zamani=d.get("veriZamani", ""),
            sicaklik=d.get("sicaklik", -9999),
            hissedilen_sicaklik=d.get("hissedilenSicaklik", -9999),
            nem=d.get("nem", -9999),
            ruzgar_hiz=d.get("ruzgarHiz", -9999),
            ruzgar_yon=d.get("ruzgarYon", -9999),
            aktuel_basinc=d.get("aktuelBasinc", -9999),
            denize_indirgenmis_basinc=d.get("denizeIndirgenmisBasinc", -9999),
            gorus=d.get("gorus", -9999),
            kapalilik=d.get("kapalilik", -9999),
            hadise_kodu=d.get("hadiseKodu", ""),
            rasat_metar=d.get("rasatMetar", "-9999"),
            yagis_24_saat=d.get("yagis24Saat", -9999),
        )


@dataclass
class MetarEvent:
    """Result of a single poll cycle."""

    event_type: EventType
    observation: Observation | None = None
    metar_raw: str = ""
    ddhhmmz: str = ""
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class CaptureRecord:
    """Per-publish record for learning LTAC burst windows."""

    ddhhmmz: str
    detection_utc: str  # ISO string
    delay_from_bulletin_s: float | None  # None if we can't compute it
    source: str  # "mgm" or "noaa"
    event_type: str  # "new" or "correction"

    def to_dict(self) -> dict:
        return {
            "ddhhmmz": self.ddhhmmz,
            "detection_utc": self.detection_utc,
            "delay_from_bulletin_s": self.delay_from_bulletin_s,
            "source": self.source,
            "event_type": self.event_type,
        }

    @staticmethod
    def from_dict(d: dict) -> CaptureRecord:
        return CaptureRecord(
            ddhhmmz=d["ddhhmmz"],
            detection_utc=d["detection_utc"],
            delay_from_bulletin_s=d.get("delay_from_bulletin_s"),
            source=d.get("source", "mgm"),
            event_type=d.get("event_type", "new"),
        )


@dataclass
class PollStats:
    """Rolling statistics for the polling engine."""

    total_polls: int = 0
    successful_polls: int = 0
    failed_polls: int = 0
    last_poll_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    latency_history: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    metars_detected: int = 0
    last_metar_detected_at: datetime | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def record_success(self, latency_ms: float) -> None:
        self.total_polls += 1
        self.successful_polls += 1
        self.last_poll_latency_ms = latency_ms
        self.latency_history.append(latency_ms)
        if latency_ms < self.min_latency_ms:
            self.min_latency_ms = latency_ms
        if latency_ms > self.max_latency_ms:
            self.max_latency_ms = latency_ms
        if self.latency_history:
            self.avg_latency_ms = sum(self.latency_history) / len(self.latency_history)

    def record_failure(self) -> None:
        self.total_polls += 1
        self.failed_polls += 1

    def record_new_metar(self) -> None:
        self.metars_detected += 1
        self.last_metar_detected_at = datetime.now(UTC)

    @property
    def success_rate(self) -> float:
        if self.total_polls == 0:
            return 0.0
        return self.successful_polls / self.total_polls

    @property
    def uptime_s(self) -> float:
        return (datetime.now(UTC) - self.started_at).total_seconds()
