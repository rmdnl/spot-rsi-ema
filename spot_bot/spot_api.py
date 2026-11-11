import os
import time
import hmac
import hashlib
import requests
import logging
from urllib.parse import urlencode

class SpotAPI:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        
        env = os.getenv("ENVIRONMENT", "testnet").lower()
        if env == "production":
            self.base_url = "https://api.binance.com"
        else:
            self.base_url = "https://testnet.binance.vision"
            logging.info("Menggunakan Binance Spot Testnet")

        self.session = requests.Session()
        self.session.headers.update({
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/json"
        })

    def _generate_signature(self, params):
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _request(self, method, endpoint, params=None, signed=False):
        url = self.base_url + endpoint
        
        if params is None:
            params = {}

        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['recvWindow'] = 5000 
            params['signature'] = self._generate_signature(params)

        try:
            if method.upper() == 'GET':
                response = self.session.get(url, params=params)
            elif method.upper() == 'POST':
                response = self.session.post(url, params=params)
            else:
                raise ValueError(f"Method HTTP tidak didukung: {method}")

            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as err:
            logging.error(f"HTTP Error: {err} | Response: {response.text}")
            raise
        except Exception as err:
            logging.error(f"Request Error: {err}")
            raise

    def get_klines(self, symbol, interval, limit=100):
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }
        return self._request('GET', '/api/v3/klines', params=params, signed=False)

    def create_order(self, symbol, side, type, quantity, price=None):
        params = {
            'symbol': symbol,
            'side': side,
            'type': type,
            'quantity': quantity
        }
        if price and type == 'LIMIT':
            params['price'] = price
            params['timeInForce'] = 'GTC'
            
        return self._request('POST', '/api/v3/order', params=params, signed=True)