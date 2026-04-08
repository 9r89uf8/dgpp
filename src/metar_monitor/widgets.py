"""Textual widgets for the METAR monitor UI."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from .models import EventType, MetarEvent, Observation, PollStats
from .temp_tracker import TempTracker, TempEvent, TempEventType, TempState

UTC = timezone.utc
ISTANBUL = ZoneInfo("Europe/Istanbul")


def _to_local(utc_dt: datetime) -> datetime:
    """Convert UTC datetime to Istanbul local."""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=UTC)
    return utc_dt.astimezone(ISTANBUL)


class AnkaraClock(Static):
    """Live clock showing current Ankara date and time."""

    clock_text: reactive[str] = reactive("")

    def render(self) -> str:
        return self.clock_text

    def refresh_clock(self) -> None:
        now = datetime.now(ISTANBUL)
        self.clock_text = f"  {now.strftime('%A, %B %d, %Y  %I:%M:%S %p')}  Ankara"

# Weather code translations
WEATHER_CODES = {
    "A": "Clear",
    "AB": "Partly Clear",
    "PB": "Partly Cloudy",
    "CB": "Cloudy",
    "HY": "Light Rain",
    "Y": "Rain",
    "SY": "Shower",
    "HSY": "Light Shower",
    "KY": "Snow",
    "KKY": "Sleet",
    "GSY": "Thunderstorm",
    "S": "Fog",
    "PUS": "Haze",
}


def _fmt_val(val: float | int, unit: str = "", precision: int = 1) -> str:
    if val == -9999:
        return "N/A"
    if isinstance(val, float):
        return f"{val:.{precision}f}{unit}"
    return f"{val}{unit}"


import re

_TEMP_RE = re.compile(r"\b(M?\d{2})/(M?\d{2})\b")


def parse_metar_temp(metar: str) -> str | None:
    """Extract the temperature from the TT/DD group in a METAR string.

    Returns a string like '15°C' or '-2°C'. None if not found.
    """
    m = _TEMP_RE.search(metar)
    if not m:
        return None
    raw = m.group(1)
    if raw.startswith("M"):
        return f"-{raw[1:].lstrip('0') or '0'}°C"
    return f"{int(raw)}°C"


class MetarDisplay(Static):
    """Shows the current raw METAR string with detection timestamp and big temp."""

    metar_text: reactive[str] = reactive("Waiting for first METAR...")
    detected_at: reactive[str] = reactive("")
    event_label: reactive[str] = reactive("")
    metar_temp: reactive[str] = reactive("")
    error_text: reactive[str] = reactive("")

    def render(self) -> str:
        parts = []
        if self.event_label:
            parts.append(f"  {self.event_label}")
        if self.detected_at:
            parts.append(f"  Detected: {self.detected_at}")
        if self.metar_temp:
            parts.append(f"  [bold]{self.metar_temp}[/]")
        if self.error_text:
            parts.append(f"  [bold red]ERROR:[/] {self.error_text}")
        parts.append("")
        parts.append(f"  {self.metar_text}")
        parts.append("")
        return "\n".join(parts)

    def update_metar(self, event: MetarEvent) -> None:
        if event.event_type == EventType.UNAVAILABLE:
            self.metar_text = "(METAR unavailable from MGM)"
            self.event_label = ""
            self.metar_temp = ""
            self.error_text = ""
            return

        if event.event_type == EventType.FETCH_ERROR:
            self.error_text = event.error or "unknown error"
            return

        # Successful poll — clear any previous error
        self.error_text = ""

        if event.event_type == EventType.SAME:
            return

        self.metar_text = event.metar_raw
        local_dt = _to_local(event.detected_at)
        ts = local_dt.strftime("%I:%M:%S.") + f"{local_dt.microsecond // 1000:03d} {local_dt.strftime('%p')}"
        self.detected_at = ts
        self.metar_temp = parse_metar_temp(event.metar_raw) or ""

        if event.event_type == EventType.NEW_METAR:
            self.event_label = "[bold white on green] NEW METAR [/]"
            self.remove_class("-correction")
            self.add_class("-new-metar")
        elif event.event_type == EventType.CORRECTION:
            self.event_label = "[bold white on yellow] CORRECTION [/]"
            self.remove_class("-new-metar")
            self.add_class("-correction")


class ObservationPanel(Static):
    """Shows parsed observation data from MGM JSON."""

    obs_text: reactive[str] = reactive("  Waiting for data...")

    def render(self) -> str:
        return self.obs_text

    def update_observation(self, obs: Observation | None) -> None:
        if obs is None:
            return
        weather = WEATHER_CODES.get(obs.hadise_kodu, obs.hadise_kodu)
        lines = [
            f"  Temp: {_fmt_val(obs.sicaklik, '°C')}  "
            f"Feels: {_fmt_val(obs.hissedilen_sicaklik, '°C')}  "
            f"Humidity: {_fmt_val(obs.nem, '%', 0)}",
            f"  Wind: {_fmt_val(obs.ruzgar_hiz, ' km/h')} @ {_fmt_val(obs.ruzgar_yon, '°', 0)}  "
            f"Visibility: {_fmt_val(obs.gorus, 'm', 0)}",
            f"  Pressure: {_fmt_val(obs.denize_indirgenmis_basinc, ' hPa')}  "
            f"Station: {_fmt_val(obs.aktuel_basinc, ' hPa')}  "
            f"Cloud: {_fmt_val(obs.kapalilik, '/8', 0)}",
            f"  Weather: {weather}  "
            f"Data time: {obs.veri_zamani}",
        ]
        self.obs_text = "\n".join(lines)


# Known publish schedule (minute past the hour)
METAR_PUBLISH_MINUTES = [20, 50]
AWS_PUBLISH_MINUTES = [6, 15, 27, 39, 48, 54]
ALL_PUBLISH_MINUTES = sorted(set(METAR_PUBLISH_MINUTES + AWS_PUBLISH_MINUTES))


def _next_publish(now_minute: int, now_second: int, schedule: list[int]) -> tuple[int, int]:
    """Return (next_minute, seconds_until) for the next publish in the schedule."""
    now_total = now_minute * 60 + now_second
    for m in schedule:
        target = m * 60
        if target > now_total:
            return m, target - now_total
    # Wrap to first entry next hour
    return schedule[0], (schedule[0] * 60 + 3600) - now_total


def _format_countdown(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


class StatsPanel(Static):
    """Shows polling statistics and next publish schedule."""

    stats_text: reactive[str] = reactive("  Polling not started")

    def render(self) -> str:
        return self.stats_text

    def update_stats(self, stats: PollStats, interval_label: str, interval: float) -> None:
        rate = f"{stats.success_rate * 100:.1f}%" if stats.total_polls > 0 else "N/A"
        min_lat = f"{stats.min_latency_ms:.0f}" if stats.min_latency_ms != float("inf") else "N/A"

        uptime_s = stats.uptime_s
        hours = int(uptime_s // 3600)
        minutes = int((uptime_s % 3600) // 60)

        since_metar = ""
        if stats.last_metar_detected_at:
            delta = (datetime.now(UTC) - stats.last_metar_detected_at).total_seconds()
            if delta < 60:
                since_metar = f"{delta:.0f}s ago"
            elif delta < 3600:
                since_metar = f"{delta / 60:.0f}m ago"
            else:
                since_metar = f"{delta / 3600:.1f}h ago"
        else:
            since_metar = "none yet"

        # Next publish countdown
        now = datetime.now(UTC)
        now_min = now.minute
        now_sec = now.second

        next_metar_min, metar_secs = _next_publish(now_min, now_sec, METAR_PUBLISH_MINUTES)
        next_aws_min, aws_secs = _next_publish(now_min, now_sec, AWS_PUBLISH_MINUTES)

        lines = [
            f"  Polling: {interval:.1f}s ({interval_label})  "
            f"Latency: {stats.last_poll_latency_ms:.0f}ms  "
            f"Avg: {stats.avg_latency_ms:.0f}ms  "
            f"Min: {min_lat}ms  Max: {stats.max_latency_ms:.0f}ms",
            f"  Polls: {stats.total_polls}  "
            f"Success: {rate}  "
            f"METARs: {stats.metars_detected}  "
            f"Last METAR: {since_metar}  "
            f"Uptime: {hours}h {minutes}m",
            f"  Next METAR: :{next_metar_min:02d} ({_format_countdown(metar_secs)})  "
            f"Next AWS: :{next_aws_min:02d} ({_format_countdown(aws_secs)})",
        ]
        self.stats_text = "\n".join(lines)


class HistoryLog(Static):
    """Shows recent METAR detection history."""

    history_text: reactive[str] = reactive("  No history yet")

    def render(self) -> str:
        return self.history_text

    def update_history(self, history: list[dict]) -> None:
        if not history:
            self.history_text = "  No history yet"
            return
        lines = []
        for entry in reversed(history[-10:]):
            ts = entry.get("detected_at", "")
            try:
                dt = _to_local(datetime.fromisoformat(ts))
                ts_short = dt.strftime("%I:%M:%S %p")
            except (ValueError, TypeError):
                ts_short = ts[:8]
            etype = entry.get("event_type", "?")
            label = "NEW" if etype == "new" else "COR" if etype == "correction" else etype.upper()
            metar = entry.get("metar", "")
            # Truncate long METARs for display
            if len(metar) > 60:
                metar = metar[:57] + "..."
            lines.append(f"  [{label:3s}] {ts_short}  {metar}")
        self.history_text = "\n".join(lines)


class AwsHistoryLog(Static):
    """Shows recent AWS observation update history."""

    aws_text: reactive[str] = reactive("  No AWS updates yet")

    def render(self) -> str:
        return self.aws_text

    def update_aws_history(self, aws_history: list[dict]) -> None:
        if not aws_history:
            self.aws_text = "  No AWS updates yet"
            return
        lines = []
        for entry in reversed(aws_history[-15:]):
            detected = entry.get("detected_at", "")
            veri = entry.get("veri_zamani", "")
            try:
                dt = _to_local(datetime.fromisoformat(detected))
                det_short = dt.strftime("%I:%M:%S %p")
            except (ValueError, TypeError):
                det_short = detected[:8]
            try:
                vt = _to_local(datetime.fromisoformat(veri))
                veri_short = vt.strftime("%I:%M %p")
            except (ValueError, TypeError):
                veri_short = veri[:5]
            temp = entry.get("sicaklik", -9999)
            nem = entry.get("nem", -9999)
            ruzgar = entry.get("ruzgar_hiz", -9999)
            gorus = entry.get("gorus", -9999)
            t_str = f"{temp:.1f}°C" if temp != -9999 else "N/A"
            n_str = f"{nem}%" if nem != -9999 else "N/A"
            r_str = f"{ruzgar:.0f}km/h" if ruzgar != -9999 else "N/A"
            g_str = f"{gorus}m" if gorus != -9999 else "N/A"
            lines.append(
                f"  [AWS] {det_short}  obs:{veri_short}  "
                f"{t_str}  {n_str}  {r_str}  vis:{g_str}"
            )
        self.aws_text = "\n".join(lines)


_STATE_LABELS = {
    TempState.RISING: "[green]RISING[/]",
    TempState.FLAT: "[yellow]FLAT[/]",
    TempState.NEAR_PEAK: "[bold yellow]NEAR PEAK[/]",
    TempState.PROVISIONAL_PEAK: "[bold red]PROVISIONAL PEAK[/]",
    TempState.CONFIRMED_PEAK: "[bold white on red] CONFIRMED PEAK [/]",
    TempState.STALE: "[dim]STALE[/]",
}


class TempPanel(Static):
    """Shows temperature peak tracking status."""

    temp_text: reactive[str] = reactive("  Temp tracker: waiting for data...")

    def render(self) -> str:
        return self.temp_text

    def update_temp(self, tracker: TempTracker) -> None:
        if not tracker.samples:
            return

        current = tracker.samples[-1].temp_c_raw
        state_label = _STATE_LABELS.get(tracker.state, str(tracker.state))

        trend = tracker.trend_10m
        if trend is not None:
            if trend > 0.1:
                arrow = f"[green]↑ +{trend:.1f}°C/10m[/]"
            elif trend < -0.1:
                arrow = f"[red]↓ {trend:.1f}°C/10m[/]"
            else:
                arrow = f"[yellow]→ {trend:+.1f}°C/10m[/]"
        else:
            arrow = "→ N/A"

        max_str = "N/A"
        if tracker.observed_max_raw is not None:
            max_time = ""
            if tracker.observed_max_raw_time:
                max_time = _to_local(tracker.observed_max_raw_time).strftime("%I:%M %p")
            max_str = f"{tracker.observed_max_raw:.1f}°C at {max_time}"

        fcast_str = "N/A"
        if tracker.forecast_daily_max is not None:
            fcast_str = f"{tracker.forecast_daily_max:.0f}°C"
            gap = tracker.forecast_gap
            if gap is not None:
                fcast_str += f" (gap: {gap:+.1f}°C)"

        mins = tracker.minutes_since_max
        mins_str = f"{mins:.0f}m ago" if mins is not None and mins > 0 else "now"
        remaining_gain = tracker.nowcast.remaining_gain_c
        final_max = tracker.nowcast.final_max_estimate_c
        down_state = tracker.nowcast.down_state.value.replace("_", " ")
        forecast_state = tracker.nowcast.forecast_state.value.replace("_", " ")
        remaining_str = (
            f"{remaining_gain:.1f}°C -> final {final_max:.1f}°C"
            if remaining_gain is not None and final_max is not None
            else "N/A"
        )
        down_str = "N/A"
        if tracker.nowcast.p_going_down is not None:
            down_str = f"{down_state} ({tracker.nowcast.p_going_down:.0%})"
        vs_forecast_str = forecast_state
        if tracker.nowcast.p_reached_max is not None:
            vs_forecast_str += f"  peak {tracker.nowcast.p_reached_max:.0%}"

        lines = [
            f"  Current: [bold]{current:.1f}°C[/]  {arrow}  State: {state_label}",
            f"  Observed max: {max_str} ({mins_str})  Forecast max: {fcast_str}",
            f"  Remaining gain: {remaining_str}  Down now: {down_str}  Vs forecast: {vs_forecast_str}",
        ]
        self.temp_text = "\n".join(lines)
