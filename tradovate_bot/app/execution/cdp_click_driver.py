"""
Chrome DevTools Protocol (CDP) click driver.

Bypasses the OS-level synthetic-input path entirely. Chromium has tightened
its trusted-event checks since M102, silently dropping `SendInput` clicks
whose metadata doesn't look like a real HID device — Tradovate hosted in
Chrome exhibits exactly that behavior when driven from a Qt host process.

CDP's `Input.dispatchMouseEvent` injects events at the Blink render layer
with the trusted flag set, so web handlers (including Tradovate's Buy Mkt /
Sell Mkt buttons) receive them as normal user clicks.

Setup required once per machine:
    - Quit all Chrome windows.
    - Relaunch Chrome with `--remote-debugging-port=9222` added to the
      shortcut / command line. Example shortcut target:
          "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
              --remote-debugging-port=9222
    - Log in to Tradovate in that Chrome.

The bot auto-discovers the Tradovate tab by URL substring and holds a
WebSocket connection to its CDP target. Click coordinates are translated
from screen space to viewport space using `window.screenX / window.screenY`.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from app.models.common import Point

log = logging.getLogger(__name__)


class CDPUnavailable(RuntimeError):
    """Raised when we can't reach the CDP endpoint or can't find a matching tab."""


class CDPClickDriver:
    """Synchronous click driver that talks to Chrome via CDP over a persistent
    WebSocket. Thread-safe — the supervisor's executor thread is the only
    caller, but locking makes the intent explicit."""

    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 9222
    DEFAULT_URL_FILTERS = ("tradovate", "trader.tradovate")

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        url_filters: tuple[str, ...] = DEFAULT_URL_FILTERS,
        connect_timeout: float = 3.0,
    ) -> None:
        import websocket  # imported lazily so non-CDP paths don't require it

        self._ws_cls = websocket
        self.host = host
        self.port = port
        self.url_filters = tuple(f.lower() for f in url_filters)
        self.connect_timeout = connect_timeout

        self._lock = threading.Lock()
        self._msg_id = 0
        self._ws = None
        self._target_id: Optional[str] = None
        self._target_url: Optional[str] = None

        self._connect()

    # ---- public API ---- #

    def click_point(self, point: Point) -> None:
        with self._lock:
            self._ensure_connected()
            vx, vy = self._screen_to_viewport(point.x, point.y)
            # Bring the tab to focus within Chrome (doesn't require OS foreground)
            self._send("Page.bringToFront", {})
            # Dispatch a full mouse sequence: move → press → release.
            # `clickCount=1` is required for the DOM to see this as a click.
            self._send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": vx, "y": vy,
                "button": "none",
            })
            self._send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": vx, "y": vy,
                "button": "left", "buttons": 1, "clickCount": 1,
            })
            self._send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": vx, "y": vy,
                "button": "left", "buttons": 0, "clickCount": 1,
            })

    def close(self) -> None:
        with self._lock:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass
                self._ws = None

    # ---- internals ---- #

    def _connect(self) -> None:
        targets = self._list_targets()
        picked = None
        for t in targets:
            url = t.get("url", "").lower()
            if t.get("type") != "page":
                continue
            if any(f in url for f in self.url_filters):
                picked = t
                break
        if picked is None:
            available = ", ".join(
                f"{t.get('type')}:{t.get('url','')[:60]}" for t in targets
            ) or "(none)"
            raise CDPUnavailable(
                f"No Chrome tab matching {self.url_filters} on "
                f"{self.host}:{self.port}. Available targets: {available}. "
                "Launch Chrome with --remote-debugging-port=9222 and open "
                "Tradovate in that window."
            )
        ws_url = picked["webSocketDebuggerUrl"]
        try:
            self._ws = self._ws_cls.create_connection(
                ws_url, timeout=self.connect_timeout,
            )
        except Exception as e:
            raise CDPUnavailable(f"websocket connect failed: {e}") from e
        self._target_id = picked["id"]
        self._target_url = picked["url"]
        self._send("Page.enable", {})
        log.info(
            "CDP click driver connected to tab id=%s url=%s",
            self._target_id, self._target_url,
        )

    def _list_targets(self) -> list[dict]:
        url = f"http://{self.host}:{self.port}/json"
        try:
            with urllib.request.urlopen(url, timeout=self.connect_timeout) as r:
                return json.loads(r.read())
        except urllib.error.URLError as e:
            raise CDPUnavailable(
                f"CDP endpoint {url} unreachable: {e}. Is Chrome running "
                "with --remote-debugging-port=9222?"
            ) from e

    def _ensure_connected(self) -> None:
        if self._ws is None or not self._ws.connected:
            log.info("CDP ws reconnecting…")
            self._connect()

    def _send(self, method: str, params: dict) -> dict:
        self._msg_id += 1
        frame = {"id": self._msg_id, "method": method, "params": params}
        self._ws.send(json.dumps(frame))
        while True:
            raw = self._ws.recv()
            if not raw:
                raise CDPUnavailable("CDP websocket closed mid-request")
            data = json.loads(raw)
            if data.get("id") == self._msg_id:
                if "error" in data:
                    raise RuntimeError(
                        f"CDP {method} failed: {data['error']}"
                    )
                return data.get("result", {})
            # Else it's an event frame — ignore.

    def _screen_to_viewport(self, sx: int, sy: int) -> tuple[int, int]:
        """Translate a screen pixel (sx, sy) into viewport-relative CSS pixels
        by asking the page itself where its viewport starts on the screen."""
        r = self._send("Runtime.evaluate", {
            "expression": (
                "JSON.stringify({sx: window.screenX, sy: window.screenY, "
                "dpr: window.devicePixelRatio})"
            ),
            "returnByValue": True,
        })
        val = json.loads(r["result"]["value"])
        dpr = float(val.get("dpr") or 1.0)
        # window.screenX/Y are in CSS pixels already, as are
        # Input.dispatchMouseEvent coordinates.
        vx = int(round((sx - int(val["sx"])) / dpr))
        vy = int(round((sy - int(val["sy"])) / dpr))
        return vx, vy
