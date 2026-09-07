import asyncio
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
from config import config
from models import Quote, Opportunity, evaluate_arbitrage, OrderRequest
from exchanges import NobitexClient, BitpinClient, WallexClient, round_amount
from streamers import OrderBookCache
from execution import ArbitrageExecutor


def test_models():
    print("Testing models and arbitrage math...")
    # Quote 1: Nobitex Buy Ask = 100.0
    # Quote 2: Bitpin Sell Bid = 101.0
    q1 = Quote(exchange="nobitex", symbol="BTC", bid=99.5, ask=100.0, bid_volume=1.0, ask_volume=1.0, timestamp=time.time())
    q2 = Quote(exchange="bitpin", symbol="BTC", bid=101.0, ask=101.5, bid_volume=1.0, ask_volume=1.0, timestamp=time.time())

    opp = evaluate_arbitrage(buy=q1, sell=q2, capital_usdt=100.0, buy_fee_pct=0.15, sell_fee_pct=0.15)
    assert opp is not None
    assert opp.symbol == "BTC"
    assert opp.buy_exchange == "nobitex"
    assert opp.sell_exchange == "bitpin"
    assert opp.net_profit_usdt > 0.6  # 1% gross spread minus ~0.3% fees
    print(f"✅ evaluate_arbitrage math verified: Net Profit = {opp.net_profit_usdt:.4f} USDT, ROI = {opp.roi_pct:.2f}%")


def test_precisions():
    print("Testing precision rounding...")
    assert round_amount("BTC", 0.12345678) == 0.123456
    assert round_amount("DOGE", 154.89) == 154.0
    assert round_amount("SOL", 1.23456) == 1.2345
    print("✅ Precision rounding verified!")


async def test_execution():
    print("Testing dry-run simulated execution...")
    nob = NobitexClient(dry_run=True)
    bp = BitpinClient(dry_run=True)
    wlx = WallexClient(dry_run=True)

    executed_receipts = []
    executor = ArbitrageExecutor(
        nobitex=nob,
        bitpin=bp,
        wallex=wlx,
        on_trade_executed_cb=lambda r: executed_receipts.append(r),
    )

    q1 = Quote(exchange="nobitex", symbol="SOL", bid=199.0, ask=200.0, timestamp=time.time())
    q2 = Quote(exchange="bitpin", symbol="SOL", bid=202.0, ask=203.0, timestamp=time.time())
    opp = evaluate_arbitrage(q1, q2, capital_usdt=100.0, buy_fee_pct=0.15, sell_fee_pct=0.15)

    import httpx
    async with httpx.AsyncClient() as client:
        await executor.execute_opportunity(opp, client)

    assert len(executed_receipts) == 1
    assert "PAPER TRADE SIMULATION" in executed_receipts[0]
    assert "SOL / USDT" in executed_receipts[0]
    print("✅ Simulated execution verified!")


def test_cache():
    print("Testing OrderBookCache opportunity detection...")
    detected = []
    cache = OrderBookCache(
        symbols=["BTC", "ETH"],
        fees_percent={"nobitex": 0.15, "bitpin": 0.15, "wallex": 0.20},
        on_opportunity_cb=lambda opp: detected.append(opp),
    )

    cache.update_quote(Quote(exchange="nobitex", symbol="BTC", bid=99000, ask=99100, timestamp=time.time()))
    cache.update_quote(Quote(exchange="bitpin", symbol="BTC", bid=100000, ask=100100, timestamp=time.time()))

    assert len(detected) >= 1
    assert detected[0].symbol == "BTC"
    print(f"✅ Cache opportunity callback verified! Found {len(detected)} opportunity(ies).")


async def test_subscriber_manager():
    print("Testing asynchronous SubscriberManager with aiofiles...")
    import os
    from telegram_bot import SubscriberManager
    test_file = "test_subscribers.json"
    if os.path.exists(test_file):
        os.remove(test_file)

    sub_mgr = SubscriberManager(test_file)
    assert await sub_mgr.add_subscriber("111222") is True
    assert await sub_mgr.add_subscriber("333444") is True
    assert await sub_mgr.add_subscriber("111222") is False  # Duplicate
    assert sub_mgr.count() == 2

    # Verify a new instance loads the saved data from disk via aiofiles
    sub_mgr2 = SubscriberManager(test_file)
    loaded = await sub_mgr2.load()
    assert "111222" in loaded
    assert "333444" in loaded
    assert sub_mgr2.count() == 2

    assert await sub_mgr2.remove_subscriber("111222") is True
    assert sub_mgr2.count() == 1

    if os.path.exists(test_file):
        os.remove(test_file)
    print("✅ Asynchronous aiofiles SubscriberManager verified!")


async def main():
    test_models()
    test_precisions()
    test_cache()
    await test_execution()
    await test_subscriber_manager()
    print("\n🎉 ALL UNIT TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    asyncio.run(main())
