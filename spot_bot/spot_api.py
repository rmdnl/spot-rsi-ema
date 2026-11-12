import os
import time
import hmac
import hashlib
import logging
import threading
from urllib.parse import urlencode

import requests


class RateLimiter:
    """Token-bucket rate limiter untuk mematuhi Binance API limits."""

    def __init__(self, rate_per_min: int = 1200):
        self._rate = rate_per_min
        self._bucket_size = float(rate_per_min)
        self._tokens = self._bucket_size
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._bucket_size, self._tokens + elapsed * (self._rate / 60.0))
        self._last_refill = now

    def acquire(self) -> None:
        with self._lock:
            self._refill()
            while self._tokens < 1.0:
                wait = (1.0 - self._tokens) / (self._rate / 60.0)
                time.sleep(wait)
                self._refill()
            self._tokens -= 1.0


class SpotAPI:
    def __init__(self, api_key: str, api_secret: str, timeout: int = 10):
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self._rate_limiter = RateLimiter()

        env = os.getenv("ENVIRONMENT", "testnet").lower()
        if env == "production":
            self.base_url = "https://api.binance.com"
        else:
            self.base_url = "https://testnet.binance.vision"
            logging.info("Menggunakan Binance Spot Testnet")

        self.session = requests.Session()
        self.session.headers.update({
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/json",
        })

    def _generate_signature(self, params: dict) -> str:
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _request(self, method: str, endpoint: str, params: dict | None = None, signed: bool = False) -> dict:
        url = self.base_url + endpoint
        params = params or {}
        # Throttle to respect Binance rate limits
        self._rate_limiter.acquire()

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000
            params["signature"] = self._generate_signature(params)

        try:
            resp = self.session.request(method.upper(), url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as err:
            status = getattr(err.response, "status_code", "?")
            body = err.response.text if err.response is not None else ""
            self.logger.error("HTTP %s %s: status=%s", method, endpoint, status)
            if body:
                self.logger.debug("Response body (truncated): %.200s", body)
            raise
        except requests.exceptions.Timeout:
            self.logger.error("Request timeout after %ds: %s %s", self.timeout, method, endpoint)
            raise
        except requests.exceptions.ConnectionError:
            self.logger.error("Connection error: %s %s", method, endpoint)
            raise
        except requests.exceptions.RequestException:
            raise
        except Exception:
            self.logger.error("Unexpected request error on %s %s", method, endpoint)
            raise

    @property
    def logger(self):
        return logging.getLogger("SpotAPI")

    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        return self._request(
            "GET", "/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )

    def create_order(self, symbol: str, side: str, type_: str, quantity: float, price: float | None = None) -> dict:
        params: dict = {
            "symbol": symbol,
            "side": side,
            "type": type_,
            "quantity": quantity,
        }
        if price is not None and type_ == "LIMIT":
            params["price"] = price
            params["timeInForce"] = "GTC"
        return self._request("POST", "/api/v3/order", params=params, signed=True)

    def get_open_orders(self, symbol: str) -> list:
        return self._request("GET", "/api/v3/openOrders", params={"symbol": symbol})

    def get_account_balance(self) -> dict:
        return self._request("GET", "/api/v3/account", signed=True)
