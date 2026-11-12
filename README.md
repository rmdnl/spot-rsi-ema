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
- **Thread-Safe State**: Akses ke状态 posisi (`in_position`, `entry_price`) dilindungi dengan `threading.Lock()` untuk mencegah race condition antara thread WSS dan fallback.
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
| `SYMBOL` | `BTCUSDT` | Pasangan trading (contoh: `ETHUSDT`, `BNBUSDT`) |
| `TRADE_QTY` | `0.005` | Jumlah koin per order (pastikan nilai > $10 setelah dikalikan harga) |
| `TIMEFRAME` | `15m` | Interval candlestick (1m, 5m, 15m, 1h, 4h, 1d) |
| `RSI_PERIOD` | `14` | Periode perhitungan RSI |
| `EMA_PERIOD` | `200` | Periode perhitungan EMA |
| `OVERSOLD` | `30` | Threshold RSI untuk sinyal Buy |
| `OVERBOUGHT` | `70` | Threshold RSI untuk sinyal Sell/TP |
| `STOP_LOSS_PCT` | `0.02` | Persentase Stop Loss (2% = 0.02) |
| `DB_PATH` | `binance_bot.db` | Path database SQLite untuk state recovery |
| `API_TIMEOUT` | `10` | Timeout koneksi REST API dalam detik |
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
        ┌─────────────┼──────────────┐
        |             |              |
   Harga ≤ SL      RSI >         WS disconnected
   (2%) OVERBOUGHT    (70)
        |             |              |
      SELL          SELL          Fallback SL/TP
                     |           check every 30s
                   State reset
```

## 📊 Logging

Bot mencatat semua aktivitas ke stdout menggunakan format:

```
2026-11-12 14:30:00,123 - RSIEngine - INFO - [BTCUSDT] Harga: 43250.00 | RSI: 28.50 | EMA200: 42100.00
2026-11-12 14:30:00,456 - RSIEngine - INFO - 🔥 Sinyal BUY (RSI=28.50 & Harga > EMA).
```

Level logging tersedia: `DEBUG`, `INFO`, `WARNING`, `ERROR`.

## ⚠️ Peringatan Penting

1. **Gunakan Testnet Dulu**: Selalu uji strategi di testnet sebelum menggunakan dana riil.
2. **Keamanan API Key**: Jangan pernah commits `.env` ke repository. Gunakan `.gitignore` yang sudah dikonfigurasi.
3. **Risk Management**: Sesuaikan `STOP_LOSS_PCT` dan `TRADE_QTY` dengan profil risiko Anda.
4. **Monitor Bot**: Bot ini tidak memiliki alerting. Pantau log secara berkala.
5. **WebSocket Disconnect**: Bot memiliki mekanisme reconnect otomatis, namun pastikan koneksi internet stabil.

## 🔧 Troubleshooting

| Masalah | Penyebab | Solusi |
|---------|----------|--------|
| Error "API Key atau Secret tidak ditemukan" | `.env` belum dibuat/diisi | Copy `.env.example` jadi `.env` dan isi kredensial |
| Order ditolak "MIN_NOTIONAL" | `TRADE_QTY * price` < batas minimum Binance | Naikkan `TRADE_QTY` atau pilih koin dengan harga lebih tinggi |
| WebSocket sering disconnect | Koneksi tidak stabil / firewall | Tidak ada masalah kritis; bot auto-reconnect |
| Double sell setelah restart | State DB tidak sinkron dengan exchange | Restart bot, cek posisi riil di dashboard Binance |
| Bot tidak eksekusi BUY | RSI > OVERSOLD atau Harga < EMA | Tunggu kondisi terpenuhi, atau ubah threshold di `.env` |

## 🛡️ Keamanan

- API Key **tidak pernah** dicetak ke log (logging aman dari kebocoran kredensial)
- `.env` ada di `.gitignore` — jangan pernah commit kredensial
- Gunakan API key dengan izin **Trading Only** (nonaktifkan Withdraw)
- Testnet disarankan untuk pengembangan awal

## 📄 Lisensi

[MIT License](LICENSE)

---

Dibuat dengan ❤️ untuk komunitas crypto trader Indonesia.
