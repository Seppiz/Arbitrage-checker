import math
import time
import uuid
from typing import Dict, Optional
import httpx
from models import OrderRequest, OrderResult


# Precision mapping for coin amounts to prevent exchange stepSize rejections
DEFAULT_PRECISIONS = {
    "BTC": 6, "ETH": 5, "SOL": 4, "BNB": 4, "XRP": 2, "ADA": 2, "AVAX": 3,
    "DOT": 3, "LINK": 3, "NEAR": 3, "SUI": 2, "APT": 3, "TRX": 1, "LTC": 4,
    "BCH": 4, "ATOM": 3, "FTM": 2, "INJ": 3, "RENDER": 3, "FET": 2, "TIA": 3,
    "ARB": 2, "OP": 2, "DOGE": 0, "SHIB": 0, "PEPE": 0, "TON": 3, "NOT": 0,
    "DOGS": 0, "HMSTR": 0, "CATI": 1, "FLOKI": 0, "BONK": 0, "WIF": 2, "BOME": 0,
}


from decimal import Decimal, ROUND_DOWN


def round_amount(symbol: str, amount: float) -> float:
    decimals = DEFAULT_PRECISIONS.get(symbol.upper(), 4)
    if decimals == 0:
        return float(int(amount))
    d = Decimal(str(amount)).quantize(Decimal(f"1e-{decimals}"), rounding=ROUND_DOWN)
    return float(d)


def format_amount(symbol: str, amount: float) -> str:
    decimals = DEFAULT_PRECISIONS.get(symbol.upper(), 4)
    if decimals == 0:
        return str(int(amount))
    d = Decimal(str(amount)).quantize(Decimal(f"1e-{decimals}"), rounding=ROUND_DOWN)
    return f"{d:f}"


class ExchangeClient:
    def __init__(self, name: str, api_key: str = "", dry_run: bool = True):
        self.name = name
        self.api_key = api_key
        self.dry_run = dry_run

    async def get_balance(self, client: httpx.AsyncClient, currency: str) -> float:
        raise NotImplementedError

    async def get_all_balances(self, client: httpx.AsyncClient) -> Dict[str, float]:
        raise NotImplementedError

    async def place_order(self, client: httpx.AsyncClient, req: OrderRequest) -> OrderResult:
        raise NotImplementedError


class NobitexClient(ExchangeClient):
    def __init__(self, token: str = "", api_key: str = "", secret_key: str = "", dry_run: bool = True):
        effective_key = api_key or token
        super().__init__("nobitex", api_key=effective_key, dry_run=dry_run)
        self.base_url = "https://apiv2.nobitex.ir"
        self.token = token
        self.secret_key = secret_key
        self._priv_key = None
        if self.secret_key:
            try:
                import base64
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                priv_bytes = base64.urlsafe_b64decode(self.secret_key)
                self._priv_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
            except Exception:
                pass

    def _headers(self, method: str = "GET", full_path: str = "", body: str = "") -> dict:
        if self._priv_key and self.api_key:
            import base64
            timestamp = str(int(time.time()))
            payload = f"{timestamp}{method.upper()}{full_path}{body}".encode()
            sig = self._priv_key.sign(payload)
            sig_b64 = base64.urlsafe_b64encode(sig).decode()
            return {
                "Nobitex-Key": self.api_key,
                "Nobitex-Signature": sig_b64,
                "Nobitex-Timestamp": timestamp,
                "Content-Type": "application/json",
                "User-Agent": "ArbitBot/CryptoArbitrage-2.0",
            }
        return {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "ArbitBot/CryptoArbitrage-2.0",
        }

    async def get_balance(self, client: httpx.AsyncClient, currency: str) -> float:
        if not self.api_key:
            return 1000.0  # Simulated paper balance

        path = "/users/wallets/list"
        url = f"{self.base_url}{path}"
        headers = self._headers(method="POST", full_path=path, body="")
        res = await client.post(url, headers=headers, timeout=5.0)
        res.raise_for_status()
        data = res.json()

        wallets = data.get("wallets", {})
        if isinstance(wallets, dict):
            for w in wallets.values():
                if w.get("currency", "").lower() == currency.lower():
                    return float(w.get("balance", 0.0)) - float(w.get("blocked", 0.0))
        elif isinstance(wallets, list):
            for w in wallets:
                if w.get("currency", "").lower() == currency.lower():
                    return float(w.get("balance", 0.0)) - float(w.get("blocked", 0.0))
        return 0.0

    async def get_all_balances(self, client: httpx.AsyncClient) -> Dict[str, float]:
        if not self.api_key:
            return {"USDT": 1000.0, "BTC": 0.05, "ETH": 0.5}

        path = "/users/wallets/list"
        url = f"{self.base_url}{path}"
        headers = self._headers(method="POST", full_path=path, body="")
        res = await client.post(url, headers=headers, timeout=5.0)
        res.raise_for_status()
        data = res.json()
        balances = {}
        wallets = data.get("wallets", {})
        iterator = wallets.values() if isinstance(wallets, dict) else wallets
        for w in iterator:
            curr = w.get("currency", "").upper()
            avail = float(w.get("balance", 0.0)) - float(w.get("blocked", 0.0))
            if avail > 0:
                balances[curr] = avail
        return balances

    async def place_order(self, client: httpx.AsyncClient, req: OrderRequest) -> OrderResult:
        rounded_qty = round_amount(req.symbol, req.amount)
        now = time.time()

        if self.dry_run or not self.api_key:
            return OrderResult(
                success=True,
                order_id=f"SIM-NOB-{uuid.uuid4().hex[:8]}",
                exchange=self.name,
                symbol=req.symbol,
                side=req.side,
                price=req.price,
                amount=rounded_qty,
                filled_amount=rounded_qty,
                is_simulated=True,
                timestamp=now,
            )

        path = "/market/orders/add"
        url = f"{self.base_url}{path}"
        payload = {
            "type": req.side.lower(),
            "execution": "market" if req.order_type == "market" else "limit",
            "srcCurrency": req.symbol.lower(),
            "dstCurrency": "usdt",
            "amount": str(rounded_qty),
        }
        if req.order_type == "limit":
            payload["price"] = str(int(req.price) if req.price >= 100 else f"{req.price:.6f}")

        import json
        body = json.dumps(payload, separators=(',', ':'))
        headers = self._headers(method="POST", full_path=path, body=body)

        try:
            res = await client.post(url, content=body, headers=headers, timeout=5.0)
            data = res.json()
            if data.get("status") == "ok":
                order_info = data.get("order", {})
                return OrderResult(
                    success=True,
                    order_id=str(order_info.get("id", "")),
                    exchange=self.name,
                    symbol=req.symbol,
                    side=req.side,
                    price=req.price,
                    amount=rounded_qty,
                    filled_amount=float(order_info.get("matchedAmount", rounded_qty)),
                    timestamp=now,
                )
            return OrderResult(
                success=False,
                order_id="",
                exchange=self.name,
                symbol=req.symbol,
                side=req.side,
                price=req.price,
                amount=rounded_qty,
                error=data.get("message", str(data)),
                timestamp=now,
            )
        except Exception as e:
            return OrderResult(
                success=False,
                order_id="",
                exchange=self.name,
                symbol=req.symbol,
                side=req.side,
                price=req.price,
                amount=rounded_qty,
                error=str(e),
                timestamp=now,
            )


