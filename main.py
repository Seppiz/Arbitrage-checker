import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
import os
import sys
import httpx

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


@dataclass
class Quote:
    exchange: str
    symbol: str
    bid: float
    ask: float


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

SUBSCRIBERS_FILE = "subscribers.json"

raw_admin_ids = os.getenv("TELEGRAM_ADMIN_IDS", "")
ADMIN_CHAT_IDS = [cid.strip() for cid in raw_admin_ids.split(",") if cid.strip()]

FEES_PERCENT = {
    "nobitex": 0.13,
    "bitpin": 0.10,
    "wallex": 0.15,
}

SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOT", "LINK", "NEAR", "SUI", "APT",
    "TRX", "LTC", "BCH", "ATOM", "FTM", "INJ", "RENDER", "FET", "TIA", "ARB", "OP",
    "DOGE", "SHIB", "PEPE", "TON", "NOT", "DOGS", "HMSTR", "CATI", "FLOKI", "BONK", "WIF", "BOME",
]

TRADE_AMOUNT_USDT = 100.0
CHECK_INTERVAL = 15
CONCURRENCY_LIMIT = 8
semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)


class SubscriberManager:
    def __init__(self, filename: str):
        self.filename = filename
        self.subscribers = self._load_subscribers()
    
    def _load_subscribers(self) -> set:
        try:
            with open(self.filename, 'r') as f:
                data = json.load(f)
                return set(data.get("subscribers", []))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()
    
    def _save_subscribers(self) -> None:
        try:
            with open(self.filename, 'w') as f:
                json.dump({"subscribers": list(self.subscribers)}, f, indent=2)
        except Exception as e:
            print(f"[Error] Failed to save subscribers: {e}")
    
    def add_subscriber(self, chat_id: str) -> bool:
        if chat_id not in self.subscribers:
            self.subscribers.add(chat_id)
            self._save_subscribers()
            return True
        return False
    
    def remove_subscriber(self, chat_id: str) -> bool:
        if chat_id in self.subscribers:
            self.subscribers.remove(chat_id)
            self._save_subscribers()
            return True
        return False
    
    def get_all_subscribers(self) -> list:
        return list(self.subscribers)
    
    def count(self) -> int:
        return len(self.subscribers)


async def send_telegram_message(client: httpx.AsyncClient, chat_id: str, message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        response = await client.post(url, json=payload, timeout=5.0)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"\n[Telegram Error]: Failed to send to {chat_id}: {e}")
        return False


