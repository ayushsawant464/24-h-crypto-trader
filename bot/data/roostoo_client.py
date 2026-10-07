import time
import hmac
import hashlib
import requests
from typing import Dict, Any, Optional
from bot.config.settings import settings
from bot.logs.logger import logger

class RoostooClient:
    """
    Authenticated client for Roostoo Mock Exchange.
    Handles HMAC SHA256 signing, timestamp drift, and exponential backoff retries.
    """
    def __init__(self, api_key: str = None, secret_key: str = None, base_url: str = None):
        self.api_key = api_key or settings.ROOSTOO_API_KEY
        self.secret_key = secret_key or settings.ROOSTOO_SECRET_KEY
        self.base_url = (base_url or settings.ROOSTOO_BASE_URL).rstrip("/")
        self.session = requests.Session()

    def _get_timestamp_ms(self) -> int:
        return int(time.time() * 1000)

    def generate_signature(self, params: Dict[str, Any]) -> str:
        """
        Sort params by key, concatenate with =, join with &, hash with HMAC SHA256.
        """
        query_string = "&".join([f"{k}={params[k]}" for k in sorted(params.keys())])
        return hmac.new(
            self.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {}

        if signed:
            payload = params if method.upper() == "GET" else data
            if payload is None:
                payload = {}
            if "timestamp" not in payload:
                payload["timestamp"] = self._get_timestamp_ms()
            
            # Stringify values for signature consistency
            str_payload = {k: str(v) for k, v in payload.items()}
            sig = self.generate_signature(str_payload)
            
            headers["RST-API-KEY"] = self.api_key
            headers["MSG-SIGNATURE"] = sig
            headers["Content-Type"] = "application/x-www-form-urlencoded"

            if method.upper() == "GET":
                params = str_payload
            else:
                data = str_payload

        backoff = 0.5
        for attempt in range(max_retries):
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    data=data,
                    timeout=10
                )
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        f"Server error {resp.status_code} on {path}. Retrying in {backoff:.1f}s (Attempt {attempt+1}/{max_retries})..."
                    )
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    logger.error(f"Roostoo API error {resp.status_code} on {path}: {resp.text}")
                    return resp.json() if resp.text else {"Success": False, "ErrMsg": resp.text}
            except (requests.exceptions.RequestException, Exception) as e:
                logger.warning(f"Network error on {path}: {e}. Retrying in {backoff:.1f}s...")
                time.sleep(backoff)
                backoff *= 2

        logger.error(f"Exceeded max retries for {path}")
        return {"Success": False, "ErrMsg": "Max retries exceeded"}

    # --- Public Endpoints ---

    def get_server_time(self) -> Dict[str, Any]:
        """GET /v3/serverTime"""
        return self._request("GET", "/v3/serverTime", signed=False)

    def get_exchange_info(self) -> Dict[str, Any]:
        """GET /v3/exchangeInfo"""
        return self._request("GET", "/v3/exchangeInfo", signed=False)

    def get_ticker(self, pair: Optional[str] = None) -> Dict[str, Any]:
        """GET /v3/ticker"""
        params = {"timestamp": self._get_timestamp_ms()}
        if pair:
            params["pair"] = pair
        return self._request("GET", "/v3/ticker", params=params, signed=False)

    # --- Signed Account Endpoints ---

    def get_balance(self) -> Dict[str, Any]:
        """GET /v3/balance"""
        resp = self._request("GET", "/v3/balance", signed=True)
        if isinstance(resp, dict):
            # Normalize Roostoo API response: supports both "SpotWallet" (live API) and "Wallet" (legacy/mock)
            wallet = resp.get("SpotWallet") or resp.get("Wallet") or {}
            resp["Wallet"] = wallet
            resp["SpotWallet"] = wallet
        return resp

    def get_pending_count(self) -> Dict[str, Any]:
        """GET /v3/pending_count"""
        return self._request("GET", "/v3/pending_count", signed=True)

    # --- Signed Spot Order Endpoints ---

    def place_order(
        self,
        pair: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: Optional[float] = None
    ) -> Dict[str, Any]:
        """POST /v3/place_order"""
        data = {
            "pair": pair,
            "side": side.upper(),
            "quantity": str(quantity),
            "type": order_type.upper()
        }
        if price is not None and order_type.upper() == "LIMIT":
            data["price"] = str(price)
        return self._request("POST", "/v3/place_order", data=data, signed=True)

    def cancel_order(self, order_id: Optional[str] = None, pair: Optional[str] = None) -> Dict[str, Any]:
        """POST /v3/cancel_order"""
        data = {}
        if order_id is not None:
            data["order_id"] = str(order_id)
        if pair is not None:
            data["pair"] = pair
        return self._request("POST", "/v3/cancel_order", data=data, signed=True)

    def query_order(
        self,
        order_id: Optional[str] = None,
        pair: Optional[str] = None,
        pending_only: bool = False
    ) -> Dict[str, Any]:
        """POST /v3/query_order"""
        data = {}
        if order_id is not None:
            data["order_id"] = str(order_id)
        if pair is not None:
            data["pair"] = pair
        if pending_only:
            data["pending_only"] = "true"
        return self._request("POST", "/v3/query_order", data=data, signed=True)

    # --- Signed Short Position Endpoints (/v6) ---

    def short_open(
        self,
        pair: str,
        collateral_usd: float,
        order_type: str = "MARKET",
        price: Optional[float] = None
    ) -> Dict[str, Any]:
        """POST /v6/short_open"""
        data = {
            "pair": pair,
            "collateral": str(round(collateral_usd, 2))
        }
        if order_type.upper() == "LIMIT" and price is not None:
            data["order_type"] = "LIMIT"
            data["price"] = str(price)
        return self._request("POST", "/v6/short_open", data=data, signed=True)

    def short_close(
        self,
        pair: str,
        close_qty: Optional[float] = None,
        close_pct: Optional[float] = None
    ) -> Dict[str, Any]:
        """POST /v6/short_close"""
        data = {"pair": pair}
        if close_qty is not None:
            data["close_qty"] = str(close_qty)
        elif close_pct is not None:
            data["close_pct"] = str(close_pct)
        return self._request("POST", "/v6/short_close", data=data, signed=True)

    def get_short_positions(self) -> Dict[str, Any]:
        """GET /v6/short_positions"""
        return self._request("GET", "/v6/short_positions", signed=True)
