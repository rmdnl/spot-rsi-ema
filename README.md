# Binance RSI Spot Bot 🚀

Bot trading otomatis untuk pasar **Binance Spot** yang menggunakan indikator **RSI (Relative Strength Index)** untuk menentukan titik masuk (*entry*) dan keluar (*exit*), dipadukan dengan **EMA (Exponential Moving Average)** sebagai filter tren dan manajemen risiko otomatis.

Bot ini dirancang agar ringan, responsif (menggunakan WebSocket), dan aman dari "amnesia" jika terjadi *server crash* (menggunakan SQLite).

## ✨ Fitur Utama

- **Strategi RSI + EMA**: Mengeksekusi *Buy* saat harga *Oversold* (RSI < 30) **DAN** tren sedang naik (Harga > EMA 200). Melakukan *Sell/Take Profit* saat harga *Overbought* (RSI > 70).
- **Stop Loss Otomatis**: Dilengkapi dengan fitur *Cut Loss* otomatis (default 2%) untuk melindungi modal saat pasar tiba-tiba anjlok (*dump*).
- **Real-Time WebSocket**: Menggunakan *Binance WebSocket Stream* untuk mendapatkan pembaruan harga setiap detik tanpa membebani *Rate Limit* REST API.
- **State Recovery (SQLite)**: Mengingat status *trading* (apakah sedang memegang posisi koin dan berapa harga belinya) ke dalam database lokal `binance_bot.db`. Bot tidak akan kehilangan arah meski di-*restart*.
- **Auto Precision Filter**: Otomatis menyesuaikan harga dan jumlah order (*Lot Size* & *Tick Size*) dengan aturan ketat Binance agar order tidak ditolak (termasuk *Min Notional*).
- **SL Fallback Thread**: Thread terpisah yang memeriksa SL/TP via REST API setiap interval tertentu sebagai cadangan apabila WebSocket terputus.
- **Thread-Safe State**: Akses ke posisi (`in_position`, `entry_price`) dilindungi dengan `threading.Lock()` untuk mencegah race condition antara thread WSS dan fallback.
- **Pending Sell Guard**: Mencegah double-sell dengan menandai order jual yang sedang diproses.
- **Order Confirmation Loop**: Menunggu konfirmasi fill dari Binance (`/api/v3/orders`) sebelum set posisi, mencegah state corruption jika order ditolak.
- **USDT Balance Check**: Cek saldo USDT pre-flight sebelum submit BUY, menghindari rejection insufficient funds.
- **Max Drawdown Cap**: Menutup posisi otomatis jika kerugian melebihi threshold (default 10%).
- **Partial Take Profit**: Menjual separuh posisi di target profit pertama, sisanya mengikuti trailing stop.
- **Trailing Stop**: Stop dinamis yang mengikuti harga tertinggi sejak entry, lock profit saat koreksi.
- **Exchange Reconciliation**: Memverifikasi state DB dengan open orders riil di exchange setelah restart.
- **Rate Limiter**: Token-bucket limiter mencegah ban dari Binance (1200 req/min).
- **Multi-Symbol Support**: Bisa trading banyak pasangan sekaligus (`SYMBOLS=BTCUSDT,ETHUSDT`).
- **Testnet Ready**: Mendukung mode `testnet` untuk pengujian strategi dengan uang virtual sebelum terjun ke pasar riil (`production`).

## ⚠️ Disclaimer

Bot ini disediakan **SEBAGAIMANA ADANYA** tanpa jaminan apa pun. Penulis tidak bertanggung jawab atas kerugian finansial yang mungkin timbul dari penggunaannya. **Selalu uji di testnet terlebih dahulu** sebelum menggunakan dana riil. Trading cryptocurrency memiliki risiko tinggi.

## 📂 Struktur Direktori

```text
├── .env                  # File kredensial API (Dibuat manual dari .env.example)
├── .env.example          # Template konfigurasi environment
├── .gitignore            # File yang diabaikan Git
├── requirements.txt      # Daftar dependensi Python
├── README.md             # Dokumentasi ini
└── spot_bot/
    ├── __init__.py
    ├── main.py           # Entry point utama untuk menjalankan bot
    ├── rsi_engine.py     # Logika utama (Indikator, WSS, Order, Database, Fallback)
    └── spot_api.py       # Klien untuk REST API Binance
```

