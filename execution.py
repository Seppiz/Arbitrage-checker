import asyncio
import sys
import time
from typing import Callable, Dict, Optional
import httpx
from models import Opportunity, OrderRequest, OrderResult, InventorySnapshot
from exchanges import ExchangeClient, NobitexClient, BitpinClient, WallexClient
from config import config

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except Exception:
        pass


class InventoryManager:
    """
    Manages dual-sided inventory across enabled exchanges.
    Implements:
    - Periodic asynchronous balance synchronization without blocking trade execution.
    - Mathematical Inventory Skew factor calculation.
    - Dynamic ROI threshold adjustment (Discount for healing trades, Penalty for draining trades).
    - Emergency threshold monitoring and batch rebalance recommendation alerts.
    """
    def __init__(
        self,
        clients: Dict[str, ExchangeClient],
        on_critical_alert_cb: Optional[Callable[[str], None]] = None,
    ):
        self.clients = clients
        self.on_critical_alert = on_critical_alert_cb
        self.snapshots: Dict[str, InventorySnapshot] = {}
        self.last_sync_time: float = 0.0
        self.last_alert_time: float = 0.0
        self._lock = asyncio.Lock()

    async def sync_balances(self, client: httpx.AsyncClient) -> Dict[str, InventorySnapshot]:
        async with self._lock:
            now = time.time()
            tasks = []
            ex_names = []
            for name, ex_client in self.clients.items():
                if name in config.enabled_exchanges:
                    tasks.append(ex_client.get_all_balances(client))
                    ex_names.append(name)

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for name, res in zip(ex_names, results):
                if isinstance(res, dict):
                    usdt_val = float(res.get("USDT", 0.0) or 0.0)
                    coins_dict = {k.upper(): float(v) for k, v in res.items() if k.upper() != "USDT"}
                    self.snapshots[name] = InventorySnapshot(
                        exchange=name,
                        usdt=usdt_val,
                        coins=coins_dict,
                        timestamp=now,
                    )
            self.last_sync_time = now
            self.check_critical_thresholds()
            return self.snapshots

    def get_cached_balance(self, exchange: str, currency: str) -> float:
        snap = self.snapshots.get(exchange.lower())
        if not snap:
            return 0.0
        if currency.upper() == "USDT":
            return snap.usdt
        return snap.coins.get(currency.upper(), 0.0)

    def calculate_inventory_skew(self, symbol: str, buy_exchange: str, sell_exchange: str) -> float:
        """
        Calculates the Inventory Imbalance/Skew factor I in [-1.0, 1.0].
        Positive I (> 0) means the trade RESTORES balance (Buying where USDT is plentiful, Selling where Coin is plentiful).
        Negative I (< 0) means the trade DRAINS an already deficient asset.
        """
        if not config.enable_inventory_skewing:
            return 0.0

        buy_ex = buy_exchange.lower()
        sell_ex = sell_exchange.lower()

        buy_snap = self.snapshots.get(buy_ex)
        sell_snap = self.snapshots.get(sell_ex)
        if not buy_snap or not sell_snap:
            return 0.0

        # 1. USDT distribution
        total_usdt = buy_snap.usdt + sell_snap.usdt
        if total_usdt > 0:
            usdt_buy_ratio = buy_snap.usdt / total_usdt
        else:
            usdt_buy_ratio = 0.5

        # 2. Coin distribution
        sym = symbol.upper()
        buy_coin = buy_snap.coins.get(sym, 0.0)
        sell_coin = sell_snap.coins.get(sym, 0.0)
        total_coin = buy_coin + sell_coin
        if total_coin > 0:
            coin_sell_ratio = sell_coin / total_coin
        else:
            coin_sell_ratio = 0.5

        # Imbalance factor: deviation from target 50/50 balance
        skew = (usdt_buy_ratio - config.target_inventory_ratio) + (coin_sell_ratio - config.target_inventory_ratio)
        return max(-1.0, min(1.0, skew))

    def evaluate_effective_roi(self, opp: Opportunity) -> Opportunity:
        """
        Applies dynamic asymmetric inventory skewing to calculate effective required ROI.
        """
        skew = self.calculate_inventory_skew(opp.symbol, opp.buy_exchange, opp.sell_exchange)
        opp.inventory_skew = skew

        base_min_roi = config.min_roi_pct
        if not config.enable_inventory_skewing:
            opp.effective_min_roi_pct = base_min_roi
            opp.is_rebalancing_trade = False
            return opp

        if skew > 0.10:
            # Self-healing rebalancing trade: Apply discount (ease entry to encourage rebalancing)
            discount = min(config.inventory_discount_factor, skew * config.inventory_discount_factor)
            effective_roi = base_min_roi * (1.0 - discount)
            fee_floor = 0.05
            opp.effective_min_roi_pct = max(fee_floor, effective_roi)
            opp.is_rebalancing_trade = True
        elif skew < -0.10:
            # Draining trade: Apply penalty (make entry harder to protect depleting inventory)
            penalty = abs(skew) * config.inventory_penalty_factor
            opp.effective_min_roi_pct = base_min_roi * (1.0 + penalty)
            opp.is_rebalancing_trade = False
        else:
            opp.effective_min_roi_pct = base_min_roi
            opp.is_rebalancing_trade = False

        return opp

    def check_critical_thresholds(self):
        """
        Monitors for inventory exhaustion (< critical_inventory_threshold_pct).
        Triggers emergency alert with batch rebalancing recommendation.
        """
        now = time.time()
        # Cooldown of 15 minutes between alerts
        if now - self.last_alert_time < 900:
            return

        total_usdt = sum(snap.usdt for snap in self.snapshots.values())
        if total_usdt <= 0:
            return

        alerts = []
        for name, snap in self.snapshots.items():
            usdt_pct = (snap.usdt / total_usdt) * 100.0
            if usdt_pct < config.critical_inventory_threshold_pct or snap.usdt < config.trade_amount_usdt:
                alerts.append(
                    f"⚠️ <b>{name.upper()} USDT Depleted:</b> {snap.usdt:.2f} USDT ({usdt_pct:.1f}% of portfolio)"
                )

        if alerts and self.on_critical_alert:
            self.last_alert_time = now
            msg = (
                f"🚨 <b>INVENTORY IMBALANCE ALERT</b>\n\n"
                + "\n".join(alerts)
                + f"\n\n💡 <b>Recommended Batch Rebalance:</b>\n"
                f"• Avoid individual transfers per trade.\n"
                f"• Transfer low-fee priority coin (e.g., NEAR, TRX or TON) from the surplus exchange to restore balance in 1 batch.\n"
                f"• Check full status using /inventory"
            )
            self.on_critical_alert(msg)

    def get_health_report(self) -> str:
        """Generates an executive health report of portfolio distribution and rebalance recommendations."""
        if not self.snapshots:
            return "⏳ در حال استعلام و همگام‌سازی موجودی‌ها از صرافی‌ها... لطفاً چند لحظه دیگر امتحان کنید."

        total_usdt = sum(snap.usdt for snap in self.snapshots.values())
        ex_lines = []
        for name, snap in sorted(self.snapshots.items()):
            pct = (snap.usdt / max(total_usdt, 0.0001)) * 100.0
            p_coins = [f"{c}: {snap.coins.get(c, 0.0):.3g}" for c in config.priority_coins if snap.coins.get(c, 0.0) > 0]
            coins_str = ", ".join(p_coins) if p_coins else "بدون موجودی"
            status_icon = "🟢" if pct >= config.critical_inventory_threshold_pct else "🔴 (بحرانی)"
            ex_lines.append(
                f"🏛 <b>{name.upper()}</b> {status_icon}\n"
                f"  • تتر نقد: <b>{snap.usdt:.2f} USDT</b> ({pct:.1f}%)\n"
                f"  • ارزهای هدف: {coins_str}"
            )

        # Health score: 100 - (deviation from 50/50 * 100)
        imbalance_diff = 0.0
        if len(self.snapshots) >= 2:
            vals = [s.usdt for s in self.snapshots.values()]
            if sum(vals) > 0:
                ratios = [v / sum(vals) for v in vals]
                imbalance_diff = abs(ratios[0] - config.target_inventory_ratio) * 200.0  # 0 to 100%
        health_score = max(0, int(100 - imbalance_diff))

        if health_score < 60:
            surplus_ex = max(self.snapshots.items(), key=lambda x: x[1].usdt)[0].upper()
            deficit_ex = min(self.snapshots.items(), key=lambda x: x[1].usdt)[0].upper()
            rebalance_advice = (
                f"\n\n💡 <b>توصیه ریبلنس تجمیعی:</b>\n"
                f"موجودی در {surplus_ex} متمرکز شده است. برای جلوگیری از کارمزد بالای تتر، "
                f"پیشنهاد می‌شود یک انتقال دسته‌ای از طریق ارزهای کم‌کارمزد (مثل <b>NEAR</b> یا <b>TRX</b>) "
                f"از {surplus_ex} به {deficit_ex} انجام شود."
            )
        else:
            rebalance_advice = "\n\n✅ <b>وضعیت توازن:</b> مطلوب (مکانیزم خود-ترمیم به صورت خودکار الاکلنگ تریدها را هدایت می‌کند)."

        sync_ago = int(time.time() - self.last_sync_time) if self.last_sync_time > 0 else 0
        report = (
            f"📊 <b>گزارش توازن و سلامت دارایی‌ها (Inventory Health)</b>\n"
            f"• امتیاز سلامت توازن: <b>{health_score}/100</b>\n"
            f"• مجموع تتر نقد: <b>{total_usdt:.2f} USDT</b>\n"
            f"• همگام‌سازی: {sync_ago} ثانیه قبل\n\n"
            + "\n\n".join(ex_lines)
            + f"{rebalance_advice}"
        )
        return report


