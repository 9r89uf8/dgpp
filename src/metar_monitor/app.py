"""Textual application for the METAR monitor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static

from .client import MGMClient
from .config import AIRPORT_ICAO, AIRPORT_TIMEZONE
from .db import Database
from .detector import MetarDetector, normalize_metar, parse_ddhhmmz
from .models import EventType, MetarEvent, PollStats
from .monitor import Monitor
from .schedule import Scheduler
from .state import MonitorState
from .temp_tracker import TempEvent, TempEventType
from .widgets import AnkaraClock, MetarDisplay, ObservationPanel, StatsPanel, HistoryLog, AwsHistoryLog, TempPanel

UTC = timezone.utc
ISTANBUL = ZoneInfo("Europe/Istanbul")

CSS = """
Screen {
    layout: vertical;
}

#ankara-clock {
    height: 1;
    text-style: bold;
    color: $text;
}

#metar-display {
    height: auto;
    min-height: 5;
    border: solid green;
    padding: 0 1;
}

#metar-display.-new-metar {
    background: $success 30%;
}

#metar-display.-correction {
    background: $warning 30%;
}

#obs-panel {
    height: auto;
    min-height: 4;
    border: solid $primary;
    padding: 0 1;
}

#temp-panel {
    height: auto;
    min-height: 4;
    border: solid $warning;
    padding: 0 1;
}

#stats-panel {
    height: auto;
    min-height: 5;
    border: solid $secondary;
    padding: 0 1;
}

#history-panel {
    height: auto;
    border: solid $surface;
    padding: 0 1;
}

#aws-panel {
    height: auto;
    border: solid $accent;
    padding: 0 1;
}

