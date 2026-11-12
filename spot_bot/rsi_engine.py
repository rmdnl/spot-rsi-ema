import math
import time
import logging
import sqlite3
import threading
import json
from decimal import Decimal, ROUND_DOWN, InvalidOperation
import websocket


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
        self.sl_fallback_interval = sl_fallback_interval

        self.in_position = False
        self.entry_price = 0.0

        self.klines_data = []

        self.price_precision = 2
        self.qty_precision = 5
        self.min_notional = 10.0

        self.logger = logging.getLogger("RSIEngine")

        self._running = False          # flag for graceful shutdown
        self._fallback_thread = None

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

    def calculate_ema(self, closes: list) -> float | None:
        n = self.ema_period
        if len(closes) < n:
            return None
        sma = sum(closes[:n]) / n
        mult = 2.0 / (n + 1)
        ema = sma
        for p in closes[n:]:
            ema = (p - ema) * mult + ema
        return ema

    # -- Core logic ----------------------------------------------------------

    def execute_logic(self, current_price: float, current_rsi: float, current_ema: float):
        qty = self.format_qty(self.trade_qty)

        if current_rsi < self.oversold and current_price > current_ema and not self.in_position:
            # Ensure order meets MIN_NOTIONAL after truncation
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
                self.api.create_order(symbol=self.symbol, side="BUY", type="MARKET", quantity=qty)
                self.in_position = True
                self.entry_price = current_price
                self.save_state_to_db()
                self.logger.info("BUY Eksekusi di ~%.8f. Tersimpan di DB.", current_price)
            except Exception:
                self.logger.error("Gagal BUY", exc_info=True)

        elif self.in_position:
            loss_threshold = self.entry_price * (1.0 - self.stop_loss_pct)

            if current_price <= loss_threshold:
                self.logger.warning(
                    "STOP LOSS! Harga=%.2f <= threshold=%.2f", current_price, loss_threshold
                )
                try:
                    self.api.create_order(symbol=self.symbol, side="SELL", type="MARKET", quantity=qty)
                    self.in_position = False
                    self.entry_price = 0.0
                    self.save_state_to_db()
                    self.logger.info("SELL (SL) Eksekusi. State di-reset.")
                except Exception:
                    self.logger.error("Gagal SELL (SL)", exc_info=True)
                return

            if current_rsi > self.overbought:
                self.logger.info("Sinyal SELL! RSI=%.2f > %d.", current_rsi, self.overbought)
                try:
                    self.api.create_order(symbol=self.symbol, side="SELL", type="MARKET", quantity=qty)
                    self.in_position = False
                    self.entry_price = 0.0
                    self.save_state_to_db()
                    self.logger.info("SELL (TP) Eksekusi. State di-reset.")
                except Exception:
                    self.logger.error("Gagal SELL", exc_info=True)

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

        if is_closed:
            self.klines_data.append(close_price)
            if len(self.klines_data) > 500:
                self.klines_data.pop(0)

        temp_closes = list(self.klines_data) + [close_price]
        current_rsi = self.calculate_rsi(temp_closes)
        current_ema = self.calculate_ema(temp_closes)

        if current_rsi is not None and current_ema is not None:
            if is_closed:
                self.logger.info(
                    "[%s] Harga: %.2f | RSI: %.2f | EMA%d: %.2f",
                    self.symbol, close_price, current_rsi, self.ema_period, current_ema,
                )
            self.execute_logic(close_price, current_rsi, current_ema)

    def on_error(self, ws, error):
        self.logger.error("WebSocket Error: %s", error)

    def on_close(self, ws, close_status_code, close_msg):
        self.logger.warning("WebSocket Terputus. Auto-reconnect dalam 5 detik...")
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
                loss_threshold = self.entry_price * (1.0 - self.stop_loss_pct) if self.in_position else 0.0

                if self.in_position and current_price <= loss_threshold:
                    self.logger.warning(
                        "SL-fallback trigger! Harga=%.2f <= %.2f", current_price, loss_threshold
                    )
                    try:
                        self.api.create_order(
                            symbol=self.symbol, side="SELL", type="MARKET", quantity=qty
                        )
                        self.in_position = False
                        self.entry_price = 0.0
                        self.save_state_to_db()
                        self.logger.info("SELL (SL-fallback) Eksekusi. State di-reset.")
                    except Exception:
                        self.logger.error("Gagal SELL (SL-fallback)", exc_info=True)

                # Also re-check TP via RSI snapshot every N seconds
                elif self.in_position:
                    try:
                        klines = self.api.get_klines(
                            self.symbol, self.timeframe, limit=max(self.rsi_period + 1, 50)
                        )
                        closes = [float(k[4]) for k in klines]
                        rsi = self.calculate_rsi(closes)
                        if rsi is not None and rsi > self.overbought:
                            self.logger.info("TP-fallback! RSI=%.2f > %d", rsi, self.overbought)
                            try:
                                self.api.create_order(
                                    symbol=self.symbol, side="SELL", type="MARKET", quantity=qty
                                )
                                self.in_position = False
                                self.entry_price = 0.0
                                self.save_state_to_db()
                                self.logger.info("SELL (TP-fallback) Eksekusi. State di-reset.")
                            except Exception:
                                self.logger.error("Gagal SELL (TP-fallback)", exc_info=True)
                    except Exception:
                        self.logger.debug("Skipping TP fallback check", exc_info=True)

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
