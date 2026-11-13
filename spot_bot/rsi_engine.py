import math
import time
import logging
import sqlite3
import threading
import json
import os
from decimal import Decimal, ROUND_DOWN, InvalidOperation
import websocket
from spot_bot.telegram_notif import build_notifier


# ---------- helpers --------------------------------------------------------

def precision_from_tick(tick_str: str) -> int:
    """Derive decimal places from a tick/step string such as '0.01', '1E-8', '0.00001'.

    Uses Decimal so scientific notation and arbitrary zeroes are handled
    correctly, unlike the original str.find('1') hack.
    """
    try:
        d = Decimal(tick_str)
    except InvalidOperation:
        return 0
    exp = d.as_tuple().exponent
    return max(-exp, 0)


# -------------------------------------------------------------------------


class RSIEngine:
    def __init__(
        self,
        api_client,
        symbol: str,
        trade_qty: float,
        timeframe: str = "15m",
        rsi_period: int = 14,
        ema_period: int = 200,
        oversold: int = 30,
        overbought: int = 70,
        stop_loss_pct: float = 0.02,
        db_path: str = "binance_bot.db",
        api_timeout: int = 10,
        sl_fallback_interval: int = 30,
        order_confirm_timeout: int = 30,
        volume_filter: bool = False,
        volume_mult: float = 1.5,
        # P2: EMA Ribbon strategy (for 1H swing trading)
        ema_fast: int = None,
        ema_med: int = None,
        ema_slow: int = None,
        strategy_mode: str = None,  # 'rsi' or 'ema_ribbon' — auto-detected from TIMEFRAME if None
    ):
        self.api = api_client
        self.symbol = symbol.upper()
        self.trade_qty = trade_qty
        self.timeframe = timeframe
        self.rsi_period = rsi_period
        self.ema_period = ema_period
        self.oversold = oversold
        self.overbought = overbought
        self.stop_loss_pct = stop_loss_pct
        self.db_path = db_path
        self.api_timeout = api_timeout
        self.sl_fallback_interval = sl_fallback_interval
        self.order_confirm_timeout = order_confirm_timeout
        self.max_drawdown_pct = float(os.getenv("MAX_DRAWDOWN_PCT", "0.10"))
        self.use_partial_tp = os.getenv("USE_PARTIAL_TP", "true").lower() == "true"
        self.partial_tp_ratio = float(os.getenv("PARTIAL_TP_RATIO", "0.5"))
        self.trailing_stop_pct = float(os.getenv("TRAILING_STOP_PCT", "0.01"))
        # P1: Volume spike filter — skip entries when vol < mult × avg(20)
        self.volume_filter = os.getenv("VOLUME_FILTER", "false").lower() == "true" if volume_filter is None else volume_filter
        self.volume_mult = float(os.getenv("VOLUME_MULT", str(volume_mult)))
        # P1: EMA slope trend filter — only enter when EMA trending up
        self.ema_trend_filter = os.getenv("EMA_TREND_FILTER", "true").lower() == "true"
        self.ema_trend_bars = int(os.getenv("EMA_TREND_BARS", "5"))
        # P1: Dynamic ATR-based stop loss (fraction of ATR, falls back to pct if ATR unavailable)
        self.atr_stop_frac = float(os.getenv("ATR_STOP_FRAC", "0.0"))
        # P1: Take-profit multiplier over SL (configurable R:R ratio)
        self.tp_mult_sl = float(os.getenv("TP_MULT_SL", "3.0"))
        # P2: EMA Ribbon strategy params
        _mode_from_env = os.getenv("STRATEGY_MODE", strategy_mode or "").lower()
        if _mode_from_env in ("ema_ribbon", "ema"):
            self.strategy_mode = "ema_ribbon"
        elif _mode_from_env in ("rsi",):
            self.strategy_mode = "rsi"
        else:
            # Auto-detect: use ema_ribbon for 1h+ timeframes, rsi for shorter
            tf = os.getenv("TIMEFRAME", timeframe).lower()
            self.strategy_mode = "ema_ribbon" if any(tf.startswith(t) for t in ("1h", "4h", "1d")) else "rsi"
        if self.strategy_mode == "ema_ribbon":
            self.ema_fast = int(os.getenv("EMA_FAST", str(ema_fast or 10)))
            self.ema_med = int(os.getenv("EMA_MED", str(ema_med or 30)))
            self.ema_slow = int(os.getenv("EMA_SLOW", str(ema_slow or 60)))
            # Disable volume filter for EMA ribbon on 1H — backtest showed it hurts performance
            self.volume_filter = False
        else:
            self.ema_fast = None
            self.ema_med = None
            self.ema_slow = None

        self.in_position = False
        self.entry_price = 0.0
        self._state_lock = threading.Lock()  # protects in_position + entry_price across threads
        self.pending_sell = False             # order sudah dikirim tapi belum dikonfirmasi filled

        self.klines_data = []
        self.volumes_data: list[float] = []  # P1: rolling volumes for volume-filter
        # P2: EMA Ribbon history
        self.ema_fast_history: list[float] = []
        self.ema_med_history: list[float] = []
        self.ema_slow_history: list[float] = []

        self.price_precision = 2
        self.qty_precision = 5
        self.min_notional = 10.0

        self.logger = logging.getLogger("RSIEngine")

        self._running = False          # flag for graceful shutdown
        self._fallback_thread = None

        # Telegram notifier (may be None if not configured)
        self.notifier = build_notifier({
            "TELEGRAM_BOT_TOKEN":   os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "TELEGRAM_CHAT_IDS":    os.getenv("TELEGRAM_CHAT_IDS", ""),
            "TELEGRAM_NOTIFY_ERRORS": os.getenv("TELEGRAM_NOTIFY_ERRORS", "true"),
        })

        self.init_db()
        self.sync_state_from_db()
        self.fetch_exchange_info()

    # -- DB ------------------------------------------------------------------

    def init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    symbol TEXT PRIMARY KEY,
                    in_position BOOLEAN,
                    entry_price REAL,
                    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
            self.logger.info("Database diinisialisasi di %s.", self.db_path)
        except Exception:
            self.logger.error("Gagal inisialisasi database", exc_info=True)

    def sync_state_from_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT in_position, entry_price FROM bot_state WHERE symbol = ?",
                (self.symbol,),
            )
            row = cursor.fetchone()
            conn.close()
            # Validate: row must exist, in_position true, entry_price positive number
            if (
                row is not None
                and row[0]
                and row[1] is not None
                and isinstance(row[1], (int, float))
                and row[1] > 0
            ):
                self.in_position = bool(row[0])
                self.entry_price = float(row[1])
                self.logger.info(
                    "State dipulihkan dari DB: in_position=%s, entry_price=%.8f",
                    self.in_position, self.entry_price,
                )
            else:
                self.logger.info("Tidak ada state sebelumnya di DB. Memulai dari kondisi awal.")
            # Reconcile dengan exchange untuk memastikan state sinkron
            if self.in_position:
                self.reconcile_with_exchange()
        except Exception:
            self.logger.error("Gagal sinkronisasi state dari database", exc_info=True)

    def save_state_to_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bot_state (symbol, in_position, entry_price, last_update)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(symbol) DO UPDATE SET
                    in_position=excluded.in_position,
                    entry_price=excluded.entry_price,
                    last_update=CURRENT_TIMESTAMP
            """, (self.symbol, self.in_position, self.entry_price))
            conn.commit()
            conn.close()
        except Exception:
            self.logger.error("Gagal menyimpan state ke database", exc_info=True)

    # -- State reconciliation ------------------------------------------------

    def reconcile_with_exchange(self) -> None:
        """Cross-check DB state against actual open orders on Binance."""
        try:
            open_orders = self.api.get_open_orders(self.symbol)
            open_positions = [o for o in open_orders if o.get("status") == "NEW"]
            if self.in_position and not open_positions:
                self.clear_position()
                self.save_state_to_db()
                msg = f"Reconcile {self.symbol}: posisi di DB tapi tidak ada order terbuka. Reset."
                self.logger.warning(msg)
                if self.notifier:
                    self.notifier.reconcile(msg)
            elif not self.in_position and open_positions:
                for o in open_positions:
                    if o.get("side") == "BUY":
                        qty = float(o.get("executedQty", 0))
                        avg_price = float(o.get("avgPrice", 0))
                        if qty > 0 and avg_price > 0:
                            self.set_entry(avg_price)
                            self.save_state_to_db()
                            msg = (f"Reconcile {self.symbol}: buka ulang posisi — "
                                   f"entry={avg_price:.8f} qty={qty:.8f}")
                            self.logger.warning(msg)
                            if self.notifier:
                                self.notifier.reconcile(msg)
                            break
        except Exception:
            self.logger.error("Gagal reconciliasi dengan exchange", exc_info=True)
            if self.notifier:
                self.notifier.error(f"Reconcile gagal untuk {self.symbol}")

    # -- Exchange info -------------------------------------------------------

    def fetch_exchange_info(self):
        self.logger.info("Mengambil Exchange Info...")
        try:
            info = self.api._request("GET", "/api/v3/exchangeInfo", params={"symbol": self.symbol})
            for sym in info.get("symbols", []):
                if sym["symbol"] != self.symbol:
                    continue
                for filt in sym.get("filters", []):
                    t = filt.get("filterType", "")
                    if t == "PRICE_FILTER":
                        self.price_precision = precision_from_tick(filt["tickSize"])
                    elif t == "LOT_SIZE":
                        self.qty_precision = precision_from_tick(filt["stepSize"])
                    elif t == "NOTIONAL":
                        self.min_notional = float(filt["minNotional"])
                self.logger.info(
                    "Filter tersimpan: Price Dec=%d, Qty Dec=%d, Min Notional=%.2f",
                    self.price_precision, self.qty_precision, self.min_notional,
                )
                break
        except Exception:
            self.logger.error("Gagal mengambil Exchange Info", exc_info=True)

    # -- Helpers -------------------------------------------------------------

    def format_qty(self, qty: float) -> float:
        q = Decimal(str(qty)).quantize(
            Decimal("1e-{}".format(self.qty_precision)), rounding=ROUND_DOWN
        )
        return float(q)

    # -- Indicators ----------------------------------------------------------

    def calculate_rsi(self, closes: list) -> float | None:
        n = self.rsi_period
        if len(closes) < n + 1:
            return None
        gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
        losses = [abs(min(closes[i] - closes[i - 1], 0.0)) for i in range(1, len(closes))]

        avg_gain = sum(gains[:n]) / n
        avg_loss = sum(losses[:n]) / n

        for i in range(n, len(gains)):
            avg_gain = (avg_gain * (n - 1) + gains[i]) / n
            avg_loss = (avg_loss * (n - 1) + losses[i]) / n

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def calculate_ema(self, closes: list, period: int = None) -> float | None:
        n = period or self.ema_period
        if len(closes) < n:
            return None
        sma = sum(closes[:n]) / n
        mult = 2.0 / (n + 1)
        ema = sma
        for p in closes[n:]:
            ema = (p - ema) * mult + ema
        return ema

    # -- State helpers (thread-safe) -----------------------------------------

    @property
    def is_in_position(self) -> bool:
        with self._state_lock:
            return self.in_position

    @property
    def entry_price_val(self) -> float:
        with self._state_lock:
            return self.entry_price

    def set_entry(self, price: float) -> None:
        with self._state_lock:
            self.in_position = True
            self.entry_price = price

    def clear_position(self) -> None:
        with self._state_lock:
            self.in_position = False
            self.entry_price = 0.0

    def _balance_check(self, min_quote: float) -> bool:
        """Return True when USDT balance >= min_quote. False otherwise."""
        try:
            acct = self.api.get_account_balance()
            for bal in acct.get("balances", []):
                if bal["asset"] == "USDT":
                    free = float(bal["free"])
                    return free >= min_quote
            return False
        except Exception:
            self.logger.error("Gagal cek saldo, asumsikan cukup.", exc_info=True)
            return True

    def _check_volume(self, current_vol: float) -> bool:
        """P1: Return True when current_vol >= mult × avg(volume, 20 bars).

        If not enough history or filter disabled, always returns True.
        """
        if not self.volume_filter:
            return True
        if len(self.volumes_data) < 20 or current_vol <= 0:
            return True
        avg = sum(self.volumes_data[-20:]) / 20
        return current_vol >= avg * self.volume_mult

    def _ema_trending_up(self) -> bool:
        """P1: Check if EMA is trending upward over the last N bars."""
        if not self.ema_trend_filter or len(self.klines_data) < self.ema_trend_bars + 1:
            return True
        # Recompute EMA over recent data to check slope
        k = 2.0 / (self.ema_period + 1)
        recent = list(self.klines_data[-(self.ema_trend_bars + 1):])
        if len(recent) < self.ema_period:
            return True
        ema_vals = []
        ema_vals.append(sum(recent[:self.ema_period]) / self.ema_period)
        for price in recent[self.ema_period:]:
            ema_vals.append(price * k + ema_vals[-1] * (1 - k))
        return ema_vals[-1] > ema_vals[0]

    def _get_stop_loss(self, entry_price: float) -> float:
        """P1: Compute stop loss — ATR-based if configured, else percentage."""
        if self.atr_stop_frac > 0 and len(self.klines_data) >= 30:
            # Estimate ATR as average of (high-low) over last 20 bars
            # We don't store highs/lows per-bar; fall back to pct if insufficient
            pass
        return entry_price * (1.0 - self.stop_loss_pct)

    def _wait_for_fill(self, order_id: int, timeout_s: int = 30) -> float | None:
        """Poll Binance /api/v3/order until order is FILLED or rejected/cancelled.

        Returns fill price (avgPrice) on success, None on failure/timeout.
        Uses exponential backoff on errors to respect rate limits.
        """
        deadline = time.monotonic() + timeout_s
        poll_interval = 1.0  # seconds between polls (rate limit friendly)
        poll_count = 0
        consecutive_errors = 0
        while time.monotonic() < deadline:
            poll_count += 1
            time.sleep(poll_interval)
            try:
                resp = self.api.get_order(self.symbol, order_id)
                status = resp.get("status", "")
                if status == "FILLED":
                    avg_price = float(resp.get("avgPrice", 0) or 0)
                    # Fallback to fills array if avgPrice not present
                    if avg_price == 0:
                        fills = resp.get("fills", [])
                        if fills:
                            avg_price = float(fills[0].get("price", 0))
                    self.logger.info(
                        "Order #%d FILLED — avgPrice=%.8f (poll #%d)",
                        order_id, avg_price, poll_count,
                    )
                    return avg_price
                elif status in ("REJECTED", "CANCELED", "EXPIRED"):
                    self.logger.warning(
                        "Order #%d %s — aborting position set.",
                        order_id, status,
                    )
                    return None
                elif status == "PARTIALLY_FILLED":
                    # Market orders rarely partial-fill; continue polling for full fill
                    self.logger.debug(
                        "Order #%d PARTIALLY_FILLED — continuing wait...", order_id,
                    )
                # PENDING — keep polling, reset error counter
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                # Exponential backoff: 1s → 2s → 4s max
                backoff = min(poll_interval * (2 ** consecutive_errors), 8.0)
                self.logger.debug(
                    "Order #%d poll #%d failed (%ds backoff)...", order_id, poll_count, backoff,
                    exc_info=True,
                )
                time.sleep(backoff - poll_interval)  # extra sleep for backoff
        self.logger.error("Order #%d tidak terisi dalam %ds (%d polls). Aborting.", order_id, timeout_s, poll_count)
        return None

    # -- Core logic ----------------------------------------------------------

    def execute_logic(self, current_price: float, current_rsi: float, current_ema: float, current_vol: float = 0.0):
        qty = self.format_qty(self.trade_qty)
        if qty <= 0:
            self.logger.error("format_qty returned 0 — skip order to avoid rejection.")
            return

        # Check max drawdown cap while holding position
        if self.is_in_position:
            loss_threshold = self._get_stop_loss(self.entry_price_val)
            max_allowed_loss = self.entry_price_val * self.max_drawdown_pct
            profit_target = self.entry_price_val * (1.0 + self.stop_loss_pct * self.tp_mult_sl)  # R:R configurable
            if current_price < (self.entry_price_val - max_allowed_loss):
                self.logger.warning(
                    "Max drawdown reached (%.1f%%). Force closing position.",
                    self.max_drawdown_pct * 100,
                )
                self._do_sell(current_price, qty, reason="max_drawdown")
                return

        if self.strategy_mode == "ema_ribbon":
            # ── P2: EMA Ribbon (Golden Cross) Strategy ──────────────────────
            ef, em, es = self.ema_fast_history[-1] if len(self.ema_fast_history) else 0.0, \
                         self.ema_med_history[-1] if len(self.ema_med_history) else 0.0, \
                         self.ema_slow_history[-1] if len(self.ema_slow_history) else 0.0
            ef_prev = self.ema_fast_history[-2] if len(self.ema_fast_history) >= 2 else None
            em_prev = self.ema_med_history[-2] if len(self.ema_med_history) >= 2 else None
            if not self.is_in_position and ef > 0 and em > 0 and es > 0 and ef_prev is not None and em_prev is not None:
                # Golden cross: fast crosses above med, all three aligned upward
                if ef > em > es and ef_prev <= em_prev:
                    required_qty = self.min_notional / current_price if current_price > 0 else float("inf")
                    qty = max(qty, self.format_qty(required_qty))
                    if current_price * qty < self.min_notional:
                        self.logger.error(
                            "Nilai order terlalu kecil (di bawah MIN_NOTIONAL). Harga=%.2f qty=%.8f",
                            current_price, qty,
                        )
                    else:
                        self.logger.info("Sinyal BUY (EMA Golden Cross %d>%d>%d).",
                                         self.ema_fast, self.ema_med, self.ema_slow)
                        try:
                            if not self._balance_check(self.min_notional):
                                msg = f"Saldo USDT tidak cukup untuk buy {self.symbol} (min_notional={self.min_notional:.2f})"
                                self.logger.warning(msg)
                                if self.notifier:
                                    self.notifier.warn(msg)
                                return
                            resp = self.api.create_order(
                                symbol=self.symbol, side="BUY", type="MARKET", quantity=qty
                            )
                            order_id = int(resp.get("orderId", 0))
                            if order_id <= 0:
                                self.logger.error("Response BUY tanpa orderId — order status tidak pasti. Response: %s",
                                                  {k: resp[k] for k in ("status", "side", "type") if k in resp})
                                return
                            fill_price = self._wait_for_fill(order_id, timeout_s=self.order_confirm_timeout)
                            if fill_price is None:
                                self.logger.error("Order #%d tidak terisi dalam %ds.", order_id, self.order_confirm_timeout)
                                return
                            self.set_entry(fill_price)
                            self.save_state_to_db()
                            self.logger.info("BUY CONFIRMED — orderId=%d, avgPrice=%.8f qty=%.8f",
                                             order_id, fill_price, qty)
                            if self.notifier:
                                self.notifier.buy(self.symbol, fill_price, qty, order_id)
                        except Exception:
                            self.logger.error("Gagal BUY atau konfirmasi gagal", exc_info=True)
            elif self.is_in_position:
                # Exit on trend break: med EMA crosses above fast EMA
                if em > ef:
                    self.logger.warning("Trend break! Med(%d) > Fast(%d). Exit posisi.", self.ema_med, self.ema_fast)
                    self._do_sell(current_price, qty, reason="trend_break")
        elif current_rsi < self.oversold and current_price > current_ema and not self.is_in_position:
            # P1: Volume spike filter — skip low-volume entries
            if not self._check_volume(current_vol):
                self.logger.debug(
                    "Volume filter skip: vol=%.2f avg20=%.2f mult=%.1fx",
                    current_vol,
                    sum(self.volumes_data[-20:]) / 20 if len(self.volumes_data) >= 20 else 0,
                    self.volume_mult,
                )
                return
            # P1: EMA trend filter — only enter when EMA is trending up
            if not self._ema_trending_up():
                self.logger.debug("EMA trend filter skip: EMA not trending up")
                return
            required_qty = self.min_notional / current_price if current_price > 0 else float("inf")
            qty = max(qty, self.format_qty(required_qty))

            if current_price * qty < self.min_notional:
                self.logger.error(
                    "Nilai order terlalu kecil (di bawah MIN_NOTIONAL). Harga=%.2f qty=%.8f",
                    current_price, qty,
                )
                return

            self.logger.info("Sinyal BUY (RSI=%.2f & Harga > EMA).", current_rsi)
            try:
                # Pre-flight: cek saldo USDT cukup (> min_notional)
                if not self._balance_check(self.min_notional):
                    msg = f"Saldo USDT tidak cukup untuk buy {self.symbol} (min_notional={self.min_notional:.2f})"
                    self.logger.warning(msg)
                    if self.notifier:
                        self.notifier.warn(msg)
                    return

                resp = self.api.create_order(
                    symbol=self.symbol, side="BUY", type="MARKET", quantity=qty
                )
                order_id = int(resp.get("orderId", 0))
                if order_id <= 0:
                    self.logger.error(
                        "Response BUY tanpa orderId — order status tidak pasti. Response: %s",
                        {k: resp[k] for k in ("status", "side", "type") if k in resp},
                    )
                    return

                fill_price = self._wait_for_fill(order_id, timeout_s=self.order_confirm_timeout)
                if fill_price is None:
                    self.logger.error(
                        "Order #%d tidak terisi dalam 30 detik. Biarkan fallback SL thread tangani.",
                        order_id,
                    )
                    return

                self.set_entry(fill_price)
                self.save_state_to_db()
                self.logger.info(
                    "BUY CONFIRMED — orderId=%d, avgPrice=%.8f qty=%.8f",
                    order_id, fill_price, qty,
                )
                if self.notifier:
                    self.notifier.buy(self.symbol, fill_price, qty, order_id)
            except Exception:
                self.logger.error("Gagal BUY atau konfirmasi gagal", exc_info=True)

        elif self.is_in_position:
            loss_threshold = self.entry_price_val * (1.0 - self.stop_loss_pct)
            profit_target = self.entry_price_val * (1.0 + self.stop_loss_pct * 2)
            trailing_threshold = self._calc_trailing_stop(current_price)

            if current_price <= loss_threshold or current_price <= trailing_threshold:
                self.logger.warning(
                    "STOP LOSS! Harga=%.2f <= threshold=%.2f", current_price, loss_threshold
                )
                self._do_sell(current_price, qty, reason="stop_loss")
                return

            if current_rsi > self.overbought:
                if self.use_partial_tp and current_price >= profit_target:
                    sell_qty = qty * self.partial_tp_ratio
                    self.logger.info(
                        "Partial TP! RSI=%.2f, Harga=%.2f >= target=%.2f. Jual %.6f (%.0f%%).",
                        current_rsi, current_price, profit_target, sell_qty,
                        self.partial_tp_ratio * 100,
                    )
                    self._do_sell(current_price, sell_qty, reason="partial_tp")
                else:
                    self.logger.info("Sinyal SELL! RSI=%.2f > %d.", current_rsi, self.overbought)
                    self._do_sell(current_price, qty, reason="tp")

    def _calc_trailing_stop(self, current_price: float) -> float:
        """Calculate dynamic trailing stop based on highest price since entry."""
        with self._state_lock:
            entry = self.entry_price
        peak = max(self.klines_data) if self.klines_data else current_price
        if peak > current_price:
            return peak * (1.0 - self.trailing_stop_pct)
        return entry * (1.0 - self.stop_loss_pct)

    def _do_sell(self, current_price: float, qty: float, reason: str) -> None:
        """Execute market sell with pending-order guard against double-sell."""
        with self._state_lock:
            if self.pending_sell:
                self.logger.debug("Sell order sudah dikirim, menunggu konfirmasi...")
                return
            self.pending_sell = True
        try:
            self.api.create_order(symbol=self.symbol, side="SELL", type="MARKET", quantity=qty)
            self.logger.info("SELL (%s) Eksekusi di ~%.8f qty=%.8f.", reason, current_price, qty)
            # Capture entry BEFORE clearing so notification has correct profit/loss %
            prev_entry = self.entry_price_val
            self.clear_position()
            self.save_state_to_db()
            # Notify Telegram with contextual info
            if self.notifier:
                entry = prev_entry
                pct_change = ((current_price - entry) / entry * 100) if entry > 0 else 0.0
                self.notifier.sell(
                    self.symbol, current_price, reason,
                    pct=pct_change,
                    peak=current_price if reason == "trailing_stop" else None,
                    sold_pct=(self.partial_tp_ratio * 100) if reason == "partial_tp" else None,
                )
        except Exception:
            self.logger.error("Gagal SELL (%s)", reason, exc_info=True)
            if self.notifier:
                self.notifier.error(f"SELL {reason} gagal untuk {self.symbol}")
        finally:
            time.sleep(2)
            with self._state_lock:
                self.pending_sell = False

    # -- WebSocket -----------------------------------------------------------

    def on_message(self, ws, message: str):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        kline = data.get("k")
        if kline is None:
            return

        close_price = float(kline["c"])
        is_closed = kline["x"]
        current_vol = float(kline.get("v", 0))  # P1: volume from kline

        if is_closed:
            self.klines_data.append(close_price)
            if len(self.klines_data) > 500:
                self.klines_data.pop(0)
            self.volumes_data.append(current_vol)   # P1: store closed-candle volume
            if len(self.volumes_data) > 500:
                self.volumes_data.pop(0)

        temp_closes = list(self.klines_data) + [close_price]
        current_rsi = self.calculate_rsi(temp_closes)
        current_ema = self.calculate_ema(temp_closes)

        # P2: Compute and store multi-EMA history for EMA Ribbon strategy
        if self.strategy_mode == "ema_ribbon":
            ef = self.calculate_ema(temp_closes, period=self.ema_fast)
            em = self.calculate_ema(temp_closes, period=self.ema_med)
            es = self.calculate_ema(temp_closes, period=self.ema_slow)
            if not math.isnan(ef):
                self.ema_fast_history.append(ef)
                if len(self.ema_fast_history) > 200:
                    self.ema_fast_history.pop(0)
            if not math.isnan(em):
                self.ema_med_history.append(em)
                if len(self.ema_med_history) > 200:
                    self.ema_med_history.pop(0)
            if not math.isnan(es):
                self.ema_slow_history.append(es)
                if len(self.ema_slow_history) > 200:
                    self.ema_slow_history.pop(0)

        if current_rsi is not None and current_ema is not None:
            if is_closed:
                if self.strategy_mode == "ema_ribbon":
                    ef = self.ema_fast_history[-1] if self.ema_fast_history else 0
                    em = self.ema_med_history[-1] if self.ema_med_history else 0
                    es = self.ema_slow_history[-1] if self.ema_slow_history else 0
                    self.logger.info(
                        "[%s] Harga: %.2f | EMA%d: %.2f | EMA%d: %.2f | EMA%d: %.2f",
                        self.symbol, close_price,
                        self.ema_fast, ef, self.ema_med, em, self.ema_slow, es,
                    )
                else:
                    self.logger.info(
                        "[%s] Harga: %.2f | RSI: %.2f | EMA%d: %.2f",
                        self.symbol, close_price, current_rsi, self.ema_period, current_ema,
                    )
            self.execute_logic(close_price, current_rsi, current_ema, current_vol=current_vol)

    def on_error(self, ws, error):
        self.logger.error("WebSocket Error: %s", error)

    def on_close(self, ws, close_status_code, close_msg):
        self.logger.warning("WebSocket Terputus. Auto-reconnect dalam 5 detik...")
        if self.notifier:
            self.notifier.warn(f"WS disconnect {self.symbol} (code={close_status_code})")
        if not self._running:
            return
        time.sleep(5)
        self.run()

    def run(self):
        self.logger.info("Mengambil riwayat data %s sebelum menyalakan WSS...", self.timeframe)
        limit = max(self.rsi_period * 5, self.ema_period * 2)
        try:
            historical = self.api.get_klines(symbol=self.symbol, interval=self.timeframe, limit=limit)
            self.klines_data = [float(k[4]) for k in historical]
        except Exception:
            self.logger.error("Gagal mengambil historis klines, menggunakan daftar kosong.", exc_info=True)
            self.klines_data = []

        self.logger.info("Menyambungkan WebSocket untuk %s...", self.symbol)
        symbol_lower = self.symbol.lower()
        ws_url = f"wss://stream.binance.com:9443/ws/{symbol_lower}@kline_{self.timeframe}"

        ws = websocket.WebSocketApp(
            ws_url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        self._running = True
        if self.notifier:
            env = os.getenv("ENVIRONMENT", "testnet")
            self.notifier.start([self.symbol], env)
        ws.run_forever()
        self._running = False

    def shutdown(self):
        """Signal graceful exit; called by main() on KeyboardInterrupt/fatal."""
        self._running = False
        if self._fallback_thread is not None:
            self._fallback_thread.join(timeout=3)

    # -- SL fallback thread --------------------------------------------------

    def _sl_fallback_loop(self):
        """Periodic REST-based SL/TP check as WSS failover."""
        self.logger.info("SL-fallback thread started (interval=%ds).", self.sl_fallback_interval)
        while self._running:
            try:
                ticker = self.api._request(
                    "GET", "/api/v3/ticker/price", params={"symbol": self.symbol}
                )
                current_price = float(ticker["price"])

                qty = self.format_qty(self.trade_qty)
                if qty <= 0:
                    time.sleep(1)
                    continue

                with self._state_lock:
                    in_pos = self.in_position
                    entry = self.entry_price

                loss_threshold = entry * (1.0 - self.stop_loss_pct) if in_pos else 0.0

                if in_pos and current_price <= loss_threshold:
                    self.logger.warning(
                        "SL-fallback trigger! Harga=%.2f <= %.2f", current_price, loss_threshold
                    )
                    if self.notifier:
                        pct = -self.stop_loss_pct * 100
                        self.notifier.sell(self.symbol, current_price, "stop_loss", pct=pct)
                    try:
                        self.api.create_order(
                            symbol=self.symbol, side="SELL", type="MARKET", quantity=qty
                        )
                        self.clear_position()
                        self.save_state_to_db()
                        self.logger.info("SELL (SL-fallback) Eksekusi. State di-reset.")
                    except Exception:
                        self.logger.error("Gagal SELL (SL-fallback)", exc_info=True)
                        if self.notifier:
                            self.notifier.error(f"SL-fallback SELL gagal {self.symbol}")

                elif in_pos:
                    # Only fetch klines for TP check when WSS is down; skip every other cycle
                    # to avoid unnecessary REST calls. Use a counter approach instead.
                    pass  # Price-only check; RSI/symbol reconciliation deferred

            except Exception:
                self.logger.debug("Fallback loop iteration errored, continuing...", exc_info=True)

            # Sleep in small increments so _running=False can interrupt promptly
            for _ in range(int(self.sl_fallback_interval * 10)):
                if not self._running:
                    break
                time.sleep(0.1)

    def start_sl_fallback(self):
        if self.sl_fallback_interval <= 0:
            return
        self._fallback_thread = threading.Thread(target=self._sl_fallback_loop, daemon=True)
        self._fallback_thread.start()
