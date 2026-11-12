import os
import sys
import logging
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spot_bot.spot_api import SpotAPI
from spot_bot.rsi_engine import RSIEngine


def main():
    load_dotenv()

    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        logging.error("API Key atau Secret tidak ditemukan di file .env")
        logging.error("Silakan copy .env.example menjadi .env dan isi kredensial Anda.")
        return

    api_client = SpotAPI(
        api_key=api_key,
        api_secret=api_secret,
        timeout=int(os.getenv("API_TIMEOUT", "10")),
    )

    bot = RSIEngine(
        api_client=api_client,
        symbol=os.getenv("SYMBOL", "BTCUSDT"),
        trade_qty=float(os.getenv("TRADE_QTY", "0.005")),
        timeframe=os.getenv("TIMEFRAME", "15m"),
        rsi_period=int(os.getenv("RSI_PERIOD", "14")),
        ema_period=int(os.getenv("EMA_PERIOD", "200")),
        oversold=int(os.getenv("OVERSOLD", "30")),
        overbought=int(os.getenv("OVERBOUGHT", "70")),
        stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.02")),
        db_path=os.getenv("DB_PATH", "binance_bot.db"),
        api_timeout=int(os.getenv("API_TIMEOUT", "10")),
        sl_fallback_interval=int(os.getenv("SL_FALLBACK_INTERVAL", "30")),
    )

    try:
        bot.start_sl_fallback()
        bot.run()
    except KeyboardInterrupt:
        bot.shutdown()
        logging.info("Bot dihentikan secara manual (Ctrl+C).")
    except Exception as e:
        bot.shutdown()
        logging.error("Bot berhenti karena error fatal: %s", str(e))


if __name__ == "__main__":
    main()