class ArbitrageExecutor:
    """
    Handles risk management, balance verification, dynamic inventory skewing, and atomic concurrent order dispatch.
    """
    def __init__(
        self,
        nobitex: NobitexClient,
        bitpin: BitpinClient,
        wallex: WallexClient,
        inventory_manager: Optional[InventoryManager] = None,
        on_trade_executed_cb: Optional[Callable[[str], None]] = None,
    ):
        self.clients: Dict[str, ExchangeClient] = {
            "nobitex": nobitex,
            "bitpin": bitpin,
            "wallex": wallex,
        }
        self.inventory_mgr = inventory_manager
        self.on_trade_executed = on_trade_executed_cb
        self.last_trade_time: Dict[str, float] = {}
        self.is_halted: bool = False
        self.total_trades_count: int = 0
        self.total_realized_profit_usdt: float = 0.0
        self.execution_lock = asyncio.Lock()  # Prevents multi-symbol balance exhaustion race conditions

    async def execute_opportunity(self, opp: Opportunity, client: httpx.AsyncClient):
        if self.is_halted:
            return

        # 1. Dynamic Inventory Skewing & Hurdle Rate
        if self.inventory_mgr:
            opp = self.inventory_mgr.evaluate_effective_roi(opp)
            required_roi = opp.effective_min_roi_pct
        else:
            required_roi = config.min_roi_pct

        if opp.net_profit_usdt < config.min_profit_usdt or opp.roi_pct < required_roi:
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

        # Acquire execution lock to prevent concurrent multi-symbol overspending
        async with self.execution_lock:
            # 3. Strict Pre-flight Balance Check (100% required)
            if not config.dry_run:
                try:
                    usdt_bal = await buy_client.get_balance(client, "usdt")
                    if usdt_bal < config.trade_amount_usdt:
                        safe_print(
                            f"⚠️ [Skipped Trade] Insufficient USDT on {opp.buy_exchange.upper()}: "
                            f"Available {usdt_bal:.2f} < Required {config.trade_amount_usdt:.2f}",
                            flush=True,
                        )
                        return

                    coin_bal = await sell_client.get_balance(client, opp.symbol)
                    if coin_bal < opp.coin_amount:
                        safe_print(
                            f"⚠️ [Skipped Trade] Insufficient {opp.symbol} on {opp.sell_exchange.upper()}: "
                            f"Available {coin_bal:.4f} < Required {opp.coin_amount:.4f}",
                            flush=True,
                        )
                        return
                except Exception as e:
                    safe_print(f"❌ [Balance Check Error]: {e}", flush=True)
                    return

            # 4. Prepare Order Requests with Max Slippage Protection
            slippage_rate = config.max_slippage_pct / 100.0
            max_buy_price = opp.buy_price * (1.0 + slippage_rate)
            min_sell_price = opp.sell_price * (1.0 - slippage_rate)

            buy_req = OrderRequest(
                exchange=opp.buy_exchange,
                symbol=opp.symbol,
                side="buy",
                amount=opp.gross_coin_amount if opp.gross_coin_amount > 0 else opp.coin_amount,
                price=max_buy_price,
                order_type="limit",  # Limit order protects against orderbook sweeping
            )
            sell_req = OrderRequest(
                exchange=opp.sell_exchange,
                symbol=opp.symbol,
                side="sell",
                amount=opp.coin_amount,
                price=min_sell_price,
                order_type="limit",
            )

            mode_str = "🧪 [SIMULATED / DRY-RUN]" if config.dry_run else "⚡ [LIVE TRADE]"
            safe_print(f"\n{mode_str} Executing Arbitrage on {opp.symbol}:", flush=True)
            safe_print(f"  • BUY {opp.coin_amount:,.6g} {opp.symbol} on {opp.buy_exchange.upper()} @ Max {max_buy_price:,.6g}", flush=True)
            safe_print(f"  • SELL {opp.coin_amount:,.6g} {opp.symbol} on {opp.sell_exchange.upper()} @ Min {min_sell_price:,.6g}", flush=True)

            # 5. Concurrent Order Execution via asyncio.gather
            start_t = time.time()
            results = await asyncio.gather(
                buy_client.place_order(client, buy_req),
                sell_client.place_order(client, sell_req),
                return_exceptions=True,
            )
            elapsed_ms = (time.time() - start_t) * 1000

            buy_res = results[0] if isinstance(results[0], OrderResult) else None
            sell_res = results[1] if isinstance(results[1], OrderResult) else None

            # 6. Audit Logging, Partial Fill Reconciliation & Summary
            success = (buy_res and buy_res.success) and (sell_res and sell_res.success)
            if success:
                # Reconcile partial fills if quantities differ
                fill_discrepancy_msg = ""
                b_filled = buy_res.filled_amount
                s_filled = sell_res.filled_amount
                diff = abs(b_filled - s_filled)
                if diff > 0 and (diff / max(b_filled, s_filled, 0.0001)) > 0.05:
                    if b_filled > s_filled:
                        unwind_qty = b_filled - s_filled
                        unwind_req = OrderRequest(opp.buy_exchange, opp.symbol, "sell", unwind_qty, opp.buy_price * 0.98, "market")
                        await buy_client.place_order(client, unwind_req)
                        fill_discrepancy_msg = f"\n⚠️ Partial fill discrepancy ({diff:.4g} {opp.symbol}) unwound on {opp.buy_exchange.upper()}."
                    else:
                        unwind_qty = s_filled - b_filled
                        unwind_req = OrderRequest(opp.sell_exchange, opp.symbol, "buy", unwind_qty, opp.sell_price * 1.02, "market")
                        await sell_client.place_order(client, unwind_req)
                        fill_discrepancy_msg = f"\n⚠️ Partial fill discrepancy ({diff:.4g} {opp.symbol}) unwound on {opp.sell_exchange.upper()}."

                self.total_trades_count += 1
                self.total_realized_profit_usdt += opp.net_profit_usdt

                skew_msg = f"\n⚖️ <b>Inventory Skew:</b> {opp.inventory_skew:+.2f} ({'Self-Healing Trade' if opp.is_rebalancing_trade else 'Balanced'})" if config.enable_inventory_skewing else ""
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
                    f"{skew_msg}"
                    f"{fill_discrepancy_msg}"
                )
                safe_print(f"✅ {mode_str} Completed in {elapsed_ms:.1f}ms! Net Profit: +{opp.net_profit_usdt:.2f} USDT\n", flush=True)

                if self.on_trade_executed:
                    self.on_trade_executed(receipt)

                # Trigger immediate background inventory sync to update skewed ratios for next trade
                if self.inventory_mgr:
                    asyncio.create_task(self.inventory_mgr.sync_balances(client))
            else:
                # Check for dangerous One-Legged Execution (one side succeeded, other side failed)
                rollback_msg = ""
                if config.enable_auto_rollback:
                    if buy_res and buy_res.success and (not sell_res or not sell_res.success):
                        # Buy filled, but Sell failed -> Immediately unwind by dumping bought coins on buy_exchange to recover USDT
                        unwind_req = OrderRequest(
                            exchange=opp.buy_exchange,
                            symbol=opp.symbol,
                            side="sell",
                            amount=buy_res.filled_amount or opp.coin_amount,
                            price=opp.buy_price * 0.98,
                            order_type="market",
                        )
                        unwind_res = await buy_client.place_order(client, unwind_req)
                        if unwind_res.success:
                            rollback_msg = f"\n\n🛡️ <b>EMERGENCY ROLLBACK SUCCESSFUL:</b> Sold back {unwind_req.amount} {opp.symbol} on {opp.buy_exchange.upper()} to recover USDT. Exposure closed."
                        else:
                            rollback_msg = f"\n\n🚨 <b>ROLLBACK FAILED:</b> Could not sell back on {opp.buy_exchange.upper()}: {unwind_res.error}. MANUAL ATTENTION REQUIRED!"

                    elif sell_res and sell_res.success and (not buy_res or not buy_res.success):
                        # Sell filled, but Buy failed -> Immediately unwind by rebuying coins on sell_exchange
                        unwind_req = OrderRequest(
                            exchange=opp.sell_exchange,
                            symbol=opp.symbol,
                            side="buy",
                            amount=sell_res.filled_amount or opp.coin_amount,
                            price=opp.sell_price * 1.02,
                            order_type="market",
                        )
                        unwind_res = await sell_client.place_order(client, unwind_req)
                        if unwind_res.success:
                            rollback_msg = f"\n\n🛡️ <b>EMERGENCY ROLLBACK SUCCESSFUL:</b> Re-bought {unwind_req.amount} {opp.symbol} on {opp.sell_exchange.upper()} to restore inventory. Exposure closed."
                        else:
                            rollback_msg = f"\n\n🚨 <b>ROLLBACK FAILED:</b> Could not re-buy on {opp.sell_exchange.upper()}: {unwind_res.error}. MANUAL ATTENTION REQUIRED!"

                err_msg = (
                    f"⚠️ <b>ARBITRAGE EXECUTION FAILED / PARTIAL</b>\n\n"
                    f"<b>Asset:</b> {opp.symbol}\n"
                    f"<b>Buy ({opp.buy_exchange.upper()}):</b> {'OK' if buy_res and buy_res.success else f'ERR: {buy_res.error if buy_res else results[0]}'}\n"
                    f"<b>Sell ({opp.sell_exchange.upper()}):</b> {'OK' if sell_res and sell_res.success else f'ERR: {sell_res.error if sell_res else results[1]}'}"
                    f"{rollback_msg}"
                )
                safe_print(f"❌ Execution failed: {err_msg}", flush=True)
                if self.on_trade_executed:
                    self.on_trade_executed(err_msg)
