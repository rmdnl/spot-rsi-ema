import time
import logging
import sqlite3
import json
from decimal import Decimal, ROUND_DOWN
import websocket 

class RSIEngine:
    def __init__(self, api_client, symbol, trade_qty, timeframe='15m', rsi_period=14, ema_period=200, oversold=30, overbought=70, stop_loss_pct=0.02, db_path='binance_bot.db'):
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
        
        self.in_position = False
        self.entry_price = 0.0
        
        self.klines_data = [] 
        
        self.price_precision = 2
        self.qty_precision = 5
        self.min_notional = 10.0

        self.logger = logging.getLogger("RSIEngine")
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        self.init_db()
        self.sync_state_from_db()
        self.fetch_exchange_info()

    def init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_state (
                    symbol TEXT PRIMARY KEY,
                    in_position BOOLEAN,
                    entry_price REAL,
                    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
            self.logger.info(f"Database diinisialisasi di {self.db_path}.")
        except Exception as e:
            self.logger.error(f"Gagal inisialisasi database: {e}")

    def sync_state_from_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT in_position, entry_price FROM bot_state WHERE symbol = ?', (self.symbol,))
            row = cursor.fetchone()
            if row:
                self.in_position = bool(row[0])
                self.entry_price = float(row[1])
                self.logger.info(f"State dipulihkan dari DB: in_position={self.in_position}, entry_price={self.entry_price}")
            else:
                self.logger.info("Tidak ada state sebelumnya di DB. Memulai dari kondisi awal.")
            conn.close()
        except Exception as e:
            self.logger.error(f"Gagal sinkronisasi state dari database: {e}")

    def save_state_to_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO bot_state (symbol, in_position, entry_price, last_update) 
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(symbol) DO UPDATE SET 
                in_position=excluded.in_position, 
                entry_price=excluded.entry_price,
                last_update=CURRENT_TIMESTAMP
            ''', (self.symbol, self.in_position, self.entry_price))
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"Gagal menyimpan state ke database: {e}")

    def fetch_exchange_info(self):
        self.logger.info("Mengambil Exchange Info...")
        try:
            info = self.api._request('GET', '/api/v3/exchangeInfo', params={'symbol': self.symbol})
            for symbol_data in info['symbols']:
                if symbol_data['symbol'] == self.symbol:
                    for filter in symbol_data['filters']:
                        if filter['filterType'] == 'PRICE_FILTER':
                            tick_size = filter['tickSize']
                            self.price_precision = tick_size.find('1') - 1 if tick_size.find('.') > 0 else 0
                        elif filter['filterType'] == 'LOT_SIZE':
                            step_size = filter['stepSize']
                            self.qty_precision = step_size.find('1') - 1 if step_size.find('.') > 0 else 0
                        elif filter['filterType'] == 'NOTIONAL':
                            self.min_notional = float(filter['minNotional'])
                    self.logger.info(f"Filter tersimpan: Price Dec={self.price_precision}, Qty Dec={self.qty_precision}")
                    break
        except Exception as e:
            self.logger.warning(f"Gagal mengambil Exchange Info: {e}")

    def format_qty(self, qty):
        q = Decimal(str(qty)).quantize(Decimal('1e-{0}'.format(self.qty_precision)), rounding=ROUND_DOWN)
        return float(q)

    def calculate_rsi(self, closes):
        if len(closes) < self.rsi_period + 1: return None
        gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
        losses = [abs(min(closes[i] - closes[i-1], 0)) for i in range(1, len(closes))]

        avg_gain = sum(gains[:self.rsi_period]) / self.rsi_period
        avg_loss = sum(losses[:self.rsi_period]) / self.rsi_period

        for i in range(self.rsi_period, len(closes) - 1):
            avg_gain = (avg_gain * (self.rsi_period - 1) + gains[i]) / self.rsi_period
            avg_loss = (avg_loss * (self.rsi_period - 1) + losses[i]) / self.rsi_period

        if avg_loss == 0: return 100.0 
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
        
    def calculate_ema(self, closes):
        if len(closes) < self.ema_period: return None
        sma = sum(closes[:self.ema_period]) / self.ema_period
        multiplier = 2 / (self.ema_period + 1)
        ema = sma
        for price in closes[self.ema_period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def execute_logic(self, current_price, current_rsi, current_ema):
        qty = self.format_qty(self.trade_qty)

        if current_rsi < self.oversold and current_price > current_ema and not self.in_position:
            if current_price * qty < self.min_notional:
                self.logger.error("Nilai order terlalu kecil (di bawah MIN_NOTIONAL Binance).")
                return

            self.logger.info(f"🔥 Sinyal BUY (RSI={current_rsi:.2f} & Harga > EMA).")
            try:
                self.api.create_order(symbol=self.symbol, side='BUY', type='MARKET', quantity=qty)
                self.in_position = True
                self.entry_price = current_price
                self.save_state_to_db()
                self.logger.info(f"✅ BUY Eksekusi di ~{current_price}. Tersimpan di DB.")
            except Exception as e:
                self.logger.error(f"❌ Gagal BUY: {e}")

        elif self.in_position:
            loss_threshold = self.entry_price * (1 - self.stop_loss_pct)
            
            if current_price <= loss_threshold:
                self.logger.warning(f"🚨 STOP LOSS (2%)! Harga: {current_price} <= {loss_threshold}.")
                try:
                    self.api.create_order(symbol=self.symbol, side='SELL', type='MARKET', quantity=qty)
                    self.in_position = False
                    self.entry_price = 0.0
                    self.save_state_to_db()
                    self.logger.info("📉 SELL (SL) Eksekusi. State di-reset.")
                except Exception as e:
                    self.logger.error(f"❌ Gagal SELL (SL): {e}")
                return
                
            if current_rsi > self.overbought:
                self.logger.info(f"💰 Sinyal SELL! RSI={current_rsi:.2f} > {self.overbought}.")
                try:
                    self.api.create_order(symbol=self.symbol, side='SELL', type='MARKET', quantity=qty)
                    self.in_position = False
                    self.entry_price = 0.0
                    self.save_state_to_db()
                    self.logger.info("✅ SELL (TP) Eksekusi. State di-reset.")
                except Exception as e:
                    self.logger.error(f"❌ Gagal SELL: {e}")

    def on_message(self, ws, message):
        data = json.loads(message)
        if 'k' in data:
            kline = data['k']
            is_kline_closed = kline['x']
            close_price = float(kline['c'])

            if is_kline_closed:
                self.klines_data.append(close_price)
                if len(self.klines_data) > 500:
                    self.klines_data.pop(0)

            temp_closes = self.klines_data + [close_price]
            current_rsi = self.calculate_rsi(temp_closes)
            current_ema = self.calculate_ema(temp_closes)

            if current_rsi is not None and current_ema is not None:
                if is_kline_closed:
                    self.logger.info(f"[{self.symbol}] Harga: {close_price:.2f} | RSI: {current_rsi:.2f} | EMA{self.ema_period}: {current_ema:.2f}")
                self.execute_logic(close_price, current_rsi, current_ema)

    def on_error(self, ws, error):
        self.logger.error(f"WebSocket Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        self.logger.warning("WebSocket Terputus. Auto-reconnect dalam 5 detik...")
        time.sleep(5)
        self.run() 

    def run(self):
        self.logger.info(f"Mengambil riwayat data {self.timeframe} sebelum menyalakan WSS...")
        limit = max(self.rsi_period * 5, self.ema_period * 2) 
        historical_klines = self.api.get_klines(symbol=self.symbol, interval=self.timeframe, limit=limit)
        self.klines_data = [float(k[4]) for k in historical_klines]

        self.logger.info(f"Menyambungkan WebSocket untuk {self.symbol}...")
        symbol_lower = self.symbol.lower()
        ws_url = f"wss://stream.binance.com:9443/ws/{symbol_lower}@kline_{self.timeframe}"
        
        ws = websocket.WebSocketApp(ws_url,
                                    on_message=self.on_message,
                                    on_error=self.on_error,
                                    on_close=self.on_close)
        ws.run_forever()