class BitpinClient(ExchangeClient):
    def __init__(self, api_key: str = "", dry_run: bool = True):
        super().__init__("bitpin", api_key=api_key, dry_run=dry_run)
        self.base_url = "https://api.bitpin.org"

    def _headers(self) -> dict:
        return {
            "api-key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "ArbitBot/CryptoArbitrage-2.0",
        }

    async def get_balance(self, client: httpx.AsyncClient, currency: str) -> float:
        if not self.api_key:
            return 1000.0

        url = f"{self.base_url}/v1/wlt/wallets/"
        res = await client.get(url, headers=self._headers(), timeout=5.0)
        res.raise_for_status()
        data = res.json()

        results = data.get("results", data) if isinstance(data, dict) else data
        if isinstance(results, list):
            for w in results:
                curr = w.get("currency", {}).get("code", "") if isinstance(w.get("currency"), dict) else str(w.get("currency", ""))
                if curr.lower() == currency.lower():
                    return float(w.get("balance", 0.0)) - float(w.get("frozen_balance", 0.0))
        return 0.0

    async def get_all_balances(self, client: httpx.AsyncClient) -> Dict[str, float]:
        if not self.api_key:
            return {"USDT": 1000.0, "BTC": 0.05, "ETH": 0.5}

        url = f"{self.base_url}/v1/wlt/wallets/"
        res = await client.get(url, headers=self._headers(), timeout=5.0)
        res.raise_for_status()
        data = res.json()
        balances = {}
        results = data.get("results", data) if isinstance(data, dict) else data
        if isinstance(results, list):
            for w in results:
                curr = w.get("currency", {}).get("code", "") if isinstance(w.get("currency"), dict) else str(w.get("currency", ""))
                avail = float(w.get("balance", 0.0)) - float(w.get("frozen_balance", 0.0))
                if curr and avail > 0:
                    balances[curr.upper()] = avail
        return balances

    async def place_order(self, client: httpx.AsyncClient, req: OrderRequest) -> OrderResult:
        rounded_qty = round_amount(req.symbol, req.amount)
        now = time.time()

        if self.dry_run or not self.api_key:
            return OrderResult(
                success=True,
                order_id=f"SIM-BP-{uuid.uuid4().hex[:8]}",
                exchange=self.name,
                symbol=req.symbol,
                side=req.side,
                price=req.price,
                amount=rounded_qty,
                filled_amount=rounded_qty,
                is_simulated=True,
                timestamp=now,
            )

        url = f"{self.base_url}/v1/odr/orders/"
        payload = {
            "market": f"{req.symbol.upper()}_USDT",
            "type": "market" if req.order_type == "market" else "limit",
            "side": req.side.lower(),
            "amount": str(rounded_qty),
        }
        if req.order_type == "limit":
            payload["price"] = str(req.price)

        try:
            res = await client.post(url, json=payload, headers=self._headers(), timeout=5.0)
            data = res.json()
            if res.status_code in (200, 201) and "id" in data:
                return OrderResult(
                    success=True,
                    order_id=str(data.get("id")),
                    exchange=self.name,
                    symbol=req.symbol,
                    side=req.side,
                    price=req.price,
                    amount=rounded_qty,
                    filled_amount=float(data.get("filled_amount", rounded_qty)),
                    timestamp=now,
                )
            return OrderResult(
                success=False,
                order_id="",
                exchange=self.name,
                symbol=req.symbol,
                side=req.side,
                price=req.price,
                amount=rounded_qty,
                error=str(data),
                timestamp=now,
            )
        except Exception as e:
            return OrderResult(
                success=False,
                order_id="",
                exchange=self.name,
                symbol=req.symbol,
                side=req.side,
                price=req.price,
                amount=rounded_qty,
                error=str(e),
                timestamp=now,
            )


