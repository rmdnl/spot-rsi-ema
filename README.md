# Binance RSI Spot Bot 🚀

Bot trading otomatis untuk pasar Binance Spot yang menggunakan indikator **RSI (Relative Strength Index)** untuk menentukan titik masuk (*entry*) dan keluar (*exit*), dipadukan dengan **EMA (Exponential Moving Average)** sebagai filter tren dan manajemen risiko otomatis.

Bot ini dirancang agar ringan, responsif (menggunakan WebSocket), dan aman dari "amnesia" jika terjadi *server crash* (menggunakan SQLite).

## ✨ Fitur Utama

- **Strategi RSI + EMA**: Mengeksekusi *Buy* saat harga *Oversold* (RSI < 30) **DAN** tren sedang naik (Harga > EMA 200). Melakukan *Sell/Take Profit* saat harga *Overbought* (RSI > 70).
- **Stop Loss Otomatis**: Dilengkapi dengan fitur *Cut Loss* otomatis (default 2%) untuk melindungi modal saat pasar tiba-tiba anjlok (*dump*).
- **Real-Time WebSocket**: Menggunakan *Binance WebSocket Stream* untuk mendapatkan pembaruan harga setiap detik tanpa membebani *Rate Limit* REST API.
- **State Recovery (SQLite)**: Mengingat status *trading* (apakah sedang memegang posisi koin dan berapa harga belinya) ke dalam database lokal `binance_bot.db`. Bot tidak akan kehilangan arah meski di-*restart*.
- **Auto Precision Filter**: Otomatis menyesuaikan harga dan jumlah order (*Lot Size* & *Tick Size*) dengan aturan ketat Binance agar order tidak ditolak (termasuk *Min Notional*).
- **Testnet Ready**: Mendukung mode `testnet` untuk pengujian strategi dengan uang virtual sebelum terjun ke pasar riil (`production`).

## 📂 Struktur Direktori

```text
binance-spot-bot/
├── .env                  # File kredensial API (Dibuat manual dari .env.example)
├── .env.example          # Template konfigurasi environment
├── requirements.txt      # Daftar dependensi Python
├── README.md             # Dokumentasi ini
└── spot_bot/
    ├── __init__.py
    ├── main.py           # Entry point utama untuk menjalankan bot
    ├── rsi_engine.py     # Logika utama (Indikator, WSS, Order, Database)
    └── spot_api.py       # Klien untuk REST API Binance