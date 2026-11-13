# Binance RSI Spot Bot 🚀🤖

Bot trading otomatis untuk pasar **Binance Spot** yang support **dua strategi**:
- **Mode Default (1H Swing)** → EMA Ribbon Golden Cross (`8/21/55`) — profitable +46% sampai +83%/tahun
- **Mode Scalping (<1H)** → RSI + EMA filter — untuk active scalper

Gak perlu mikir manual, bot udah ngurus entry, exit, stop loss, sampe trailing stop. Ringan, cepet (WebSocket), dan gak punya amnesia (SQLite).

Jadi kalau server lo crash atau listrik mati, bot bakal inget posisi terakhir lo pas nyala lagi. *No stress, no panic.* 😎

## ✨ Fitur-Fitur Keren

- **Strategi Dual Mode**: 
  - **EMA Ribbon (default 1H)**: Buy pas golden cross (EMA_fast > EMA_med > EMA_slow), sell pas trend break. Backtest: **+46% s.d +83%** per tahun.
  - **RSI+EMA (<1H)**: Buy pas RSI oversold (RSI < 30) DAN tren naik (Harga > EMA). Best: SOLUSDT 15m = **+5.43%** dengan volume filter.
- **Stop Loss Otomatis**: Cut loss 2% auto eksekusi kalau pasar lagi *dump*. Modal lo aman, gak perlu WAGYU vibes.
- **Real-Time WebSocket**: Harga update tiap detik via Binance WebSocket Stream. Gak pusing soal rate limit REST API.
- **State Recovery (SQLite)**: Bot inget posisi lo pakai database lokal `binance_bot.db`. Restart sekalipun, tetap on point.
- **Auto Precision Filter**: Harga & jumlah order otomatis disesuaikan sama aturan Binance. Gak ada lagi drama "order ditolak karena tick size salah".
- **Order Confirmation Loop**: Baru set posisi setelah order beneran di-fill Binance. Kalau reject? Skip. Gak ada state corruption.
- **USDT Balance Check**: Cek saldo USDT dulu sebelum buy. Kalau minimoney? Bot skip, bukan malah error.
- **SL Fallback Thread**: Kalau WebSocket putus, thread REST tetep jaga SL/TP setiap interval tertentu. Double insurance.
- **Thread-Safe State**: Akses posisi dilindungi `threading.Lock()`. Gak ada race condition antar thread.
- **Pending Sell Guard**: Mencegah double-sell pas order jual lagi diproses. Anti amburadul.
- **Max Drawdown Cap**: Stop auto-close kalau kerugian tembus threshold (default 10%). Proteksi black swan event.
- **Partial Take Profit**: Jual separuh posisi pas target profit pertama tercapai, sisanya ikutan trailing stop. Best of both worlds.
- **Trailing Stop**: Stop dinamis ngikutin harga tertinggi sejak entry. Lock profit pas koreksi.
- **Exchange Reconciliation**: Setelah restart, bot cek posisi DB vs open orders riil di exchange. Sinkronisasi otomatis.
- **Rate Limiter**: Token-bucket limiter cegah ban dari Binance (1200 req/min). Sopan santun sama API mereka.
- **Multi-Symbol Support**: Bisa trading banyak pasangan sekaligus (`SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT`). Not stuck with one.
- **Telegram Notifications**: Notifikasi real-time ke HP lo buat setiap BUY, SELL, disconnect, error, dll. *Always in the know.*
- **Testnet Ready**: Mode testnet buat latihan dulu pake uang virtual sebelum terjun ke pasar beneran. Safety first!

## ⚠️ Disclaimer (Read This Bro)

Bot ini disediakan **ADA ADANYA** tanpa jaminan apapun. Penulis gak tanggung jawab kalau lo rugi karena bot ini. Trading crypto itu *high risk*, jadi selalu uji di testnet minimal 1-2 minggu sebelum pakai uang beneran. *DYOR* ya, bro. 🙏

## 📂 Struktur Folder

