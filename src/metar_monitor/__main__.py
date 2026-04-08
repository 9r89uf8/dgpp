"""Entry point for the METAR monitor: python -m metar_monitor"""

from __future__ import annotations

import argparse
import asyncio
import logging

from .client import MGMClient
from .config import (
    AIRPORT_ELEVATION_M,
    AIRPORT_ICAO,
    AIRPORT_LAT,
    AIRPORT_LON,
    AIRPORT_NAME,
    AIRPORT_TIMEZONE,
    DEFAULT_DB_PATH,
    MGM_DAILY_FORECAST_ISTNO,
    MGM_OBS_ISTNO,
    MGM_SHAPE_FORECAST_ISTNO,
)
from .db import Database
from .import_json import import_monitor_state
from .state import MonitorState


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ultra-low-latency LTAC METAR monitor",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--adopt-current",
        dest="mode",
        action="store_const",
        const="adopt-current",
        default="adopt-current",
        help="Seed detector with current METAR, no alert (default)",
    )
    mode.add_argument(
        "--alert-if-fresh",
        "--alert-on-first",
        dest="mode",
        action="store_const",
        const="alert-if-fresh",
        help="Alert if current METAR differs from persisted state",
    )
    mode.add_argument(
        "--silent-warmup",
        dest="mode",
        action="store_const",
        const="silent-warmup",
        help="Suppress alerts for 10s on startup (first install)",
    )

    p.add_argument(
        "--headless", "--no-ui",
        action="store_true",
        help="Run without Textual UI (alerts + logging only)",
    )
    p.add_argument(
        "--web",
        action="store_true",
        help="Start web dashboard (implies --headless)",
    )
    p.add_argument(
        "--web-port",
        type=int,
        default=8080,
        help="Web dashboard port (default: 8080)",
    )
    p.add_argument(
        "--state-file",
        default=None,
        help="Override state directory (default: ~/.metar_monitor)",
    )
    p.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help="SQLite database path (default: ~/.metar_monitor/metar_monitor.db)",
    )
    p.add_argument(
        "--import-json",
        action="store_true",
        help="Import the legacy state.json history into SQLite, then exit",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Override base polling interval in seconds",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    state = MonitorState(state_dir=args.state_file)
    state.load()

    if args.import_json:
        db = _init_db(args.db_path)
        counts = import_monitor_state(state, db)
        logging.getLogger("metar_monitor.import_json").info(
            "Imported JSON state into %s: %s",
            db.path,
            ", ".join(f"{key}={value}" for key, value in counts.items()),
        )
        return

    client = MGMClient()
    db = _init_db(args.db_path)

    if args.web:
        # --web implies headless
        _run_web(client, state, db, args)
    elif args.headless:
        _run_headless(client, state, db, args)
    else:
        _run_ui(client, state, db, args)


def _init_db(db_path: str) -> Database:
    db = Database(db_path=db_path)
    db.init_schema()
    db.ensure_airport(
        icao=AIRPORT_ICAO,
        name=AIRPORT_NAME,
        timezone_name=AIRPORT_TIMEZONE,
        lat=AIRPORT_LAT,
        lon=AIRPORT_LON,
        elevation_m=AIRPORT_ELEVATION_M,
    )
    db.ensure_airport_source(
        airport_icao=AIRPORT_ICAO,
        provider="mgm",
        product_kind="obs",
        external_id=str(MGM_OBS_ISTNO),
        priority=0,
    )
    db.ensure_airport_source(
        airport_icao=AIRPORT_ICAO,
        provider="mgm",
        product_kind="daily_forecast",
        external_id=str(MGM_DAILY_FORECAST_ISTNO),
        priority=0,
    )
    db.ensure_airport_source(
        airport_icao=AIRPORT_ICAO,
        provider="mgm",
        product_kind="shape_forecast",
        external_id=str(MGM_SHAPE_FORECAST_ISTNO),
        priority=0,
    )
    return db


def _run_ui(
    client: MGMClient,
    state: MonitorState,
    db: Database | None,
    args: argparse.Namespace,
) -> None:
    from .app import MetarMonitorApp

    app = MetarMonitorApp(
        client=client,
        state=state,
        db=db,
        startup_mode=args.mode,
        base_interval=args.interval,
    )
    app.run()


def _run_headless(
    client: MGMClient, state: MonitorState, db: Database | None, args: argparse.Namespace
) -> None:
    from .runtime import Runtime

    log = logging.getLogger("metar_monitor.headless")
    rt = Runtime(
        client=client,
        state=state,
        db=db,
        startup_mode=args.mode,
        base_interval=args.interval,
    )

    async def run() -> None:
        await rt.monitor.warmup(mode=args.mode)
        log.info("Monitor started (headless). Press Ctrl+C to stop.")
        try:
            await rt.monitor.run()
        except asyncio.CancelledError:
            pass
        finally:
            await client.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Shutting down.")


def _run_web(
    client: MGMClient, state: MonitorState, db: Database | None, args: argparse.Namespace
) -> None:
    import uvicorn
    from .runtime import Runtime
    from .web.server import create_app

    log = logging.getLogger("metar_monitor.web")
    rt = Runtime(
        client=client,
        state=state,
        db=db,
        startup_mode=args.mode,
        base_interval=args.interval,
    )
    app = create_app(rt)

    async def run() -> None:
        await rt.monitor.warmup(mode=args.mode)
        log.info("Monitor started with web dashboard on port %d", args.web_port)

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=args.web_port,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        monitor_task = asyncio.create_task(rt.monitor.run())
        server_task = asyncio.create_task(server.serve())

        try:
            done, pending = await asyncio.wait(
                [monitor_task, server_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
        except asyncio.CancelledError:
            pass
        finally:
            rt.monitor.stop()
            await client.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
