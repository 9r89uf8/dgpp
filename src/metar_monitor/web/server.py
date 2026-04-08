"""FastAPI web server for the METAR monitor dashboard."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(runtime) -> FastAPI:
    """Create the FastAPI app wired to a Runtime instance."""
    from ..runtime import Runtime
    rt: Runtime = runtime

    app = FastAPI(title="LTAC METAR Monitor")

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_path = STATIC_DIR / "index.html"
        return index_path.read_text(encoding="utf-8")

    @app.get("/api/snapshot")
    async def snapshot():
        return rt.snapshot()

    @app.get("/api/history/metar")
    async def metar_history(since: str | None = None, limit: int | None = None):
        return rt.metar_history(since=since, limit=limit)

    @app.get("/api/history/aws")
    async def aws_history(since: str | None = None, limit: int | None = None):
        return rt.aws_history(since=since, limit=limit)

    @app.get("/api/history/forecast")
    async def forecast_history(since: str | None = None, limit: int | None = None):
        return rt.forecast_history(since=since, limit=limit)

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        sub = rt.hub.subscribe(maxsize=256)

        try:
            # Send init message with full state
            init_msg = {
                "type": "init",
                "snapshot": rt.snapshot(),
                "aws_history": rt.aws_history(),
                "metar_history": rt.metar_history(),
                "forecast_history": rt.forecast_history(),
            }
            await ws.send_json(init_msg)

            # Start 1Hz heartbeat task
            async def heartbeat():
                while not sub.closed:
                    try:
                        await ws.send_json({
                            "type": "stats_tick",
                            "snapshot": rt.snapshot(),
                        })
                    except Exception:
                        break
                    await asyncio.sleep(1.0)

            heartbeat_task = asyncio.create_task(heartbeat())

            # Forward hub messages
            try:
                while True:
                    msg = await sub.get()
                    ws_msg = {
                        "type": msg.kind,
                        "seq": msg.seq,
                        "created_at": msg.created_at.isoformat(),
                        "payload": msg.payload,
                    }
                    await ws.send_json(ws_msg)
            except ConnectionError:
                pass  # subscription closed (zombie or shutdown)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug("WebSocket error: %s", e)
        finally:
            heartbeat_task.cancel()
            if not sub.closed:
                sub.unsubscribe()

    return app
