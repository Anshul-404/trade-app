"""Single upstream DhanHQ market-feed connection; never places orders."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

LOGGER = logging.getLogger(__name__)
EXCHANGE_CODES = {"IDX_I": "IDX", "NSE_EQ": "NSE", "NSE_FNO": "NSE_FNO",
                  "BSE_EQ": "BSE", "MCX_COMM": "MCX"}


class DhanStream:
    def __init__(self, client_id: str, access_token: str, exchange: str,
                 security_id: str, on_tick: Callable[[float, int], None]):
        self.client_id = client_id
        self._access_token = access_token
        self.exchange = exchange
        self.security_id = str(security_id)
        self.on_tick = on_tick
        self.state = "not-started"
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._feed = None
        self._stopping = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="DhanHQFeed")
        self._thread.start()

    def _run(self):
        # Dhan's SDK creates its own event loop: construct it IN the worker thread.
        try:
            from dhanhq import DhanContext, MarketFeed
            code = getattr(MarketFeed, EXCHANGE_CODES[self.exchange])
            context = DhanContext(self.client_id, self._access_token)
            self.state = "connecting"

            def on_connect(*_):
                self.state = "connected"
                self.error = None

            def on_message(_instance, message):
                if not isinstance(message, dict):
                    return
                if str(message.get("security_id")) != self.security_id:
                    return
                # 'Previous Close', OI, and market status aren't trade ticks.
                if message.get("type") not in {"Ticker Data", "Quote Data", "Full Data"}:
                    return
                try:
                    price = float(message["LTP"])
                except (KeyError, ValueError, TypeError):
                    return
                # SDK converts exchange LTT into a time-of-day string (loses date).
                # Receipt time is used for display; do not use as backtest-quality tick time.
                self.on_tick(price, int(time.time()))

            def on_close(*_):
                self.state = "disconnected" if not self._stopping else "stopped"

            def on_error(*_):
                self.state = "error"
                self.error = "Dhan feed error. Check console and DhanHQ token/subscription."

            self._feed = MarketFeed(
                context, [(code, self.security_id, MarketFeed.Ticker)],
                version="v2", on_connect=on_connect, on_message=on_message,
                on_close=on_close, on_error=on_error,
            )
            # run() processes incoming data continuously and handles reconnects.
            # SDK's run_forever() in some versions only opens the connection.
            self._feed.run()
        except Exception as exc:
            LOGGER.error("DhanHQ stream failed: %s", type(exc).__name__)
            self.state = "error"
            self.error = f"Dhan feed failed ({type(exc).__name__}). Check logs/config."

    def stop(self):
        self._stopping = True
        if self._feed is not None:
            try:
                self._feed.close_connection()
            except Exception:
                LOGGER.exception("Could not close Dhan feed")
        self.state = "stopped"
