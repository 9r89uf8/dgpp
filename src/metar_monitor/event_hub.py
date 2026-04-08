"""Queue-based fan-out event hub with lossless/lossy delivery."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import EventType, MetarEvent, PollStats
from .temp_tracker import TempEvent, TempEventType

UTC = timezone.utc
log = logging.getLogger(__name__)

# Kinds that are lossless (never dropped)
_LOSSLESS_KINDS = frozenset({"metar", "temp_state"})

# How long a subscriber can be saturated before auto-disconnect
_ZOMBIE_TIMEOUT_S = 60.0


@dataclass(slots=True)
class HubMessage:
    kind: str  # "metar", "aws_update", "temp_state", "temp_forecast", "stats", "error"
    seq: int
    created_at: datetime
    payload: dict
    lossless: bool


class Subscription:
    """Per-subscriber message channel with lossless overflow."""

    def __init__(self, hub: EventHub, maxsize: int = 256) -> None:
        self._hub = hub
        self.main: deque[HubMessage] = deque(maxlen=maxsize)
        self.overflow: deque[HubMessage] = deque()  # lossless only, unbounded but rare
        self.event: asyncio.Event = asyncio.Event()
        self.closed: bool = False
        self._maxsize = maxsize
        self._saturated_since: float | None = None

    async def get(self) -> HubMessage:
        """Get next message. Drains overflow first, then main."""
        while not self.closed:
            if self.overflow:
                msg = self.overflow.popleft()
                self._clear_saturation()
                return msg
            if self.main:
                msg = self.main.popleft()
                self._clear_saturation()
                return msg
            self.event.clear()
            await self.event.wait()
        raise ConnectionError("Subscription closed")

    def get_nowait(self) -> HubMessage | None:
        """Non-blocking get. Returns None if empty."""
        if self.overflow:
            self._clear_saturation()
            return self.overflow.popleft()
        if self.main:
            self._clear_saturation()
            return self.main.popleft()
        return None

    def drain(self) -> list[HubMessage]:
        """Drain all pending messages. Returns list."""
        msgs: list[HubMessage] = []
        while self.overflow:
            msgs.append(self.overflow.popleft())
        while self.main:
            msgs.append(self.main.popleft())
        if msgs:
            self._clear_saturation()
        return msgs

    def unsubscribe(self) -> None:
        self.closed = True
        self.event.set()  # wake any waiting get()
        self._hub._remove(self)

    def _clear_saturation(self) -> None:
        self._saturated_since = None

    @property
    def is_saturated(self) -> bool:
        return len(self.main) >= self._maxsize

    def _check_zombie(self) -> bool:
        """Returns True if this subscriber should be auto-disconnected."""
        if not self.is_saturated:
            self._saturated_since = None
            return False
        now = time.monotonic()
        if self._saturated_since is None:
            self._saturated_since = now
            return False
        return (now - self._saturated_since) >= _ZOMBIE_TIMEOUT_S


class EventHub:
    """Fan-out event bus. publish() is always non-blocking."""

    def __init__(self) -> None:
        self._subscribers: list[Subscription] = []
        self._seq: int = 0

    def subscribe(self, maxsize: int = 256) -> Subscription:
        sub = Subscription(self, maxsize=maxsize)
        self._subscribers.append(sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        try:
            self._subscribers.remove(sub)
        except ValueError:
            pass

    def publish(self, msg: HubMessage) -> None:
        """Publish to all subscribers. Never blocks."""
        zombies: list[Subscription] = []

        for sub in self._subscribers:
            if sub.closed:
                continue

            if msg.lossless:
                sub.overflow.append(msg)
                sub.event.set()
            else:
                if sub.is_saturated:
                    # Evict oldest of same kind
                    evicted = False
                    for i, existing in enumerate(sub.main):
                        if existing.kind == msg.kind and not existing.lossless:
                            del sub.main[i]  # type: ignore[arg-type]
                            evicted = True
                            break
                    if not evicted:
                        # Can't evict — drop this new message
                        continue
                sub.main.append(msg)
                sub.event.set()

            # Check zombie
            if sub._check_zombie():
                zombies.append(sub)

        for zombie in zombies:
            log.warning("Auto-disconnecting zombie subscriber (saturated >%ds)", _ZOMBIE_TIMEOUT_S)
            zombie.unsubscribe()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def publish_event(self, event: MetarEvent, stats: PollStats) -> None:
        """Publish a monitor event. METAR new/correction are lossless."""
        now = datetime.now(UTC)

        if event.event_type in (EventType.NEW_METAR, EventType.CORRECTION):
            self.publish(HubMessage(
                kind="metar",
                seq=self._next_seq(),
                created_at=now,
                payload={
                    "event_type": event.event_type.value,
                    "metar_raw": event.metar_raw,
                    "ddhhmmz": event.ddhhmmz,
                    "detected_at": event.detected_at.isoformat(),
                },
                lossless=True,
            ))
        elif event.event_type == EventType.AWS_UPDATE:
            obs = event.observation
            self.publish(HubMessage(
                kind="aws_update",
                seq=self._next_seq(),
                created_at=now,
                payload={
                    "detected_at": event.detected_at.isoformat(),
                    "veri_zamani": obs.veri_zamani if obs else "",
                    "sicaklik": obs.sicaklik if obs else None,
                    "nem": obs.nem if obs else None,
                    "ruzgar_hiz": obs.ruzgar_hiz if obs else None,
                    "ruzgar_yon": obs.ruzgar_yon if obs else None,
                    "denize_indirgenmis_basinc": obs.denize_indirgenmis_basinc if obs else None,
                    "gorus": obs.gorus if obs else None,
                    "kapalilik": obs.kapalilik if obs else None,
                },
                lossless=False,
            ))
        elif event.event_type == EventType.FETCH_ERROR:
            self.publish(HubMessage(
                kind="error",
                seq=self._next_seq(),
                created_at=now,
                payload={"error": event.error},
                lossless=False,
            ))

    def publish_temp(self, event: TempEvent) -> None:
        """Publish a temp tracker event. State transitions are lossless."""
        now = datetime.now(UTC)

        is_state_change = event.event_type in (
            TempEventType.TEMP_NEAR_PEAK,
            TempEventType.TEMP_PROVISIONAL_PEAK,
            TempEventType.TEMP_CONFIRMED_PEAK,
            TempEventType.TEMP_PEAK_REVOKED,
            TempEventType.TEMP_DAY_RESET,
        )

        kind = "temp_state" if is_state_change else "temp_forecast"

        self.publish(HubMessage(
            kind=kind,
            seq=self._next_seq(),
            created_at=now,
                payload={
                    "event_type": event.event_type.value,
                    "state": event.state.value,
                    "observed_max_raw": event.observed_max_raw,
                    "observed_max_time": event.observed_max_time.isoformat() if event.observed_max_time else None,
                    "current_temp_raw": event.current_temp_raw,
                    "trend_10m": event.trend_10m,
                    "trend_30m": event.trend_30m,
                    "trend_60m": event.trend_60m,
                    "remaining_gain_c": event.remaining_gain_c,
                    "final_max_estimate_c": event.final_max_estimate_c,
                    "p_reached_max": event.p_reached_max,
                    "p_going_down": event.p_going_down,
                    "p_above_forecast": event.p_above_forecast,
                    "p_below_forecast": event.p_below_forecast,
                    "down_state": event.down_state,
                    "forecast_state": event.forecast_state,
                },
                lossless=is_state_change,
            ))
