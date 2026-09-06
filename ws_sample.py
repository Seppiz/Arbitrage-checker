import asyncio
import json
import sys
from datetime import datetime
import websockets

# Configure UTF-8 encoding on Windows to support emojis and symbols
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# Global in-memory cache for the latest best bid & ask
latest_quotes = {
    "nobitex": {"bid": None, "ask": None},
    "bitpin": {"bid": None, "ask": None},
}


def print_arbitrage_comparison():
    """Calculates and prints real-time spread whenever any exchange price updates."""
    nob = latest_quotes["nobitex"]
    bp = latest_quotes["bitpin"]

    if nob["bid"] and nob["ask"] and bp["bid"] and bp["ask"]:
        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now_str}] 📊 LIVE QUOTE UPDATE:", flush=True)
        print(f"  • Nobitex -> Best Bid: {nob['bid']:,.2f} USDT | Best Ask: {nob['ask']:,.2f} USDT", flush=True)
        print(f"  • Bitpin  -> Best Bid: {bp['bid']:,.2f} USDT | Best Ask: {bp['ask']:,.2f} USDT", flush=True)

        # Opportunity 1: Buy on Nobitex, Sell on Bitpin
        spread1 = ((bp["bid"] - nob["ask"]) / nob["ask"]) * 100
        # Opportunity 2: Buy on Bitpin, Sell on Nobitex
        spread2 = ((nob["bid"] - bp["ask"]) / bp["ask"]) * 100

        print(f"  ⚡ Buy Nobitex / Sell Bitpin Spread: {spread1:+.3f}%", flush=True)
        print(f"  ⚡ Buy Bitpin / Sell Nobitex Spread: {spread2:+.3f}%", flush=True)
        print("-" * 65, flush=True)


async def nobitex_ws_client(symbol: str = "BTCUSDT"):
    """
    Connects to Nobitex WebSocket (Centrifugo Protocol)
    Channel: public:orderbook-{SYMBOL}
    """
    uri = "wss://ws.nobitex.ir/connection/websocket"
    channel = f"public:orderbook-{symbol}"

    while True:
        try:
            print(f"🌐 [Nobitex] Connecting to {uri}...")
            async with websockets.connect(uri, ping_interval=None) as ws:
                # 1. Connect handshake
                await ws.send(json.dumps({"id": 1, "connect": {}}))
                await ws.recv()

                # 2. Subscribe to orderbook channel
                await ws.send(json.dumps({
                    "id": 2,
                    "subscribe": {"channel": channel}
                }))
                await ws.recv()
                print(f"✅ [Nobitex] Subscribed to {channel}!")

                # 3. Message loop
                async for message in ws:
                    # Keep-alive heartbeat: server sends empty '{}', respond with '{}'
                    if message.strip() == "{}":
                        await ws.send("{}")
                        continue

                    data = json.loads(message)
                    pub = data.get("push", {}).get("pub", {}).get("data", {})

                    bids = pub.get("bids", [])
                    asks = pub.get("asks", [])

                    if bids and asks:
                        latest_quotes["nobitex"]["bid"] = float(bids[0][0])
                        latest_quotes["nobitex"]["ask"] = float(asks[0][0])
                        print_arbitrage_comparison()

        except Exception as e:
            print(f"❌ [Nobitex WS Error]: {e}. Reconnecting in 3s...")
            await asyncio.sleep(3)


async def bitpin_ws_client(symbol: str = "BTC_USDT"):
    """
    Connects to Bitpin WebSocket (Centrifugo Protocol)
    Channel: orderbook:{SYMBOL}
    """
    uri = "wss://centrifugo.bitpin.org/connection/websocket"
    channel = f"orderbook:{symbol}"

    while True:
        try:
            print(f"🌐 [Bitpin] Connecting to {uri}...")
            async with websockets.connect(uri, ping_interval=None) as ws:
                # 1. Connect handshake
                await ws.send(json.dumps({"id": 1, "connect": {}}))
                await ws.recv()

                # 2. Subscribe to orderbook channel
                await ws.send(json.dumps({
                    "id": 2,
                    "subscribe": {"channel": channel}
                }))
                await ws.recv()
                print(f"✅ [Bitpin] Subscribed to {channel}!")

                # 3. Message loop
                async for message in ws:
                    # Keep-alive heartbeat: server sends empty '{}', respond with '{}'
                    if message.strip() == "{}":
                        await ws.send("{}")
                        continue

                    data = json.loads(message)
                    pub = data.get("push", {}).get("pub", {}).get("data", {})

                    bids = pub.get("bids", [])
                    asks = pub.get("asks", [])

                    if bids and asks:
                        latest_quotes["bitpin"]["bid"] = float(bids[0][0])
                        latest_quotes["bitpin"]["ask"] = float(asks[0][0])
                        print_arbitrage_comparison()

        except Exception as e:
            print(f"❌ [Bitpin WS Error]: {e}. Reconnecting in 3s...")
            await asyncio.sleep(3)


async def main():
    print("=" * 65)
    print("🚀 Real-Time Exchange WebSocket Arbitrage Sample")
    print("Streaming live orderbook data for BTC/USDT...")
    print("=" * 65)

    await asyncio.gather(
        nobitex_ws_client("BTCUSDT"),
        bitpin_ws_client("BTC_USDT"),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Stopped by user.")