.panel-title {
    dock: top;
    padding: 0 1;
    text-style: bold;
}
"""


class MetarMonitorApp(App):
    """LTAC METAR Speed Monitor."""

    CSS = CSS
    TITLE = "LTAC METAR Monitor"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("f", "force_poll", "Force Poll"),
        Binding("m", "toggle_mute", "Mute"),
    ]

    def __init__(
        self,
        client: MGMClient,
        state: MonitorState,
        db: Database | None = None,
        startup_mode: str = "adopt-current",
        base_interval: float | None = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._state = state
        self._db = db
        self._startup_mode = startup_mode
        self._muted = False

        if self._db:
            self._seed_state_from_db()

        # Build detector from persisted state
        self._detector = MetarDetector(
            last_seen_metar=state.last_seen_metar,
            last_seen_ddhhmmz=state.last_seen_ddhhmmz,
            last_seen_veri_zamani=state.last_seen_veri_zamani,
        )

        from .config import BASE_INTERVAL
        self._scheduler = Scheduler(base_interval=base_interval or BASE_INTERVAL)

        self._monitor = Monitor(
            client=self._client,
            detector=self._detector,
            state=self._state,
            scheduler=self._scheduler,
            on_event=self._on_event,
            on_temp_event=self._on_temp_event,
            db=self._db,
            muted=self._muted,
        )
        self._poll_task: asyncio.Task | None = None

    def _seed_state_from_db(self) -> None:
        latest_metar = self._db.get_latest_metar(AIRPORT_ICAO) if self._db else None
        if latest_metar:
            self._state.last_seen_metar = latest_metar.get("metar_raw")
            self._state.last_seen_ddhhmmz = latest_metar.get("ddhhmmz")
            self._state.last_seen_at = latest_metar.get("detected_at")

        latest_obs = self._db.get_latest_surface_observation(AIRPORT_ICAO) if self._db else None
        if latest_obs:
            self._state.last_seen_veri_zamani = latest_obs.get("veri_zamani")

    def _metar_history(self) -> list[dict]:
        if not self._db:
            return self._state.history
        rows = self._db.get_metar_history(
            AIRPORT_ICAO,
            event_types=("new", "correction"),
        )
        history = [
            {
                "metar": row["metar_raw"],
                "ddhhmmz": row["ddhhmmz"],
                "detected_at": row["detected_at"],
                "event_type": row["event_type"],
            }
            for row in rows
        ]
        return history or self._state.history

    def _aws_history(self) -> list[dict]:
        if not self._db:
            return self._state.aws_history
        rows = self._db.get_surface_history(AIRPORT_ICAO)
        history = [
            {
                "veri_zamani": row["veri_zamani"],
                "detected_at": row["detected_at"],
                "sicaklik": row["sicaklik"],
                "nem": row["nem"],
                "ruzgar_hiz": row["ruzgar_hiz"],
                "gorus": row["gorus"],
                "denize_indirgenmis_basinc": row["denize_indirgenmis_basinc"],
                "ruzgar_yon": row["ruzgar_yon"],
                "kapalilik": row["kapalilik"],
            }
            for row in rows
        ]
        return history or self._state.aws_history

    def _replay_temp_from_history(self) -> None:
        if self._db:
            source_entries = self._db.get_surface_observations_for_local_day(
                AIRPORT_ICAO,
                AIRPORT_TIMEZONE,
            )
        else:
            source_entries = self._state.aws_history

        if not source_entries:
            return

        now_istanbul = datetime.now(ISTANBUL).date()
        entries = []
        for entry in source_entries:
            veri = entry.get("veri_zamani", "")
            sicaklik = entry.get("sicaklik", -9999)
            if sicaklik == -9999 or not veri:
                continue
            try:
                dt = datetime.fromisoformat(str(veri).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                continue
            if dt.astimezone(ISTANBUL).date() == now_istanbul:
                entries.append((dt, veri, sicaklik))

        entries.sort(key=lambda item: item[0])
        for dt, veri, sicaklik in entries:
            self._monitor.temp_tracker.record(veri, sicaklik, now_utc=dt)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield AnkaraClock(id="ankara-clock")
            yield Static(" METAR", classes="panel-title")
            yield MetarDisplay(id="metar-display")
            yield Static(" Temperature Peak", classes="panel-title")
            yield TempPanel(id="temp-panel")
            yield Static(" Observation", classes="panel-title")
            yield ObservationPanel(id="obs-panel")
            yield Static(" Stats", classes="panel-title")
            yield StatsPanel(id="stats-panel")
            yield Static(" METAR History", classes="panel-title")
            yield HistoryLog(id="history-panel")
            yield Static(" AWS History", classes="panel-title")
            yield AwsHistoryLog(id="aws-panel")
        yield Footer()

    async def on_mount(self) -> None:
        # Warmup and seed detector
        await self._monitor.warmup(mode=self._startup_mode)
        self._replay_temp_from_history()

        # Show initial state from persistence
        history = self._metar_history()
        if history:
            self.query_one("#history-panel", HistoryLog).update_history(
                history
            )
        aws_history = self._aws_history()
        if aws_history:
            self.query_one("#aws-panel", AwsHistoryLog).update_aws_history(
                aws_history
            )

        if self._detector.current_metar:
            self.query_one("#metar-display", MetarDisplay).metar_text = (
                self._detector.current_metar
            )

        # Start polling loop
        self._poll_task = asyncio.create_task(self._monitor.run())

        # Start stats refresh timer
        self.set_interval(0.5, self._refresh_stats)

    def _on_event(self, event: MetarEvent, stats: PollStats) -> None:
        """Called by monitor on every poll — update UI."""
        # This runs in the async context, safe to update widgets
        metar_display = self.query_one("#metar-display", MetarDisplay)
        metar_display.update_metar(event)

        if event.observation:
            self.query_one("#obs-panel", ObservationPanel).update_observation(
                event.observation
            )

        if event.event_type in (EventType.NEW_METAR, EventType.CORRECTION):
            self.query_one("#history-panel", HistoryLog).update_history(
                self._metar_history()
            )
            # Clear flash after 3 seconds
            self.set_timer(3.0, lambda: self._clear_flash(metar_display))

        if event.event_type == EventType.AWS_UPDATE:
            self.query_one("#aws-panel", AwsHistoryLog).update_aws_history(
                self._aws_history()
            )

    def _clear_flash(self, widget: MetarDisplay) -> None:
        widget.remove_class("-new-metar")
        widget.remove_class("-correction")

    def _on_temp_event(self, event: TempEvent) -> None:
        """Called by monitor on temp state changes."""
        self.query_one("#temp-panel", TempPanel).update_temp(
            self._monitor.temp_tracker
        )

    def _refresh_stats(self) -> None:
        """Periodic stats refresh."""
        self.query_one("#ankara-clock", AnkaraClock).refresh_clock()
        self.query_one("#stats-panel", StatsPanel).update_stats(
            self._monitor.stats,
            self._scheduler.interval_label,
            self._scheduler.get_interval(),
        )
        # Also refresh temp panel for live countdown/trend
        self.query_one("#temp-panel", TempPanel).update_temp(
            self._monitor.temp_tracker
        )

    def action_force_poll(self) -> None:
        """Manually trigger an immediate poll."""
        asyncio.create_task(self._monitor._poll_once())

    def action_toggle_mute(self) -> None:
        self._muted = not self._muted
        self._monitor.muted = self._muted
        self.sub_title = "MUTED" if self._muted else ""

    async def action_quit(self) -> None:
        self._monitor.stop()
        if self._poll_task:
            self._poll_task.cancel()
        await self._client.close()
        self.exit()
