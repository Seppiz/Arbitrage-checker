import asyncio
import json
import time
from typing import Callable, Dict, List, Optional
import httpx
import websockets
from models import Quote, Opportunity, evaluate_arbitrage
from config import config


class OrderBookCache:
    """
    In-memory cache for best bids and asks across all monitored symbols and exchanges.
    Triggers an opportunity check whenever a quote is updated.
    """
    def __init__(
        self,
        symbols: List[str],
        fees_percent: Dict[str, float],
        on_opportunity_cb: Optional[Callable[[Opportunity], None]] = None,
    ):
        self.symbols = [s.upper() for s in symbols]
        self.fees_percent = fees_percent
        self.on_opportunity = on_opportunity_cb
        # quotes[symbol][exchange] = Quote
        self.quotes: Dict[str, Dict[str, Quote]] = {s: {} for s in self.symbols}
        self.exchanges = ["nobitex", "bitpin", "wallex"]

    def update_quote(self, quote: Quote):
        sym = quote.symbol.upper()
        if sym not in self.quotes:
            self.quotes[sym] = {}
        self.quotes[sym][quote.exchange] = quote

        # Evaluate arbitrage against other exchanges for this symbol
        self._check_arbitrage(sym)

    def _check_arbitrage(self, symbol: str):
        sym_quotes = self.quotes.get(symbol, {})
        available_exchanges = [ex for ex in self.exchanges if ex in sym_quotes and sym_quotes[ex].is_valid()]

        if len(available_exchanges) < 2:
            return

        for buy_ex in available_exchanges:
            for sell_ex in available_exchanges:
                if buy_ex == sell_ex:
                    continue

                buy_q = sym_quotes[buy_ex]
                sell_q = sym_quotes[sell_ex]

                opp = evaluate_arbitrage(
                    buy=buy_q,
                    sell=sell_q,
                    capital_usdt=config.trade_amount_usdt,
                    buy_fee_pct=self.fees_percent.get(buy_ex, 0.15),
                    sell_fee_pct=self.fees_percent.get(sell_ex, 0.15),
                    require_depth=config.require_sufficient_depth,
                    max_age_seconds=config.max_quote_age_seconds,
                )
                if opp and opp.net_profit_usdt > 0:
                    if self.on_opportunity:
                        self.on_opportunity(opp)

    def get_quote(self, symbol: str, exchange: str) -> Optional[Quote]:
        return self.quotes.get(symbol.upper(), {}).get(exchange)


class NobitexWebSocket:
    """
    Multiplexed WebSocket client for Nobitex using Centrifugo protocol.
    Subscribes to all configured symbols over a single persistent connection.
    """
    def __init__(self, cache: OrderBookCache, symbols: List[str]):
        self.cache = cache
        self.symbols = symbols
        self.uri = "wss://ws.nobitex.ir/connection/websocket"
        self.running = False

    async def run(self):
        self.running = True
        backoff = 2

        while self.running:
            try:
                print(f"🌐 [Nobitex WS] Connecting to {self.uri}...", flush=True)
                async with websockets.connect(self.uri, ping_interval=None, open_timeout=10) as ws:
                    # 1. Connect handshake
                    await ws.send(json.dumps({"id": 1, "connect": {}}))
                    await ws.recv()

                    # 2. Subscribe to all symbols
                    sub_id = 2
                    for sym in self.symbols:
                        channel = f"public:orderbook-{sym.upper()}USDT"
                        await ws.send(json.dumps({"id": sub_id, "subscribe": {"channel": channel}}))
                        sub_id += 1

                    print(f"✅ [Nobitex WS] Subscribed to {len(self.symbols)} symbols!", flush=True)
                    backoff = 2  # Reset backoff upon successful connection

                    # 3. Message loop
                    async for message in ws:
                        if not self.running:
                            break

                        for line in message.splitlines():
                            line = line.strip()
                            if not line:
                                continue

                            if line == "{}":
                                await ws.send("{}")
                                continue

                            try:
                                data = json.loads(line)
                            except Exception:
                                continue

                            push = data.get("push", {})
                            channel = push.get("channel", "")

                            # Extract symbol from channel: public:orderbook-{SYM}USDT
                            if channel.startswith("public:orderbook-") and channel.endswith("USDT"):
                                raw_sym = channel.replace("public:orderbook-", "").replace("USDT", "")
                                pub = push.get("pub", {}).get("data", {})
                                bids = pub.get("bids", [])
                                asks = pub.get("asks", [])

                                if bids and asks:
                                    bid_price = float(bids[0][0])
                                    bid_vol = float(bids[0][1]) if len(bids[0]) > 1 else 0.0
                                    ask_price = float(asks[0][0])
                                    ask_vol = float(asks[0][1]) if len(asks[0]) > 1 else 0.0

                                    quote = Quote(
                                        exchange="nobitex",
                                        symbol=raw_sym,
                                        bid=bid_price,
                                        ask=ask_price,
                                        bid_volume=bid_vol,
                                        ask_volume=ask_vol,
                                        timestamp=time.time(),
                                    )
                                    self.cache.update_quote(quote)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [Nobitex WS Error]: {e}. Reconnecting in {backoff}s...", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 20)


