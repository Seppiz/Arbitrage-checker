import asyncio
import os
import sys
import time
from datetime import datetime
import httpx

# Configure UTF-8 encoding on Windows to support emojis and symbols
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import config, DEFAULT_SYMBOLS, DEFAULT_FEES_PERCENT
from models import Quote, Opportunity, evaluate_arbitrage
from exchanges import NobitexClient, BitpinClient, WallexClient
from streamers import OrderBookCache, NobitexWebSocket, BitpinWebSocket, WallexStreamer
from execution import ArbitrageExecutor
from telegram_bot import SubscriberManager, TelegramBot

# Re-export key variables for backward compatibility
SYMBOLS = config.symbols
TRADE_AMOUNT_USDT = config.trade_amount_usdt
FEES_PERCENT = config.fees_percent


# Backward-compatible single-pair REST getters (preserved from original main.py)
async def get_nobitex(client: httpx.AsyncClient, symbol: str) -> Quote:
    url = "https://apiv2.nobitex.ir/market/stats"
    res = await client.get(url, params={"srcCurrency": symbol.lower(), "dstCurrency": "usdt"}, timeout=5.0)
    res.raise_for_status()
    data = res.json()
    key = f"{symbol.lower()}-usdt"
    market = data.get("stats", {}).get(key)
    if not market or not market.get("bestBuy") or not market.get("bestSell"):
        raise ValueError(f"Nobitex: No market for {symbol}-USDT")
    return Quote(exchange="nobitex", symbol=symbol.upper(), bid=float(market["bestBuy"]), ask=float(market["bestSell"]), timestamp=time.time())


async def get_bitpin(client: httpx.AsyncClient, symbol: str) -> Quote:
    url = f"https://api.bitpin.org/api/v1/mth/orderbook/{symbol.upper()}_USDT/"
    res = await client.get(url, timeout=5.0)
    res.raise_for_status()
    data = res.json()
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    if not bids or not asks:
        raise ValueError(f"Bitpin: Empty orderbook for {symbol}_USDT")
    return Quote(exchange="bitpin", symbol=symbol.upper(), bid=float(bids[0][0]), ask=float(asks[0][0]), timestamp=time.time())


async def get_wallex(client: httpx.AsyncClient, symbol: str) -> Quote:
    url = "https://api.wallex.ir/v1/depth"
    res = await client.get(url, params={"symbol": f"{symbol.upper()}USDT"}, timeout=5.0)
    res.raise_for_status()
    data = res.json()
    bids = data.get("result", {}).get("bid", [])
    asks = data.get("result", {}).get("ask", [])
    if not bids or not asks:
        raise ValueError(f"Wallex: Empty orderbook for {symbol}USDT")
    return Quote(exchange="wallex", symbol=symbol.upper(), bid=float(bids[0]["price"]), ask=float(asks[0]["price"]), timestamp=time.time())


async def scan_single_symbol(client: httpx.AsyncClient, symbol: str) -> list[Opportunity]:
    """Scans a single symbol via REST (preserved for testing and manual one-off scans)."""
    results = await asyncio.gather(
        get_nobitex(client, symbol),
        get_bitpin(client, symbol),
        get_wallex(client, symbol),
        return_exceptions=True,
    )
    quotes = [r for r in results if isinstance(r, Quote)]
    if len(quotes) < 2:
        return []
    opportunities = []
    for buy in quotes:
        for sell in quotes:
            if buy.exchange == sell.exchange:
                continue
            opp = evaluate_arbitrage(
                buy=buy,
                sell=sell,
                capital_usdt=config.trade_amount_usdt,
                buy_fee_pct=config.fees_percent.get(buy.exchange, 0.15),
                sell_fee_pct=config.fees_percent.get(sell.exchange, 0.15),
            )
            if opp:
                opportunities.append(opp)
    return opportunities


async def telegram_polling_loop(bot: TelegramBot, executor: ArbitrageExecutor, nobitex, bitpin, wallex, client: httpx.AsyncClient):
    """Periodically polls Telegram for user commands."""
    while True:
        try:
            await bot.process_updates(client, executor, nobitex, bitpin, wallex)
        except asyncio.CancelledError:
            break
        except Exception:
            pass
        await asyncio.sleep(1.0)