class WallexClient(ExchangeClient):
    def __init__(self, api_key: str = "", dry_run: bool = True):
        super().__init__("wallex", api_key=api_key, dry_run=dry_run)
        self.base_url = "https://api.wallex.ir"

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "ArbitBot/CryptoArbitrage-2.0",
        }

    async def get_balance(self, client: httpx.AsyncClient, currency: str) -> float:
        if not self.api_key:
            return 1000.0

        url = f"{self.base_url}/v1/account/balances"
        res = await client.get(url, headers=self._headers(), timeout=5.0)
        res.raise_for_status()
        data = res.json()

        balances = data.get("result", {}).get("balances", {})
        c_info = balances.get(currency.upper(), {})
        val = float(c_info.get("value", 0.0))
        locked = float(c_info.get("locked", 0.0))
        return max(0.0, val - locked)

    async def get_all_balances(self, client: httpx.AsyncClient) -> Dict[str, float]:
        if not self.api_key:
            return {"USDT": 1000.0, "BTC": 0.05, "ETH": 0.5}

        url = f"{self.base_url}/v1/account/balances"
        res = await client.get(url, headers=self._headers(), timeout=5.0)
        res.raise_for_status()
        data = res.json()
        balances = {}
        items = data.get("result", {}).get("balances", {})
        for curr, c_info in items.items():
            avail = float(c_info.get("value", 0.0)) - float(c_info.get("locked", 0.0))
            if avail > 0:
                balances[curr.upper()] = avail
        return balances

    async def place_order(self, client: httpx.AsyncClient, req: OrderRequest) -> OrderResult:
        rounded_qty = round_amount(req.symbol, req.amount)
        now = time.time()

        if self.dry_run or not self.api_key:
            return OrderResult(
                success=True,
                order_id=f"SIM-WLX-{uuid.uuid4().hex[:8]}",
                exchange=self.name,
                symbol=req.symbol,
                side=req.side,
                price=req.price,
                amount=rounded_qty,
                filled_amount=rounded_qty,
                is_simulated=True,
                timestamp=now,
            )

        url = f"{self.base_url}/v1/account/orders"
        payload = {
            "symbol": f"{req.symbol.upper()}USDT",
            "type": "LIMIT" if req.order_type == "limit" else "MARKET",
            "side": req.side.upper(),
            "quantity": str(rounded_qty),
        }
        if req.order_type == "limit":
            payload["price"] = str(req.price)

        try:
            res = await client.post(url, json=payload, headers=self._headers(), timeout=5.0)
            data = res.json()
            if data.get("success") is True and "result" in data:
                result_data = data.get("result", {})
                order_id = str(result_data.get("clientOrderId") or result_data.get("orderId", uuid.uuid4().hex[:8]))
                executed_qty = float(result_data.get("executedQty", rounded_qty))
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    exchange=self.name,
                    symbol=req.symbol,
                    side=req.side,
                    price=req.price,
                    amount=rounded_qty,
                    filled_amount=executed_qty,
                    timestamp=now,
                )
            return OrderResult(
                success=False,
                order_id="",
                exchange=self.name,
                symbol=req.symbol,
                side=req.side,
                price=req.price,
                amount=rounded_qty,
                error=data.get("message", str(data)),
                timestamp=now,
            )
        except Exception as e:
            return OrderResult(
                success=False,
                order_id="",
                exchange=self.name,
                symbol=req.symbol,
                side=req.side,
                price=req.price,
                amount=rounded_qty,
                error=str(e),
                timestamp=now,
            )
