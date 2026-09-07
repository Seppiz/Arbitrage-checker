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
    has_sufficient_depth: bool = True
    timestamp: float = 0.0

    def format_details(self) -> str:
        depth_flag = " ✅ (Full Depth)" if self.has_sufficient_depth else " ⚠️ (Thin Orderbook)"
        return (
            f"💰 <b>{self.symbol}</b> | {self.buy_exchange.upper()} ➡️ {self.sell_exchange.upper()}{depth_flag}\n"
            f"• Buy: {self.buy_price:,.6g} USDT on {self.buy_exchange.upper()}\n"
            f"• Sell: {self.sell_price:,.6g} USDT on {self.sell_exchange.upper()}\n"
            f"• Spread: {self.spread_pct:+.2f}%\n"
            f"• Fees: {self.total_fees_usdt:.2f} USDT\n"
            f"• <b>Net Profit: {self.net_profit_usdt:+.2f} USDT ({self.roi_pct:+.2f}% ROI)</b>"
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
    require_depth: bool = False,
) -> Opportunity | None:
    """
    Evaluates arbitrage opportunity between two quotes using user's exact formula from main.py.
    Optionally checks orderbook depth to protect against slippage.
    """
    if not buy.is_valid() or not sell.is_valid():
        return None

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
        # Check depth if volume information is present
        has_depth = True
        if buy.ask_volume > 0 and buy.ask_volume < coin_bought * 0.8:
            has_depth = False
        if sell.bid_volume > 0 and sell.bid_volume < coin_bought * 0.8:
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
            total_fees_usdt=total_fees_usdt,
            has_sufficient_depth=has_depth,
            timestamp=time.time(),
        )

    return None
