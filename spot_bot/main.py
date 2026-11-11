import os
import sys
import logging
from dotenv import load_dotenv

# Menambahkan root folder ke sys.path agar bisa import spot_bot
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spot_bot.spot_api import SpotAPI 
from spot_bot.rsi_engine import RSIEngine 

def main():
    load_dotenv()
    
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    
    # Setup logger dasar
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    if not api_key or not api_secret:
        logging.error("API Key atau Secret tidak ditemukan di file .env")
        logging.error("Silakan copy .env.example menjadi .env dan isi kredensial Anda.")
        return

    # Inisialisasi API Client
    api_client = SpotAPI(api_key=api_key, api_secret=api_secret)
    
    logging.info("=========================================")
    logging.info("   MEMULAI BINANCE RSI SPOT BOT          ")
    logging.info(f"   Environment: {os.getenv('ENVIRONMENT', 'testnet').upper()}")
    logging.info("=========================================")

    # Inisialisasi Bot RSI
    bot = RSIEngine(
        api_client=api_client, 
        symbol="BTCUSDT",           # Pasangan koin
        trade_qty=0.005,            # Pastikan nilai ini > $10 (MIN_NOTIONAL)
        timeframe='15m',            
        rsi_period=14,              
        ema_period=200,             
        oversold=30,                
        overbought=70,              
        stop_loss_pct=0.02,         # SL 2%
        db_path='binance_bot.db'    
    )
    
    try:
        bot.run()
    except KeyboardInterrupt:
        logging.info("Bot dihentikan secara manual (Ctrl+C).")
    except Exception as e:
        logging.error(f"Bot berhenti karena error fatal: {e}")

if __name__ == "__main__":
    main()