async def broadcast_alert(client: httpx.AsyncClient, subscriber_manager: SubscriberManager, message: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    subscribers = subscriber_manager.get_all_subscribers()
    if not subscribers:
        return
    
    tasks = [send_telegram_message(client, chat_id, message) for chat_id in subscribers]
    await asyncio.gather(*tasks, return_exceptions=True)


async def process_telegram_updates(client: httpx.AsyncClient, subscriber_manager: SubscriberManager, offset: int = 0) -> int:
    if not TELEGRAM_BOT_TOKEN:
        return offset

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {
        "offset": offset,
        "timeout": 0,
    }
    
    try:
        response = await client.get(url, params=params, timeout=5.0)
        response.raise_for_status()
        data = response.json()
        
        updates = data.get("result", [])
        max_update_id = offset
        
        for update in updates:
            update_id = update.get("update_id", 0)
            max_update_id = max(max_update_id, update_id + 1)
            
            message = update.get("message")
            if not message:
                continue
            
            chat_id = str(message.get("chat", {}).get("id", ""))
            text = message.get("text", "")
            
            if text == "/start":
                if subscriber_manager.add_subscriber(chat_id):
                    welcome_msg = (
                        "🚀 <b>Welcome to the Arbitrage Scanner Bot!</b>\n\n"
                        "You'll receive alerts whenever profitable arbitrage opportunities are detected.\n\n"
                        "Commands:\n"
                        "/start - Start receiving alerts\n"
                        "/stop - Stop receiving alerts\n"
                        "/status - Check current settings\n"
                        "/help - Show this help message"
                    )
                else:
                    welcome_msg = "✅ You're already subscribed to alerts!"
                
                await send_telegram_message(client, chat_id, welcome_msg)
            
            elif text == "/stop":
                if subscriber_manager.remove_subscriber(chat_id):
                    goodbye_msg = "👋 You've been unsubscribed from alerts. Use /start to subscribe again."
                else:
                    goodbye_msg = "ℹ️ You weren't subscribed."
                
                await send_telegram_message(client, chat_id, goodbye_msg)
            
            elif text == "/status":
                status_msg = (
                    f"📊 <b>Bot Status</b>\n\n"
                    f"• Status: Running ✅\n"
                    f"• Watching: {len(SYMBOLS)} coins\n"
                    f"• Capital per trade: {TRADE_AMOUNT_USDT:,.0f} USDT\n"
                    f"• Check interval: {CHECK_INTERVAL}s\n"
                    f"• Subscribers: {subscriber_manager.count()}"
                )
                await send_telegram_message(client, chat_id, status_msg)
            
            elif text == "/help":
                help_msg = (
                    "🤖 <b>Available Commands:</b>\n\n"
                    "/start - Start receiving alerts\n"
                    "/stop - Stop receiving alerts\n"
                    "/status - Check current settings\n"
                    "/help - Show this help message"
                )
                await send_telegram_message(client, chat_id, help_msg)
            
            elif text == "/broadcast" and chat_id in ADMIN_CHAT_IDS:
                admin_msg = f"📊 Currently {subscriber_manager.count()} subscribers"
                await send_telegram_message(client, chat_id, admin_msg)
        
        return max_update_id
    
    except Exception as e:
        print(f"\n[Telegram Update Error]: {e}")
        return offset


async def get_nobitex(client: httpx.AsyncClient, symbol: str) -> Quote:
    async with semaphore:
        url = "https://apiv2.nobitex.ir/market/stats"
        response = await client.get(
            url,
            params={
                "srcCurrency": symbol.lower(),
                "dstCurrency": "usdt",
            },
            headers={"User-Agent": "ArbitBot/CryptoArbitrage-1.0.0"},
        )
        response.raise_for_status()
        data = response.json()

        key = f"{symbol.lower()}-usdt"
        market = data.get("stats", {}).get(key)
        if not market or not market.get("bestBuy") or not market.get("bestSell"):
            raise ValueError(f"Nobitex: No market for {symbol}-USDT")

        bid = float(market["bestBuy"])
        ask = float(market["bestSell"])
        if bid <= 0 or ask <= 0:
            raise ValueError("Nobitex: Invalid bid/ask")

        return Quote(exchange="nobitex", symbol=symbol, bid=bid, ask=ask)


async def get_bitpin(client: httpx.AsyncClient, symbol: str) -> Quote:
    async with semaphore:
        url = f"https://api.bitpin.org/api/v1/mth/orderbook/{symbol.upper()}_USDT/"
        response = await client.get(
            url,
            headers={"User-Agent": "ArbitBot/CryptoArbitrage-1.0.0"},
        )
        response.raise_for_status()
        data = response.json()

        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if not bids or not asks:
            raise ValueError(f"Bitpin: Empty orderbook for {symbol}_USDT")

        bid = max(float(order[0]) for order in bids)
        ask = min(float(order[0]) for order in asks)
        if bid <= 0 or ask <= 0:
            raise ValueError("Bitpin: Invalid bid/ask")

        return Quote(exchange="bitpin", symbol=symbol, bid=bid, ask=ask)


async def get_wallex(client: httpx.AsyncClient, symbol: str) -> Quote:
    async with semaphore:
        url = "https://api.wallex.ir/v1/depth"
        response = await client.get(
            url,
            params={"symbol": f"{symbol.upper()}USDT"},
            headers={"User-Agent": "ArbitBot/CryptoArbitrage-1.0.0"},
        )
        response.raise_for_status()
        data = response.json()

        order_book = data.get("result", {})
        bids = order_book.get("bid", [])
        asks = order_book.get("ask", [])

        if not bids or not asks:
            raise ValueError(f"Wallex: Empty orderbook for {symbol}USDT")

        bid = max(float(order["price"]) for order in bids)
        ask = min(float(order["price"]) for order in asks)
        if bid <= 0 or ask <= 0:
            raise ValueError("Wallex: Invalid bid/ask")

        return Quote(exchange="wallex", symbol=symbol, bid=bid, ask=ask)


@dataclass
class Opportunity:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    spread_pct: float
    net_profit_usdt: float
    roi_pct: float
    coin_amount: float
    total_fees_usdt: float


def evaluate_arbitrage(
    buy: Quote,
    sell: Quote,
    capital_usdt: float,
    buy_fee_pct: float,
    sell_fee_pct: float,
) -> Opportunity | None:
    buy_fee_rate = buy_fee_pct / 100.0
    sell_fee_rate = sell_fee_pct / 100.0

    buy_fee_usdt = capital_usdt * buy_fee_rate
    usdt_for_coins = capital_usdt - buy_fee_usdt
    coin_bought = usdt_for_coins / buy.ask

    gross_revenue_usdt = coin_bought * sell.bid
    sell_fee_usdt = gross_revenue_usdt * sell_fee_rate
    net_revenue_usdt = gross_revenue_usdt - sell_fee_usdt

    net_profit_usdt = net_revenue_usdt - capital_usdt
    roi_pct = (net_profit_usdt / capital_usdt) * 100.0
    spread_pct = ((sell.bid - buy.ask) / buy.ask) * 100.0
    total_fees_usdt = buy_fee_usdt + sell_fee_usdt

    if net_profit_usdt > 0:
        return Opportunity(
            symbol=buy.symbol,
            buy_exchange=buy.exchange,
            sell_exchange=sell.exchange,
            buy_price=buy.ask,
            sell_price=sell.bid,
            spread_pct=spread_pct,
            net_profit_usdt=net_profit_usdt,
            roi_pct=roi_pct,
            coin_amount=coin_bought,
            total_fees_usdt=total_fees_usdt,
        )

    return None


async def scan_single_symbol(client: httpx.AsyncClient, symbol: str) -> list[Opportunity]:
    results = await asyncio.gather(
        get_nobitex(client, symbol),
        get_bitpin(client, symbol),
        get_wallex(client, symbol),
        return_exceptions=True,
    )

    quotes: list[Quote] = [r for r in results if isinstance(r, Quote)]
    if len(quotes) < 2:
        return []

    opportunities: list[Opportunity] = []
    for buy in quotes:
        for sell in quotes:
            if buy.exchange == sell.exchange:
                continue

            opp = evaluate_arbitrage(
                buy=buy,
                sell=sell,
                capital_usdt=TRADE_AMOUNT_USDT,
                buy_fee_pct=FEES_PERCENT.get(buy.exchange, 0.13),
                sell_fee_pct=FEES_PERCENT.get(sell.exchange, 0.13),
            )
            if opp:
                opportunities.append(opp)

    return opportunities


async def run_scan_cycle(client: httpx.AsyncClient, subscriber_manager: SubscriberManager) -> None:
    now_str = datetime.now().strftime("%H:%M:%S")
    print(f"[{now_str}] Scanning {len(SYMBOLS)} coins across Nobitex, Bitpin & Wallex...", end="\r")

    tasks = [scan_single_symbol(client, sym) for sym in SYMBOLS]
    results = await asyncio.gather(*tasks)

    all_opportunities: list[Opportunity] = []
    for opp_list in results:
        all_opportunities.extend(opp_list)

    print(" " * 85, end="\r")

    if all_opportunities:
        all_opportunities.sort(key=lambda x: x.net_profit_usdt, reverse=True)
        print("\a", end="")

        print(f"\n🔥 [{now_str}] FOUND {len(all_opportunities)} PROFITABLE ARBITRAGE OPPORTUNITY(IES):")
        print("=" * 80)

        for opp in all_opportunities:
            details = (
                f"💰 <b>{opp.symbol}</b> | {opp.buy_exchange.upper()} ➡️ {opp.sell_exchange.upper()}\n"
                f"• Buy: {opp.buy_price:,.6g} USDT on {opp.buy_exchange.upper()}\n"
                f"• Sell: {opp.sell_price:,.6g} USDT on {opp.sell_exchange.upper()}\n"
                f"• Spread: {opp.spread_pct:+.2f}%\n"
                f"• Fees: {opp.total_fees_usdt:.2f} USDT\n"
                f"• <b>Net Profit: {opp.net_profit_usdt:+.2f} USDT ({opp.roi_pct:+.2f}% ROI)</b>"
            )
            print(details.replace("<b>", "").replace("</b>", ""))
            print("-" * 60)

            if subscriber_manager.count() > 0:
                tg_message = f"🚨 <b>ARBITRAGE OPPORTUNITY FOUND!</b>\n\n{details}\n\n<i>Capital: {TRADE_AMOUNT_USDT:,.0f} USDT</i>"
                await broadcast_alert(client, subscriber_manager, tg_message)

        print("=" * 80 + "\n")
    else:
        print(f"[{now_str}] Checked {len(SYMBOLS)} coins. No profitable opportunity. (Next check in {CHECK_INTERVAL}s) | Subscribers: {subscriber_manager.count()}")


async def main():
    print("=" * 80)
    print("🚀 Crypto Arbitrage Scanner with Telegram Bot")
    print(f"Watching: {len(SYMBOLS)} coins across Nobitex, Bitpin, Wallex")
    print(f"Capital: {TRADE_AMOUNT_USDT:,.2f} USDT")
    
    if TELEGRAM_BOT_TOKEN:
        print(f"📱 Telegram Bot: ENABLED ✅")
        print("   Users can subscribe by sending /start to your bot")
    else:
        print("📱 Telegram Bot: DISABLED (Set TELEGRAM_BOT_TOKEN)")
    print("=" * 80 + "\n")

    subscriber_manager = SubscriberManager(SUBSCRIBERS_FILE)
    print(f"📊 Loaded {subscriber_manager.count()} subscribers")
    
    for admin_id in ADMIN_CHAT_IDS:
        subscriber_manager.add_subscriber(admin_id)
    
    print(f"👑 Admin IDs: {', '.join(ADMIN_CHAT_IDS)}")
    print("=" * 80 + "\n")

    update_offset = 0
    
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                update_offset = await process_telegram_updates(client, subscriber_manager, update_offset)
                await run_scan_cycle(client, subscriber_manager)
                
            except Exception as e:
                print(f"\n[Error in main loop]: {e}")

            await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
