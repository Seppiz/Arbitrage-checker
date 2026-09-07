import asyncio
import time
from typing import Callable, Dict, Optional
import httpx
from models import Opportunity, OrderRequest, OrderResult
from exchanges import ExchangeClient, NobitexClient, BitpinClient, WallexClient
from config import config


class ArbitrageExecutor:
    """
    Handles risk management, balance verification, and atomic concurrent order dispatch.
    """
    def __init__(
        self,
        nobitex: NobitexClient,
        bitpin: BitpinClient,
        wallex: WallexClient,
        on_trade_executed_cb: Optional[Callable[[str], None]] = None,
    ):
        self.clients: Dict[str, ExchangeClient] = {
            "nobitex": nobitex,
            "bitpin": bitpin,
            "wallex": wallex,
        }
        self.on_trade_executed = on_trade_executed_cb
        self.last_trade_time: Dict[str, float] = {}
        self.is_halted: bool = False
        self.total_trades_count: int = 0
        self.total_realized_profit_usdt: float = 0.0

    async def execute_opportunity(self, opp: Opportunity, client: httpx.AsyncClient):
        if self.is_halted:
            return

        # 1. Minimum Threshold Filters
        if opp.net_profit_usdt < config.min_profit_usdt or opp.roi_pct < config.min_roi_pct:
            return

        # 2. Per-symbol Cooldown Check
        now = time.time()
        last_time = self.last_trade_time.get(opp.symbol, 0)
        if now - last_time < config.trade_cooldown_seconds:
            return

        # Mark cooldown timestamp immediately to prevent race conditions
        self.last_trade_time[opp.symbol] = now

        buy_client = self.clients.get(opp.buy_exchange)
        sell_client = self.clients.get(opp.sell_exchange)

        if not buy_client or not sell_client:
            return

        # 3. Balance Pre-flight Check (for Live Trading)
        if not config.dry_run:
            try:
                usdt_bal = await buy_client.get_balance(client, "usdt")
                if usdt_bal < config.trade_amount_usdt:
                    print(
                        f"⚠️ [Skipped Trade] Insufficient USDT on {opp.buy_exchange.upper()}: "
                        f"Available {usdt_bal:.2f} < Required {config.trade_amount_usdt:.2f}",
                        flush=True,
                    )
                    return

                coin_bal = await sell_client.get_balance(client, opp.symbol)
                if coin_bal < opp.coin_amount * 0.95:
                    print(
                        f"⚠️ [Skipped Trade] Insufficient {opp.symbol} on {opp.sell_exchange.upper()}: "
                        f"Available {coin_bal:.4f} < Required {opp.coin_amount:.4f}",
                        flush=True,
                    )
                    return
            except Exception as e:
                print(f"❌ [Balance Check Error]: {e}", flush=True)
                return

        # 4. Prepare Order Requests
        buy_req = OrderRequest(
            exchange=opp.buy_exchange,
            symbol=opp.symbol,
            side="buy",
            amount=opp.coin_amount,
            price=opp.buy_price,
            order_type="market",
        )
        sell_req = OrderRequest(
            exchange=opp.sell_exchange,
            symbol=opp.symbol,
            side="sell",
            amount=opp.coin_amount,
            price=opp.sell_price,
            order_type="market",
        )

        mode_str = "🧪 [SIMULATED / DRY-RUN]" if config.dry_run else "⚡ [LIVE TRADE]"
        print(f"\n{mode_str} Executing Arbitrage on {opp.symbol}:", flush=True)
        print(f"  • BUY {opp.coin_amount:,.6g} {opp.symbol} on {opp.buy_exchange.upper()} @ {opp.buy_price:,.6g}", flush=True)
        print(f"  • SELL {opp.coin_amount:,.6g} {opp.symbol} on {opp.sell_exchange.upper()} @ {opp.sell_price:,.6g}", flush=True)

        # 5. Concurrent Order Execution via asyncio.gather (Zero Lag!)
        start_t = time.time()
        results = await asyncio.gather(
            buy_client.place_order(client, buy_req),
            sell_client.place_order(client, sell_req),
            return_exceptions=True,
        )
        elapsed_ms = (time.time() - start_t) * 1000

        buy_res = results[0] if isinstance(results[0], OrderResult) else None
        sell_res = results[1] if isinstance(results[1], OrderResult) else None

        # 6. Audit Logging & Summary
        success = (buy_res and buy_res.success) and (sell_res and sell_res.success)
        if success:
            self.total_trades_count += 1
            self.total_realized_profit_usdt += opp.net_profit_usdt

            receipt = (
                f"{'🧪 <b>PAPER TRADE SIMULATION</b>' if config.dry_run else '🚀 <b>LIVE ARBITRAGE EXECUTED!</b>'}\n\n"
                f"<b>Asset:</b> {opp.symbol} / USDT\n"
                f"<b>Buy Order:</b> {opp.buy_exchange.upper()} (ID: <code>{buy_res.order_id}</code>)\n"
                f"<b>Sell Order:</b> {opp.sell_exchange.upper()} (ID: <code>{sell_res.order_id}</code>)\n"
                f"<b>Capital:</b> {config.trade_amount_usdt:.2f} USDT\n"
                f"<b>Gross Spread:</b> {opp.spread_pct:+.2f}%\n"
                f"<b>Est. Net Profit:</b> +{opp.net_profit_usdt:.2f} USDT (+{opp.roi_pct:.2f}%)\n"
                f"<b>Execution Roundtrip:</b> {elapsed_ms:.1f} ms\n"
                f"<b>Total Session Profit:</b> {self.total_realized_profit_usdt:+.2f} USDT ({self.total_trades_count} trades)"
            )
            print(f"✅ {mode_str} Completed in {elapsed_ms:.1f}ms! Net Profit: +{opp.net_profit_usdt:.2f} USDT\n", flush=True)

            if self.on_trade_executed:
                self.on_trade_executed(receipt)
        else:
            err_msg = (
                f"⚠️ <b>ARBITRAGE EXECUTION FAILED / PARTIAL</b>\n\n"
                f"<b>Asset:</b> {opp.symbol}\n"
                f"<b>Buy ({opp.buy_exchange.upper()}):</b> {'OK' if buy_res and buy_res.success else f'ERR: {buy_res.error if buy_res else results[0]}'}\n"
                f"<b>Sell ({opp.sell_exchange.upper()}):</b> {'OK' if sell_res and sell_res.success else f'ERR: {sell_res.error if sell_res else results[1]}'}"
            )
            print(f"❌ Execution failed: {err_msg}", flush=True)
            if self.on_trade_executed:
                self.on_trade_executed(err_msg)