async def main():
    print("=" * 80)
    print("🚀 REAL-TIME WEBSOCKET ARBITRAGE & AUTO-EXECUTION BOT")
    print("=" * 80)
    print(f"📊 Monitored Symbols: {len(config.symbols)} coins across Nobitex, Bitpin, and Wallex")
    print(f"💰 Capital per Trade: {config.trade_amount_usdt:,.2f} USDT")
    print(f"🎯 Min Profit Target: {config.min_profit_usdt:.2f} USDT (Min {config.min_roi_pct:.2f}% ROI)")
    print(f"🛡️ Safety Mode: {'🧪 DRY-RUN (Paper Simulation)' if config.dry_run else '⚡ LIVE TRADING (Real Execution)'}")
    print(f"⏱️ Cooldown per Coin: {config.trade_cooldown_seconds} seconds")

    # 1. Initialize Telegram Bot & Subscribers
    sub_mgr = SubscriberManager(config.subscribers_file)
    await sub_mgr.load()
    for admin_id in config.telegram_admin_ids:
        await sub_mgr.add_subscriber(admin_id)

    bot = TelegramBot(
        token=config.telegram_bot_token,
        admin_ids=config.telegram_admin_ids,
        subscriber_manager=sub_mgr,
    )
    if config.telegram_bot_token:
        print(f"📱 Telegram Bot: ENABLED ✅ ({sub_mgr.count()} subscribers)")
    else:
        print("📱 Telegram Bot: DISABLED (Set TELEGRAM_BOT_TOKEN in .env)")

    # 2. Initialize Exchange Clients
    nobitex_client = NobitexClient(token=config.nobitex_api_token, dry_run=config.dry_run)
    bitpin_client = BitpinClient(api_key=config.bitpin_api_key, dry_run=config.dry_run)
    wallex_client = WallexClient(api_key=config.wallex_api_key, dry_run=config.dry_run)

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        # 3. Initialize Execution Engine
        def on_trade_receipt(receipt: str):
            asyncio.create_task(bot.broadcast(http_client, receipt))

        executor = ArbitrageExecutor(
            nobitex=nobitex_client,
            bitpin=bitpin_client,
            wallex=wallex_client,
            on_trade_executed_cb=on_trade_receipt,
        )

        # 4. Opportunity Callback from Real-Time Streamers
        last_console_log: dict = {}
        last_tg_alert: dict = {}

        def on_opportunity_detected(opp: Opportunity):
            now = time.time()
            # Throttle console printing per symbol to once every 5 seconds to keep terminal clean
            if now - last_console_log.get(opp.symbol, 0) >= 5.0:
                last_console_log[opp.symbol] = now
                now_str = datetime.now().strftime("%H:%M:%S")
                try:
                    print(
                        f"\n🔥 [{now_str}] LIVE ARBITRAGE: {opp.symbol} | {opp.buy_exchange.upper()} -> {opp.sell_exchange.upper()} "
                        f"| Spread: {opp.spread_pct:+.2f}% | Net Profit: +{opp.net_profit_usdt:.2f} USDT (+{opp.roi_pct:.2f}% ROI)",
                        flush=True,
                    )
                except Exception:
                    pass

            # Throttle Telegram alerts per symbol (every trade_cooldown_seconds, min 15s)
            tg_cooldown = max(15, config.trade_cooldown_seconds)
            if now - last_tg_alert.get(opp.symbol, 0) >= tg_cooldown:
                last_tg_alert[opp.symbol] = now
                asyncio.create_task(bot.broadcast(http_client, opp.format_details()))

            # Safely dispatch execution to executor (which manages its own strict cooldown & filters)
            async def safe_execute():
                try:
                    await executor.execute_opportunity(opp, http_client)
                except Exception as e:
                    try:
                        print(f"❌ Execution error on {opp.symbol}: {e}", flush=True)
                    except Exception:
                        pass

            asyncio.create_task(safe_execute())

        # 5. Initialize OrderBook Cache & Streamers
        cache = OrderBookCache(
            symbols=config.symbols,
            fees_percent=config.fees_percent,
            on_opportunity_cb=on_opportunity_detected,
        )

        nobitex_ws = NobitexWebSocket(cache, config.symbols)
        bitpin_ws = BitpinWebSocket(cache, config.symbols)
        wallex_streamer = WallexStreamer(cache, config.symbols, interval=1.5)

        print("=" * 80)
        print("🚀 Starting real-time WebSocket streams and trading engine...\n", flush=True)

        tasks = [
            asyncio.create_task(nobitex_ws.run()),
            asyncio.create_task(bitpin_ws.run()),
            asyncio.create_task(wallex_streamer.run(http_client)),
            asyncio.create_task(telegram_polling_loop(bot, executor, nobitex_client, bitpin_client, wallex_client, http_client)),
        ]

        try:
            await asyncio.gather(*tasks)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n👋 Shutting down arbitrage bot cleanly...")
            nobitex_ws.running = False
            bitpin_ws.running = False
            wallex_streamer.running = False
            for t in tasks:
                t.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped.")