## 🚀 Instalasi

### 1. Clone Repository

```bash
git clone https://github.com/rmdnl/spot-rsi-ema.git
cd spot-rsi-ema
```

### 2. Buat Virtual Environment (disarankan)

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

### 4. Konfigurasi Environment

```bash
cp .env.example .env
```

Edit file `.env` dan isi kredensial API Binance kamu:

```ini
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here
ENVIRONMENT=testnet
```

## ⚙️ Konfigurasi Lengkap

| Variabel | Default | Deskripsi |
|----------|---------|-----------|
| `BINANCE_API_KEY` | — | API Key dari [Binance API Management](https://www.binance.com/en/my/settings/api-management) |
| `BINANCE_API_SECRET` | — | API Secret yang sesuai |
| `ENVIRONMENT` | `testnet` | `testnet` untuk uji coba, `production` untuk pasar riil |
| `SYMBOLS` | `BTCUSDT` | Pasangan trading comma-separated (contoh: `BTCUSDT,ETHUSDT,SOLUSDT`) |
| `TRADE_QTY` | `0.005` | Jumlah koin per order (pastikan nilai > $10 setelah dikalikan harga) |
| `TIMEFRAME` | `15m` | Interval candlestick (1m, 5m, 15m, 1h, 4h, 1d) |
| `RSI_PERIOD` | `14` | Periode perhitungan RSI |
| `EMA_PERIOD` | `200` | Periode perhitungan EMA |
| `OVERSOLD` | `30` | Threshold RSI untuk sinyal Buy |
| `OVERBOUGHT` | `70` | Threshold RSI untuk sinyal Sell/TP |
| `STOP_LOSS_PCT` | `0.02` | Persentase Stop Loss (2% = 0.02) |
| `MAX_DRAWDOWN_PCT` | `0.10` | Force close jika loss > 10% dari entry (proteksi black swan) |
| `USE_PARTIAL_TP` | `true` | Jual separuh posisi di TP target, sisanya trailing stop |
| `PARTIAL_TP_RATIO` | `0.5` | Rasio partial sell (0.5 = 50%) |
| `TRAILING_STOP_PCT` | `0.01` | Jarak trailing stop dari peak price (1% = 0.01) |
| `DB_PATH` | `binance_bot.db` | Path database SQLite untuk state recovery |
| `API_TIMEOUT` | `10` | Timeout koneksi REST API dalam detik |
| `ORDER_CONFIRM_TIMEOUT` | `30` | Detik polling order fill via `/api/v3/orders` (0 = disable confirmation) |
| `SL_FALLBACK_INTERVAL` | `30` | Interval pengecekan SL via REST (detik, 0 = matikan) |

### Cara Mendapatkan API Key Binance

1. Login ke akun Binance Anda
2. Buka [API Management](https://www.binance.com/en/my/settings/api-management)
3. Buat API key baru dengan nama "Trading Bot"
4. **Aktifkan hanya "Spot Trading"** (nonaktifkan Withdraw)
5. Simpan API Key dan Secret dengan aman
6. Untuk testnet, daftarkan di [Binance Spot Testnet](https://testnet.binance.vision/)

## ▶️ Menjalankan Bot

```bash
# Pastikan virtual environment aktif, lalu:
python -m spot_bot.main
```

Atau langsung:

```bash
python spot_bot/main.py
```

Bot akan:
1. Memuat konfigurasi dari file `.env`
2. Inisialisasi database SQLite
3. Mengambil informasi exchange (tick size, step size, min notional)
4. Memuat riwayat kline historis untuk inisialisasi indikator
5. Menyambungkan WebSocket untuk streaming harga real-time
6. Menjalankan fallback thread untuk pengecekan SL/TP via REST

Untuk menghentikan bot, tekan **Ctrl+C**.

## 🧪 Testing di Testnet

Sebelum menggunakan dana riil, **wajib** menguji di Binance Testnet:

1. Daftar di [Binance Spot Testnet](https://testnet.binance.vision/)
2. Dapatkan API key testnet dari dashboard
3. Set `ENVIRONMENT=testnet` di `.env`
4. Jalankan bot dan amati log output
5. Verifikasi order masuk/keluar sesuai harapan

## 🔍 Cara Kerja Strategi

```
                RSI < OVERSOLD (30)
                      AND
                Harga > EMA(200)  →  BUY (Market Order)
                      |
                      |   HOLD POSISI
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
                 Max Drawdown?
                     |
              Force Close ALL
```

**Flow Penjualan:**
1. **Stop Loss** -- harga turun 2% dari entry -> market sell seluruh posisi
2. **Take Profit** -- harga naik 4% (2x SL) DAN RSI > 70:
   - Jika `USE_PARTIAL_TP=true`: jual 50% di target, sisa ikuti trailing stop
   - Jika `USE_PARTIAL_TP=false`: jual seluruh posisi
3. **Trailing Stop** -- setiap harga baru record peak, kalau turun 1% dari peak -> sell sisa posisi
4. **Max Drawdown** -- jika loss > 10% dari entry -> force close semua
5. **Fallback** -- jika WebSocket putus, thread REST tetap cek SL tiap 30 detik

## 📊 Logging

Bot mencatat semua aktivitas ke stdout menggunakan format:

```
2026-11-12 14:30:00,123 - RSIEngine - INFO - [BTCUSDT] Harga: 43250.00 | RSI: 28.50 | EMA200: 42100.00
2026-11-12 14:30:00,456 - RSIEngine - INFO - 🔥 Sinyal BUY (RSI=28.50 & Harga > EMA).
```

Level logging tersedia: `DEBUG`, `INFO`, `WARNING`, `ERROR`.

## ⚠️ Peringatan Penting

1. **Gunakan Testnet Dulu**: Selalu uji strategi di testnet minimal 1 minggu sebelum production.
2. **Keamanan API Key**: Jangan pernah commit `.env` ke repository. `.env` sudah ada di `.gitignore`.
3. **Risk Management**: Atur `STOP_LOSS_PCT`, `MAX_DRAWDOWN_PCT`, dan `TRADE_QTY` sesuai profil risiko Anda.
4. **Monitor Bot**: Bot ini tidak memiliki alerting eksternal. Pantau log dan cek posisi secara berkala.
5. **WebSocket Disconnect**: Bot auto-reconnect, tapi fallback thread juga menyediakan SL via REST.
6. **Reconciliation**: Setelah crash/restart, bot akan otomatis sync dengan open orders di exchange.
7. **Multi-Symbol**: Jika pakai banyak koin, pastikan total modal cukup untuk semua posisi (min notional per pair).

## 🔧 Troubleshooting

| Masalah | Penyebab | Solusi |
|---------|----------|--------|
| Error "API Key atau Secret tidak ditemukan" | `.env` belum dibuat/diisi | Copy `.env.example` jadi `.env` dan isi kredensial |
| Order ditolak "MIN_NOTIONAL" | `TRADE_QTY * price` < batas minimum Binance | Naikkan `TRADE_QTY` atau pilih koin dengan harga lebih tinggi |
| WebSocket sering disconnect | Koneksi tidak stabil / firewall | Tidak ada masalah kritis; bot auto-reconnect |
| Double sell setelah restart | State DB tidak sinkron dengan exchange | Restart bot, cek posisi riil di dashboard Binance |
| Bot tidak eksekusi BUY | RSI > OVERSOLD atau Harga < EMA | Tunggu kondisi terpenuhi, atau ubah threshold di `.env` |
| Double sell setelah restart | State DB tidak sinkron dengan exchange | Bot akan auto-reconcile, atau cek manual di dashboard Binance |
| Order ditolak rate limit | Terlalu banyak request REST | Rate limiter sudah aktif; kurangi `SL_FALLBACK_INTERVAL` |

## 🛡️ Keamanan

- API Key **tidak pernah** dicetak ke log (logging aman dari kebocoran kredensial)
- `.env` ada di `.gitignore` — jangan pernah commit kredensial
- Gunakan API key dengan izin **Trading Only** (nonaktifkan Withdraw)
- Testnet disarankan untuk pengembangan awal

## 📄 Lisensi

[MIT License](LICENSE)

---

Dibuat dengan ❤️ untuk komunitas crypto trader Indonesia.
