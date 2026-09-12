import os
from dataclasses import dataclass, field
from typing import List, Dict
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)
load_dotenv()

# List of 36 cryptocurrencies from original main.py
DEFAULT_SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOT", "LINK", "NEAR", "SUI", "APT",
    "TRX", "LTC", "BCH", "ATOM", "FTM", "INJ", "RENDER", "FET", "TIA", "ARB", "OP",
    "DOGE", "SHIB", "PEPE", "TON", "NOT", "DOGS", "HMSTR", "CATI", "FLOKI", "BONK", "WIF", "BOME",
]

# Realistic Taker fees percentage (spot USDT markets)
DEFAULT_FEES_PERCENT = {
    "nobitex": float(os.getenv("NOBITEX_FEE_PCT", "0.15")),
    "bitpin": float(os.getenv("BITPIN_FEE_PCT", "0.15")),
    "wallex": float(os.getenv("WALLEX_FEE_PCT", "0.20")),
}


@dataclass
class BotConfig:
    # Telegram settings
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_admin_ids: List[str] = field(
        default_factory=lambda: [
            cid.strip() for cid in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") if cid.strip()
        ]
    )
    subscribers_file: str = os.getenv("SUBSCRIBERS_FILE", "subscribers.json")

    # Exchange API Credentials
    nobitex_api_token: str = os.getenv("NOBITEX_API_TOKEN", "")
    nobitex_api_key: str = os.getenv("NOBITEX_API_KEY", "")
    nobitex_secret_key: str = os.getenv("NOBITEX_SECRET_KEY", "")
    bitpin_api_key: str = os.getenv("BITPIN_API_KEY", "")
    wallex_api_key: str = os.getenv("WALLEX_API_KEY", "")

    # Trading & Execution Settings
    # DRY_RUN is True by default for safety! Toggle to False only when ready for live execution.
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")
    trade_amount_usdt: float = float(os.getenv("TRADE_AMOUNT_USDT", "5.0"))
    min_profit_usdt: float = float(os.getenv("MIN_PROFIT_USDT", "0.01"))
    min_roi_pct: float = float(os.getenv("MIN_ROI_PCT", "0.35"))
    max_slippage_pct: float = float(os.getenv("MAX_SLIPPAGE_PCT", "0.30"))
    trade_cooldown_seconds: int = int(os.getenv("TRADE_COOLDOWN_SECONDS", "30"))
    max_quote_age_seconds: float = float(os.getenv("MAX_QUOTE_AGE_SECONDS", "2.0"))
    enable_auto_rollback: bool = os.getenv("ENABLE_AUTO_ROLLBACK", "true").lower() in ("true", "1", "yes")
    require_sufficient_depth: bool = os.getenv("REQUIRE_SUFFICIENT_DEPTH", "true").lower() in ("true", "1", "yes")

    # Dynamic Inventory Skewing & Self-Healing Settings
    enable_inventory_skewing: bool = os.getenv("ENABLE_INVENTORY_SKEWING", "true").lower() in ("true", "1", "yes")
    inventory_sync_interval: float = float(os.getenv("INVENTORY_SYNC_INTERVAL", "12.0"))
    target_inventory_ratio: float = float(os.getenv("TARGET_INVENTORY_RATIO", "0.50"))
    inventory_penalty_factor: float = float(os.getenv("INVENTORY_PENALTY_FACTOR", "1.5"))
    inventory_discount_factor: float = float(os.getenv("INVENTORY_DISCOUNT_FACTOR", "0.6"))
    critical_inventory_threshold_pct: float = float(os.getenv("CRITICAL_INVENTORY_THRESHOLD_PCT", "10.0"))
    priority_coins: List[str] = field(
        default_factory=lambda: [
            c.strip().upper() for c in os.getenv("PRIORITY_COINS", "NEAR,TRX,TON,ADA,ATOM").split(",") if c.strip()
        ]
    )

    # Coin list and fees
    symbols: List[str] = field(default_factory=lambda: DEFAULT_SYMBOLS)
    fees_percent: Dict[str, float] = field(default_factory=lambda: DEFAULT_FEES_PERCENT)
    enabled_exchanges: List[str] = field(
        default_factory=lambda: [
            ex.strip().lower() for ex in os.getenv("ENABLED_EXCHANGES", "nobitex,wallex").split(",") if ex.strip()
        ]
    )


config = BotConfig()
