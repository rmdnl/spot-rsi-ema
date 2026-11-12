# -*- coding: utf-8 -*-
"""Telegram notification module for Binance RSI Spot Bot."""
import os
import time
import logging
import threading
from urllib.parse import urlencode

import requests


class TelegramNotifier:
    """Ringan: kirim notifikasi ke Telegram tanpa library tambahan."""

    _FORMATS = {
        "buy":          "🟢 *BUY CONFIRMED*\nSymbol: {symbol}\nPrice: ${price:,.2f}\nQty: {qty:.8f}\nOrderID: {order_id}",
        "sell_sl":      "🔴 *SELL — STOP LOSS*\nSymbol: {symbol}\nPrice: ${price:,.2f}\nReason: SL hit at {pct}% loss",
        "sell_tp":      "🟡 *SELL — TAKE PROFIT*\nSymbol: {symbol}\nPrice: ${price:,.2f}\nProfit: +{pct:.2f}%",
        "sell_partial": "🟠 *SELL — PARTIAL TP*\nSymbol: {symbol}\nPrice: ${price:,.2f}\nSold: {pct:.0f}% of position",
        "sell_trailing":"🟠 *SELL — TRAILING STOP*\nSymbol: {symbol}\nPrice: ${price:,.2f}\nPeak: ${peak:,.2f}",
        "sell_dd":      "🔴 *SELL — MAX DRAWDOWN*\nSymbol: {symbol}\nPrice: ${price:,.2f}\nLoss: {pct:.2f}% from entry",
        "error":        "⚠️ *ERROR*\n{message}",
        "warn":         "⚡ *WARNING*\n{message}",
        "info":         "ℹ️ *INFO*\n{message}",
        "reconcile":    "🔄 *RECONCILE*\n{message}",
        "start":        "🚀 *BOT STARTED*\nSymbols: {symbols}\nEnvironment: {env}",
        "shutdown":     "🛑 *BOT SHUTDOWN*\nReason: {reason}",
    }

    def __init__(self, bot_token: str, chat_ids: list[str], notify_errors: bool = True):
        self.bot_token = bot_token.strip()
        self.chat_ids = [str(c).strip() for c in chat_ids if str(c).strip()]
        self.notify_errors = notify_errors
        self._base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._lock = threading.Lock()
        # Telegram allows 30 msg/sec; we throttle per-chat to be safe
        self._last_send: dict[str, float] = {cid: 0.0 for cid in self.chat_ids}
        self._throttle_sec = 2.0
        self.logger = logging.getLogger("TelegramNotifier")
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    def _can_send(self, chat_id: str) -> bool:
        with self._lock:
            now = time.monotonic()
            last = self._last_send.get(chat_id, 0.0)
            return (now - last) >= self._throttle_sec

    def _update_last(self, chat_id: str) -> None:
        with self._lock:
            self._last_send[chat_id] = time.monotonic()

    def _send(self, chat_id: str, text: str) -> bool:
        """Send a single message; returns True on success."""
        if not self.bot_token or not chat_id:
            return False
        if not self._can_send(chat_id):
            return False
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            resp = self._session.post(
                f"{self._base_url}/sendMessage",
                json=payload,
                timeout=10,
            )
            data = resp.json()
            ok = data.get("ok", False)
            if not ok:
                err = data.get("description", repr(data))
                self.logger.warning("Telegram error for chat %s: %s", chat_id[:8], err)
            else:
                self._update_last(chat_id)
            return ok
        except Exception:
            self.logger.debug("Telegram send failed for chat %s", chat_id[:8], exc_info=True)
            return False

    def notify(self, kind: str, **kwargs) -> None:
        """Thread-safe public interface. Format string from kwargs, dispatch to all chats."""
        template = self._FORMATS.get(kind)
        if template is None:
            self.logger.error("Unknown notification kind: %s", kind)
            return
        try:
            text = template.format(**kwargs)
        except KeyError as e:
            self.logger.error("Missing key for notif %s: %s", kind, e)
            return
        # Errors & warnings: send even if throttle active (use larger delay)
        if kind in ("error", "warn") and not self.notify_errors:
            return
        for cid in self.chat_ids:
            self._send(cid, text)

    def buy(self, symbol: str, price: float, qty: float, order_id: int) -> None:
        self.notify("buy", symbol=symbol, price=price, qty=qty, order_id=order_id)

    def sell(self, symbol: str, price: float, reason: str, pct: float | None = None,
             peak: float | None = None, sold_pct: float | None = None) -> None:
        tag = "sell_" + reason
        fmt = self._FORMATS.get(tag, self._FORMATS["sell_tp"])
        try:
            text = fmt.format(symbol=symbol, price=price, pct=pct or 0.0,
                              peak=peak or 0.0, sold_pct=sold_pct or 0.0)
        except KeyError:
            text = f"📉 SELL ({reason})\nSymbol: {symbol}\nPrice: {price:.2f}"
        for cid in self.chat_ids:
            self._send(cid, text)

    def start(self, symbols: list[str], env: str) -> None:
        self.notify("start", symbols=", ".join(symbols), env=env)

    def shutdown(self, reason: str = "") -> None:
        self.notify("shutdown", reason=reason or "manual / KeyboardInterrupt")

    def reconcile(self, message: str) -> None:
        self.notify("reconcile", message=message)

    def error(self, message: str) -> None:
        self.notify("error", message=message)

    def warn(self, message: str) -> None:
        self.notify("warn", message=message)

    def info(self, message: str) -> None:
        self.notify("info", message=message)


def build_notifier(env_vars: dict) -> TelegramNotifier | None:
    """Create notifier from env vars; return None when disabled."""
    token = env_vars.get("TELEGRAM_BOT_TOKEN", "").strip()
    raw_chats = env_vars.get("TELEGRAM_CHAT_IDS", "").strip()
    if not token or not raw_chats:
        return None
    chat_ids = [c.strip() for c in raw_chats.split(",") if c.strip()]
    notify_errors = env_vars.get("TELEGRAM_NOTIFY_ERRORS", "true").lower() != "false"
    return TelegramNotifier(token, chat_ids, notify_errors)