"""Tests for the event hub."""

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from metar_monitor.event_hub import EventHub, HubMessage, Subscription, _ZOMBIE_TIMEOUT_S

UTC = timezone.utc


def _msg(kind: str = "stats", lossless: bool = False, seq: int = 0) -> HubMessage:
    return HubMessage(
        kind=kind,
        seq=seq,
        created_at=datetime.now(UTC),
        payload={"test": True},
        lossless=lossless,
    )


class TestFanOut:
    def test_message_reaches_all_subscribers(self):
        hub = EventHub()
        sub1 = hub.subscribe()
        sub2 = hub.subscribe()
        hub.publish(_msg())
        assert len(sub1.main) == 1
        assert len(sub2.main) == 1

    def test_ordering_preserved(self):
        hub = EventHub()
        sub = hub.subscribe()
        for i in range(5):
            hub.publish(_msg(seq=i))
        msgs = sub.drain()
        assert [m.seq for m in msgs] == [0, 1, 2, 3, 4]


class TestLossless:
    def test_lossless_goes_to_overflow(self):
        hub = EventHub()
        sub = hub.subscribe(maxsize=2)
        hub.publish(_msg(kind="metar", lossless=True))
        assert len(sub.overflow) == 1
        assert len(sub.main) == 0

    def test_lossless_survives_full_queue(self):
        hub = EventHub()
        sub = hub.subscribe(maxsize=2)
        # Fill main queue
        hub.publish(_msg(kind="stats", lossless=False))
        hub.publish(_msg(kind="stats", lossless=False))
        assert sub.is_saturated
        # Lossless still delivered via overflow
        hub.publish(_msg(kind="metar", lossless=True, seq=99))
        assert len(sub.overflow) == 1
        assert sub.overflow[0].seq == 99

    def test_get_drains_overflow_first(self):
        hub = EventHub()
        sub = hub.subscribe()
        hub.publish(_msg(kind="stats", lossless=False, seq=1))
        hub.publish(_msg(kind="metar", lossless=True, seq=2))
        # overflow has seq=2, main has seq=1
        msg = sub.get_nowait()
        assert msg is not None
        assert msg.seq == 2  # overflow first
        msg = sub.get_nowait()
        assert msg is not None
        assert msg.seq == 1  # then main


class TestLossyEviction:
    def test_same_kind_evicted_when_full(self):
        hub = EventHub()
        sub = hub.subscribe(maxsize=3)
        hub.publish(_msg(kind="stats", seq=1))
        hub.publish(_msg(kind="aws_update", seq=2))
        hub.publish(_msg(kind="stats", seq=3))
        assert sub.is_saturated
        # New stats should evict oldest stats (seq=1)
        hub.publish(_msg(kind="stats", seq=4))
        msgs = sub.drain()
        seqs = [m.seq for m in msgs]
        assert 1 not in seqs  # evicted
        assert 4 in seqs  # new one added

    def test_no_same_kind_drops_new(self):
        hub = EventHub()
        sub = hub.subscribe(maxsize=2)
        hub.publish(_msg(kind="aws_update", seq=1))
        hub.publish(_msg(kind="stats", seq=2))
        assert sub.is_saturated
        # New error — no existing error to evict, dropped
        hub.publish(_msg(kind="error", seq=3))
        msgs = sub.drain()
        assert len(msgs) == 2
        assert all(m.seq != 3 for m in msgs)

    def test_lossless_never_evicted(self):
        hub = EventHub()
        sub = hub.subscribe(maxsize=3)
        hub.publish(_msg(kind="metar", lossless=True, seq=1))  # goes to overflow
        hub.publish(_msg(kind="stats", seq=2))
        hub.publish(_msg(kind="stats", seq=3))
        hub.publish(_msg(kind="stats", seq=4))
        # Main is full with stats, overflow has metar
        # Metar in overflow is untouched
        assert len(sub.overflow) == 1
        assert sub.overflow[0].seq == 1


class TestUnsubscribe:
    def test_unsubscribe_removes_from_hub(self):
        hub = EventHub()
        sub = hub.subscribe()
        assert len(hub._subscribers) == 1
        sub.unsubscribe()
        assert len(hub._subscribers) == 0

    def test_unsubscribe_closes(self):
        hub = EventHub()
        sub = hub.subscribe()
        sub.unsubscribe()
        assert sub.closed

    def test_publish_skips_closed(self):
        hub = EventHub()
        sub = hub.subscribe()
        sub.closed = True
        hub.publish(_msg())
        assert len(sub.main) == 0


class TestZombieDisconnect:
    def test_zombie_auto_disconnected(self):
        hub = EventHub()
        sub = hub.subscribe(maxsize=2)
        # Fill queue
        hub.publish(_msg(seq=1))
        hub.publish(_msg(seq=2))
        assert sub.is_saturated

        # Simulate time passing > 60s
        sub._saturated_since = time.monotonic() - (_ZOMBIE_TIMEOUT_S + 1)

        # Next publish triggers zombie check
        hub.publish(_msg(seq=3))
        assert sub.closed
        assert len(hub._subscribers) == 0

    def test_not_zombie_if_drained(self):
        hub = EventHub()
        sub = hub.subscribe(maxsize=2)
        hub.publish(_msg())
        hub.publish(_msg())
        sub._saturated_since = time.monotonic() - 30  # 30s < 60s threshold
        sub.drain()
        # After drain, saturation cleared
        assert sub._saturated_since is None


class TestAsyncGet:
    @pytest.mark.asyncio
    async def test_get_waits_for_message(self):
        hub = EventHub()
        sub = hub.subscribe()

        async def publish_later():
            await asyncio.sleep(0.05)
            hub.publish(_msg(seq=42))

        task = asyncio.create_task(publish_later())
        msg = await asyncio.wait_for(sub.get(), timeout=1.0)
        assert msg.seq == 42
        await task

    @pytest.mark.asyncio
    async def test_get_raises_on_closed(self):
        hub = EventHub()
        sub = hub.subscribe()

        async def close_later():
            await asyncio.sleep(0.05)
            sub.unsubscribe()

        task = asyncio.create_task(close_later())
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(sub.get(), timeout=1.0)
        await task
