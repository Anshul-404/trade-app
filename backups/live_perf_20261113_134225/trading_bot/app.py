"""Local read-only Trading Bot chart service."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .candles import CandleStore, SUPPORTED_MINUTES
from .dhan_feed import DhanStream, EXCHANGE_CODES
from .history import fetch_recent_1m
from .quality import analyze_quality

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
LOGGER = logging.getLogger(__name__)

CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "").strip()
ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
EXCHANGE = os.getenv("TB_EXCHANGE", "IDX_I").strip().upper()
SECURITY_ID = os.getenv("TB_SECURITY_ID", "13").strip()
INSTRUMENT = os.getenv("TB_INSTRUMENT", "INDEX").strip().upper()
SYMBOL = os.getenv("TB_SYMBOL", "NIFTY 50").strip()

store = CandleStore(volume_supported=(INSTRUMENT != "INDEX"))
stream: DhanStream | None = None
history_status = "not-started"
history_error: str | None = None
clients: set[WebSocket] = set()


def _status() -> dict:
    latest = store.last_tick_epoch
    return {
        "project": "Trading Bot", "symbol": SYMBOL, "exchange": EXCHANGE,
        "security_id": SECURITY_ID, "timeframes": list(SUPPORTED_MINUTES),
        "feed_status": stream.state if stream is not None else "not-started",
        "feed_error": stream.error if stream is not None else None,
        "historical_status": history_status, "historical_error": history_error,
        "last_price": store.last_price,
        "last_tick_utc": datetime.fromtimestamp(latest, timezone.utc).isoformat() if latest else None,
        "candles_1m": len(store.snapshot(1, limit=6000)),
        "orders_enabled": False,
    }


async def _run_broadcasts():
    last_version = -1
    while True:
        await asyncio.sleep(0.7)
        if not clients:
            continue
        if store.version != last_version:
            last_version = store.version
            bars = {str(tf): (store.snapshot(tf, limit=1) or [None])[-1]
                    for tf in SUPPORTED_MINUTES}
            message = {"type": "bars", "bars": bars, "status": _status()}
        else:
            message = {"type": "status", "status": _status()}
        dead = []
        for websocket in tuple(clients):
            try:
                await websocket.send_json(message)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            clients.discard(websocket)


async def _warm_history():
    global history_status, history_error
    history_status = "loading"
    try:
        raw = await asyncio.to_thread(fetch_recent_1m, CLIENT_ID, ACCESS_TOKEN,
                                      EXCHANGE, SECURITY_ID, INSTRUMENT)
        store.seed(raw)
        history_status = "loaded"
    except Exception as exc:
        history_status = "error"
        history_error = f"Historical load failed ({type(exc).__name__}); check subscription/logs."
        LOGGER.error("Historical data load failed: %s", type(exc).__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global stream, history_status, history_error
    if EXCHANGE not in EXCHANGE_CODES:
        raise RuntimeError(f"Unsupported exchange: {EXCHANGE}")
    task = asyncio.create_task(_run_broadcasts())
    history_task = None
    if not CLIENT_ID or not ACCESS_TOKEN:
        history_status = "unconfigured"
        history_error = "Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in trading_bot/.env"
    else:
        # Connect LIVE immediately; history loading must not delay the feed.
        stream = DhanStream(CLIENT_ID, ACCESS_TOKEN, EXCHANGE, SECURITY_ID,
                            lambda price, ts: store.on_tick(price, ts))
        stream.start()
        history_task = asyncio.create_task(_warm_history())
    try:
        yield
    finally:
        if history_task is not None:
            history_task.cancel()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if stream is not None:
            stream.stop()


app = FastAPI(title="Trading Bot â€” Live Chart", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def homepage():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/status")
def status():
    return _status()


@app.get("/api/candles")
def candles(timeframe: int = Query(default=1, ge=1, le=60)):
    if timeframe not in SUPPORTED_MINUTES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Supported: 1, 2, 5, 10, 15, 60 minutes")
    return {"symbol": SYMBOL, "timeframe": timeframe, "candles": store.snapshot(timeframe)}




@app.get("/api/structure")
def structure(timeframe: int = Query(default=1, ge=1, le=60)):
    from fastapi import HTTPException
    from .structure_overlay import analyze_structure
    if timeframe not in SUPPORTED_MINUTES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")
    return analyze_structure(store.snapshot(timeframe), timeframe=timeframe, symbol=SYMBOL)


@app.get("/api/zones")
def zones(timeframe: int = Query(default=1, ge=1, le=60)):
    from fastapi import HTTPException
    from .zones_overlay import analyze_zones
    if timeframe not in SUPPORTED_MINUTES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")
    return analyze_zones(store.snapshot(timeframe), timeframe=timeframe, symbol=SYMBOL)


@app.get("/api/quality")
def quality():
    result = analyze_quality(store.snapshot(1, limit=store.max_bars))
    result["symbol"] = SYMBOL
    result["feed_status"] = stream.state if stream is not None else "not-started"
    result["historical_status"] = history_status
    return result

@app.websocket("/ws/chart")
async def chart_socket(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        await websocket.send_json({"type": "status", "status": _status()})
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        clients.discard(websocket)

