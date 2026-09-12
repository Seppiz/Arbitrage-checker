import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class Quote:
    exchange: str
    symbol: str
    bid: float
    ask: float
    bid_volume: float = 0.0
    ask_volume: float = 0.0
    timestamp: float = 0.0

    def is_valid(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid


@dataclass
class InventorySnapshot:
    exchange: str
    usdt: float
    coins: dict
    timestamp: float = 0.0


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
    gross_coin_amount: float = 0.0
    total_fees_usdt: float = 0.0
    has_sufficient_depth: bool = True
    timestamp: float = 0.0
    inventory_skew: float = 0.0
    effective_min_roi_pct: float = 0.0
    is_rebalancing_trade: bool = False

    def format_details(self) -> str:
        depth_flag = " ✅" if self.has_sufficient_depth else " ⚠️ (Low Depth)"
        rebalance_tag = "\n⚖️ <b>Self-Healing Rebalancing Trade</b> (Auto-Inventory Discount Applied)" if self.is_rebalancing_trade else ""
        return (
            f"📡 <b>ARBITRAGE OPPORTUNITY FOUND</b> | #{self.symbol}{depth_flag}\n\n"
            f"🛒 <b>Buy:</b> {self.buy_exchange.upper()} @ {self.buy_price:,.6g} USDT\n"
            f"🏷️ <b>Sell:</b> {self.sell_exchange.upper()} @ {self.sell_price:,.6g} USDT\n"
            f"📊 <b>Gross Spread:</b> {self.spread_pct:+.2f}%\n"
            f"💎 <b>Est. Net Profit:</b> +{self.net_profit_usdt:.2f} USDT (+{self.roi_pct:+.2f}% ROI)"
            f"{rebalance_tag}"
        )


@dataclass
class OrderRequest:
    exchange: str
    symbol: str
    side: str  # "buy" or "sell"
    amount: float
    price: float
    order_type: str = "market"  # "market" or "limit"


@dataclass
class OrderResult:
    success: bool
    order_id: str
    exchange: str
    symbol: str
    side: str
    price: float
    amount: float
    filled_amount: float = 0.0
    fee_paid: float = 0.0
    error: Optional[str] = None
    is_simulated: bool = False
    timestamp: float = 0.0


def evaluate_arbitrage(
    buy: Quote,
    sell: Quote,
    capital_usdt: float,
    buy_fee_pct: float,
    sell_fee_pct: float,
    require_depth: bool = True,
    max_age_seconds: float = 2.5,
) -> Opportunity | None:
    """
    Evaluates arbitrage opportunity between two quotes using user's exact formula from main.py.
    Protects against:
    1. Invalid or inverted prices
    2. Stale quotes (quotes older than max_age_seconds)
    3. Thin orderbook depth (insufficient liquidity causing slippage)
    """
    if not buy.is_valid() or not sell.is_valid():
        return None

    now = time.time()
    # 1. Staleness Check: Reject quotes older than max_age_seconds
    if buy.timestamp > 0 and (now - buy.timestamp) > max_age_seconds:
        return None
    if sell.timestamp > 0 and (now - sell.timestamp) > max_age_seconds:
        return None

    buy_fee_rate = buy_fee_pct / 100.0
    sell_fee_rate = sell_fee_pct / 100.0

    gross_coins = capital_usdt / buy.ask
    coin_bought = gross_coins * (1.0 - buy_fee_rate)

    buy_fee_usdt = capital_usdt * buy_fee_rate
    gross_revenue_usdt = coin_bought * sell.bid
    sell_fee_usdt = gross_revenue_usdt * sell_fee_rate
    net_revenue_usdt = gross_revenue_usdt - sell_fee_usdt

    net_profit_usdt = net_revenue_usdt - capital_usdt
    roi_pct = (net_profit_usdt / capital_usdt) * 100.0
    spread_pct = ((sell.bid - buy.ask) / buy.ask) * 100.0
    total_fees_usdt = buy_fee_usdt + sell_fee_usdt

    if net_profit_usdt > 0:
        # 2. Strict Orderbook Depth Check: Ensure top-of-book volume covers required trade capital
        has_depth = True
        min_required_usd = capital_usdt * 0.95
        if buy.ask_volume > 0 and (buy.ask_volume * buy.ask) < min_required_usd:
            has_depth = False
        if sell.bid_volume > 0 and (sell.bid_volume * sell.bid) < min_required_usd:
            has_depth = False

        if require_depth and not has_depth:
            return None

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
            gross_coin_amount=gross_coins,
            total_fees_usdt=total_fees_usdt,
            has_sufficient_depth=has_depth,
            timestamp=time.time(),
        )

    return None
