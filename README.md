# Real-Time WebSocket Crypto Arbitrage & Auto-Execution Bot

A high-speed cryptocurrency arbitrage trading bot that streams real-time orderbooks across three Iranian exchanges (**Nobitex**, **Bitpin**, and **Wallex**) via WebSockets and automatically executes concurrent arbitrage trades using personal API keys.

---

## Key Features

- ⚡ **Multi-Channel WebSocket Streaming**: Connects directly to Nobitex and Bitpin WebSockets (Centrifugo protocol) streaming 36+ pairs simultaneously with zero polling latency, plus high-frequency bulk streaming for Wallex.
- 🤖 **Automated Concurrent Execution**: Dispatches atomic Buy and Sell orders concurrently (`asyncio.gather`) within milliseconds of spread detection.
- 🛡️ **Risk & Safety Controls**:
  - `DRY_RUN` mode enabled by default for paper simulation.
  - Minimum net profit (`MIN_PROFIT_USDT`) and minimum ROI percentage (`MIN_ROI_PCT`) thresholds.
  - Cooldown timer per cryptocurrency to prevent duplicate executions on price spikes.
  - Pre-flight balance checks before live order placement.
  - Emergency halt (`/kill`) switch via Telegram.
- 🎯 **Preserved User Logic**: Retains the user's exact 36+ coins list, fee structures, and net profit evaluation formula from `main.py`.
- 📱 **Interactive Telegram Bot**:
  - Receive instant execution receipts and trade alerts.
  - `/status` — View active settings, trade counts, and session profit.
  - `/balance` — View live wallet balances across Nobitex, Bitpin, and Wallex.
  - `/dryrun [on|off]` — Switch between simulation mode and real money execution.
  - `/capital <usdt>` — Dynamically change trade amount.
  - `/minprofit <pct>` — Dynamically adjust minimum ROI target.
  - `/kill` & `/resume` — Emergency stop and resume trading.

---

## Supported Exchanges

| Exchange | WebSocket Protocol | REST Execution API | Default Taker Fee |
|----------|-------------------|-------------------|-------------------|
| **Nobitex** | Centrifugo (`wss://ws.nobitex.ir`) | `/market/orders/add` | 0.15% |
| **Bitpin**  | Centrifugo (`wss://centrifugo.bitpin.org`) | `/v1/odr/orders/` | 0.15% |
| **Wallex**  | Bulk REST Streamer (`/v1/markets`) | `/v1/account/orders` | 0.20% |

---

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
# or:
pip install websockets httpx python-dotenv
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your keys:
```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_ADMIN_IDS=your_telegram_chat_id

# Exchange API Credentials
NOBITEX_API_TOKEN=your_nobitex_token
BITPIN_API_KEY=your_bitpin_api_key
WALLEX_API_KEY=your_wallex_api_key

# Safety & Trading Settings
DRY_RUN=true
TRADE_AMOUNT_USDT=100.0
MIN_PROFIT_USDT=0.20
MIN_ROI_PCT=0.35
```

### 3. Run Automated Unit Tests
```bash
python test_bot.py
```

### 4. Start the Bot
```bash
python main.py
```

---

## Dual-Exchange Inventory Model
To execute arbitrage trades instantly without waiting for blockchain network confirmations:
1. Maintain a pre-funded balance of **USDT** on the buying exchange.
2. Maintain a pre-funded balance of the **Coin** on the selling exchange.
3. When spread is detected, the bot buys the coin and sells it simultaneously on the respective exchanges.