```text
├── .env                  # File rahasia API (buat sendiri dari .env.example)
├── .env.example          # Template configs
├── .gitignore            # Biar kredensial gak kebaca public
├── requirements.txt      # Dependensi Python
├── README.md             # Dokumentasi ini (yang lo baca sekarang wkwk)
└── spot_bot/
    ├── __init__.py
    ├── main.py           # Entry point utama buat nyalain bot
    ├── rsi_engine.py     # Otaknya bot (indikator, WSS, order, DB, fallback)
    ├── spot_api.py       # Klien buat ngobrol sama API Binance
    └── telegram_notif.py # Module notifikasi Telegram
```

## 🚀 Cara Install

### 1. Clone Repo Ini

```bash
git clone https://github.com/rmdnl/spot-rsi-ema.git
cd spot-rsi-ema
```

### 2. Buat Virtual Environment (Recommended)

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate
```

### 3. Install Dependensi

```bash
pip install -r requirements.txt
```

### 4. Setup `.env`

```bash
cp .env.example .env
```

terus edit `.env` dan isi API key Binance lo. Jangan lupa set `ENVIRONMENT=testnet` dulu ya!

## ⚙️ Konfigurasi Lengkap

| Variabel | Default | Gue Jelasin Apa |
|----------|---------|----------------|
| `BINANCE_API_KEY` | — | API Key dari [Binance API Management](https://www.binance.com/en/my/settings/api-management) |
| `BINANCE_API_SECRET` | — | API Secret yang sesuai |
| `ENVIRONMENT` | `testnet` | `testnet` = playground, `production` = uang beneran |
| `SYMBOLS` | `SOLUSDT,NEARUSDT` | **Default 1H swing** — backtest terbaik. Scalping <1H pakai SOLUSDT aja |
| `TRADE_QTY` | `0.005` | Jumlah koin per order (harusnya nilainya > $10 pas dikali harga) |
| `TIMEFRAME` | `1h` | **Default: 1H** = EMA Ribbon Golden Cross swing trading. <1h otomatis RSI mode |
| `RSI_PERIOD` | `14` | Periode hitung RSI (hanya dipakai di TIMEFRAME < 1h / mode RSI) |
| `EMA_PERIOD` | `200` | Periode hitung EMA single (hanya dipakai di TIMEFRAME < 1h / mode RSI) |
| `OVERSOLD` | `30` | Threshold RSI untuk sinyal Buy (hanya mode RSI / <1h) |
| `OVERBOUGHT` | `70` | Threshold RSI untuk sinyal Sell/TP (hanya mode RSI / <1h) |
| `STOP_LOSS_PCT` | `0.02` | Persentase Stop Loss (2% = 0.02) |
| `MAX_DRAWDOWN_PCT` | `0.10` | Force close kalau loss > 10% dari entry (anti black swan) |
| `USE_PARTIAL_TP` | `true` | Jual separuh posisi pas TP target, sisanya ikutan trailing stop |
| `PARTIAL_TP_RATIO` | `0.5` | Rasio partial sell (0.5 = 50%) |
| `TRAILING_STOP_PCT` | `0.01` | Jarak trailing stop dari peak price (1% = 0.01) |
| `DB_PATH` | `binance_bot.db` | Path database SQLite |
| `API_TIMEOUT` | `10` | Timeout koneksi REST API dalam detik |
| `ORDER_CONFIRM_TIMEOUT` | `30` | Detik polling order fill via `/api/v3/order` |
| `SL_FALLBACK_INTERVAL` | `30` | Interval cek SL via REST (detik, 0 = disable) |
| `TELEGRAM_BOT_TOKEN` | — | Token bot dari @BotFather (kosong = off) |
| `TELEGRAM_CHAT_IDS` | — | Chat ID tujuan (comma-sep, kosong = off) |
| `TELEGRAM_NOTIFY_ERRORS` | `true` | Kirim notif error ke Telegram |
| `VOLUME_FILTER` | `false` | Default false — otomatis disabled saat mode ema_ribbon. Aktifkan hanya untuk scalping <1h |
| `VOLUME_MULT` | `2.0` | Threshold multiplier volume spike (hanya aktif di mode RSI / <1h) |
| `EMA_TREND_FILTER` | `true` | **P1**: Hanya entry kalau EMA sedang trending naik |
| `EMA_TREND_BARS` | `5` | Jumlah bar untuk cek arah trend EMA |
| `TP_MULT_SL` | `3.0` | **P1 RECOMMENDED**: TP multiplier × SL% (3.0x = R:R 1:3, terbukti profitable) |
| `ATR_STOP_FRAC` | `0.0` | ATR-based stop (0.0 = pakai fixed %, experimental) |
| `STRATEGY_MODE` | `ema_ribbon` | **DEFAULT**: EMA Ribbon Golden Cross untuk 1H swing (+46%~+83%). Pakai `rsi` buat scalping <1H |
| `EMA_FAST` | `8` | Fast EMA — golden cross signal (optimal: 8/21/55 dari backtest) |
| `EMA_MED` | `21` | Medium EMA — trend baseline |
| `EMA_SLOW` | `55` | Slow EMA — trend confirmation |

## 🔑 Cara Nyari API Key Binance

1. Login ke akun Binance
2. Buka [API Management](https://www.binance.com/en/my/settings/api-management)
3. Klik **Create API** → kasih nama "Trading Bot"
4. **Centang cuma "Spot Trading"** (matian Withdraw, safety first!)
5. Copy API Key & Secret, simpan dengan aman
6. Buat testnet → daftar di [Binance Spot Testnet](https://testnet.binance.vision/)

## 📱 Cara Setup Telegram Bot

Bot ini bisa ngirim notifikasi ke HP lo lewat Telegram. Caranya:

1. Cari **@BotFather** di Telegram → ketik `/newbot` → kasih nama → simpan TOKEN-nya
2. Cari bot yang baru dibuat → klik **Start**
3. Buat tau Chat ID lo:
   - Kunjungi `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Cari `"from":{"id":XXXXXXX}` → angka itu Chat ID lo
   - Atau kirim pesan ke bot, terus cek URL di atas