class BitpinWebSocket:
    """
    Multiplexed WebSocket client for Bitpin using Centrifugo protocol.
    Subscribes to all configured symbols over a single persistent connection.
    """
    def __init__(self, cache: OrderBookCache, symbols: List[str]):
        self.cache = cache
        self.symbols = symbols
        self.uri = "wss://centrifugo.bitpin.org/connection/websocket"
        self.running = False

    async def run(self):
        self.running = True
        backoff = 2

        while self.running:
            try:
                print(f"🌐 [Bitpin WS] Connecting to {self.uri}...", flush=True)
                async with websockets.connect(self.uri, ping_interval=None, open_timeout=10) as ws:
                    # 1. Connect handshake
                    await ws.send(json.dumps({"id": 1, "connect": {}}))
                    await ws.recv()

                    # 2. Subscribe to all symbols
                    sub_id = 2
                    for sym in self.symbols:
                        channel = f"orderbook:{sym.upper()}_USDT"
                        await ws.send(json.dumps({"id": sub_id, "subscribe": {"channel": channel}}))
                        sub_id += 1

                    print(f"✅ [Bitpin WS] Subscribed to {len(self.symbols)} symbols!", flush=True)
                    backoff = 2

                    # 3. Message loop
                    async for message in ws:
                        if not self.running:
                            break

                        for line in message.splitlines():
                            line = line.strip()
                            if not line:
                                continue

                            if line == "{}":
                                await ws.send("{}")
                                continue

                            try:
                                data = json.loads(line)
                            except Exception:
                                continue

                            push = data.get("push", {})
                            channel = push.get("channel", "")

                            # Extract symbol from channel: orderbook:{SYM}_USDT
                            if channel.startswith("orderbook:") and channel.endswith("_USDT"):
                                raw_sym = channel.replace("orderbook:", "").replace("_USDT", "")
                                pub = push.get("pub", {}).get("data", {})
                                bids = pub.get("bids", [])
                                asks = pub.get("asks", [])

                                if bids and asks:
                                    bid_price = float(bids[0][0])
                                    bid_vol = float(bids[0][1]) if len(bids[0]) > 1 else 0.0
                                    ask_price = float(asks[0][0])
                                    ask_vol = float(asks[0][1]) if len(asks[0]) > 1 else 0.0

                                    quote = Quote(
                                        exchange="bitpin",
                                        symbol=raw_sym,
                                        bid=bid_price,
                                        ask=ask_price,
                                        bid_volume=bid_vol,
                                        ask_volume=ask_vol,
                                        timestamp=time.time(),
                                    )
                                    self.cache.update_quote(quote)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [Bitpin WS Error]: {e}. Reconnecting in {backoff}s...", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 20)


class WallexStreamer:
    """
    High-frequency bulk streamer for Wallex.
    Fetches all 385 pairs in a single efficient HTTP call every 1.5 seconds.
    """
    def __init__(self, cache: OrderBookCache, symbols: List[str], interval: float = 1.5):
        self.cache = cache
        self.symbols = set(s.upper() for s in symbols)
        self.interval = interval
        self.running = False

    async def run(self, client: httpx.AsyncClient):
        self.running = True
        url = "https://api.wallex.ir/v1/markets"
        headers = {"User-Agent": "ArbitBot/CryptoArbitrage-2.0"}

        while self.running:
            try:
                res = await client.get(url, headers=headers, timeout=5.0)
                if res.status_code == 200:
                    data = res.json()
                    symbols_map = data.get("result", {}).get("symbols", {})
                    now = time.time()

                    for sym in self.symbols:
                        key = f"{sym}USDT"
                        item = symbols_map.get(key)
                        if not item:
                            continue

                        stats = item.get("stats", {})
                        bid_str = stats.get("bidPrice")
                        ask_str = stats.get("askPrice")
                        bid_vol_str = stats.get("bidVolume", "0")
                        ask_vol_str = stats.get("askVolume", "0")

                        if bid_str and ask_str:
                            bid_val = float(bid_str)
                            ask_val = float(ask_str)
                            if bid_val > 0 and ask_val > 0:
                                quote = Quote(
                                    exchange="wallex",
                                    symbol=sym,
                                    bid=bid_val,
                                    ask=ask_val,
                                    bid_volume=float(bid_vol_str),
                                    ask_volume=float(ask_vol_str),
                                    timestamp=now,
                                )
                                self.cache.update_quote(quote)

            except asyncio.CancelledError:
                break
            except Exception as e:
                # Silently handle transient network glitches
                pass

            await asyncio.sleep(self.interval)