4. Isi di `.env`:

```ini
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_IDS=123456789
# Buat multi-chat: TELEGRAM_CHAT_IDS=123456789,987654321
```

### Notifikasi yang Lo Akan Terima:
- 🟢 **BUY CONFIRMED** — harga entry, qty, order ID
- 🔴 **SELL (SL)** — harga exit, persentase loss
- 🟡 **SELL (TP)** — harga exit, profit %
- 🟠 **Partial TP / Trailing Stop** — detail sebagian posisi
- 🔴 **Max Drawdown** — alarm force close
- ⚡ **WS Disconnect** — peringatan reconnect
- 🔄 **Reconcile** — hasil sinkro setelah restart
- ❌ **Error** — semua exception

## ▶️ Cara Nyalain Bot

```bash
# Pastikan virtual environment aktif, terus:
python -m spot_bot.main
```

atau langsung:

```bash
python spot_bot/main.py
```

Bot bakal jalan dan mulai:
1. Loading config dari `.env`
2. Init database SQLite
3. Ambil info exchange (tick size, step size, min notional)
4. Load riwayat kline buat inisialisasi indikator
5. Sambung WebSocket buat streaming harga real-time
6. Jalanin fallback thread buat CEK SL/TP via REST

Stop bot: tekan **Ctrl+C**.

## 🧪 Testing di Testnet (WAJIB DULU BRO!)

Sebelum lo taro uang beneran, **wajib** testing dulu di testnet minimal 1-2 minggu:

1. Daftar di [Binance Spot Testnet](https://testnet.binance.vision/)
2. Ambil API key testnet dari dashboard mereka
3. Set `ENVIRONMENT=testnet` di `.env`
4. Jalankan bot dan pantau log output
5. Verifikasi semua order masuk/keluar sesuai harapan

Kalau sudah oke dan profit konsisten → baru deh coba production. Slow wins the race. 🐢💨

## 🔍 Logika Strategi

```
                RSI < OVERSOLD (30)
                      AND
                Harga > EMA(200)  →  BUY (Market Order)
                      |
                      |   HOLD POSISI SAMPE ADA SINYAL
                      |
        ┌─────────────┼──────────────────┐
        |             |                  |
   Harga <= SL     Harga >= TP Target   WS disconnected
   (2%)       + RSI > OVERBOUGHT          |
        |             |              Fallback SL (REST)
      SELL        ┌─────┐               check every 30s
                 | Half|  ->  Sisanya
                 |Sell |     Trailing Stop
                 └─────┘
                     |
                 Max Drawdown? (loss > 10%?)
                     |
              Force Close ALL
```

**Flow Penjualan:**
1. **Stop Loss** — harga turun 2% dari entry → market sell keseluruhan posisi
2. **Take Profit** — harga naik 4% (2x SL) DAN RSI > 70:
   - Kalau `USE_PARTIAL_TP=true`: jual 50% di target, sisa ikutan trailing stop
   - Kalau `USE_PARTIAL_TP=false`: jual semua sekaligus
3. **Trailing Stop** — tracking harga tertinggi sejak entry, kalau turun 1% dari peak → sell sisa
4. **Max Drawdown** — kalau loss > 10% dari entry → force close semua (proteksi ekstrem)
5. **Fallback** — kalau WebSocket mati, thread REST tetep cek SL tiap 30 detik

## 📊 Logging

Bot nge-log semua aktivitas ke stdout:

```
2026-11-12 14:30:00,123 - RSIEngine - INFO - [BTCUSDT] Harga: 43250.00 | RSI: 28.50 | EMA200: 42100.00
2026-11-12 14:30:00,456 - RSIEngine - INFO - 🔥 Sinyal BUY (RSI=28.50 & Harga > EMA).
```

Level logging tersedia: `DEBUG`, `INFO`, `WARNING`, `ERROR`.

## ⚠️ Hal-Hal Penting yang Harus Lo Tau

1. **Testnet dulu!** Minimal 1-2 minggu sebelum production. Jangan skip ini.
2. **Keamanan API Key** — jangan pernah commit `.env` ke repo. Sudah ada di `.gitignore`.
3. **Risk Management** — atur `STOP_LOSS_PCT`, `MAX_DRAWDOWN_PCT`, dan `TRADE_QTY` sesuai mental lo. Jangan asal-asalan.
4. **Monitor Bot** — bot ini gak punya alerting eksternal selain Telegram. Pantau log secara berkala.
5. **WebSocket Disconnect** — bot auto-reconnect, tapi fallback thread juga jaga SL via REST.
6. **Reconciliation** — setelah crash/restart, bot auto-sync sama exchange. Tapi tetap cek manual dulu.
7. **Multi-Symbol** — kalau pakai banyak koin, pastikan modal cukup buat semua posisi (min notional per pair).

## 🔧 Troubleshooting (Problem & Solution)

| Masalah | Penyebab | Solusi |
|---------|----------|--------|
| Error "API Key atau Secret tidak ditemukan" | `.env` belum dibuat/diisi | Copy `.env.example` jadi `.env`, terus isi |
| Order ditolak "MIN_NOTIONAL" | `TRADE_QTY * price` < batas minimum Binance | Naikin `TRADE_QTY` atau pilih koin yang harganya lebih mahal |
| WebSocket sering disconnect | Koneksi不稳定 / firewall | Gak masalah kritis, bot auto-reconnect aja |
| Double sell setelah restart | State DB gak sinkron dengan exchange | Bot auto-reconcile pas start, atau cek manual di dashboard Binance |
| Bot gak eksekusi BUY | RSI > OVERSOLD atau Harga < EMA | Tunggu kondisi terpenuhi, atau ubah threshold di `.env` |
| Order ditolak rate limit | Terlalu banyak request REST | Rate limiter udah aktif; kurangin `SL_FALLBACK_INTERVAL` |
| Gak nemu chat ID Telegram | Gak tau cara cari | Kunjungi `https://api.telegram.org/bot<TOKEN>/getUpdates` |

## 🛡️ Keamanan

- API Key **nggak pernah** dicetak ke log (aman dari kebocoran)
- `.env` ada di `.gitignore` — jangan pernah commit
- Pake API key dengan izin **Trading Only** (disable Withdraw)
- Testnet recommended banget buat development awal
- Bot sudah pake signature HMAC SHA-256 yang benar sesuai standar Binance

## 📄 Lisensi

[MIT License](LICENSE)

---

Dibuat dengan ❤️ + ☕ + banyak debugging buat komunitas crypto trader Indonesia. Happy trading! 🚀📈
