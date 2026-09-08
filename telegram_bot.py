import asyncio
import json
import os
from typing import List, Optional
import aiofiles
import httpx
from config import config
from exchanges import NobitexClient, BitpinClient, WallexClient


class SubscriberManager:
    """
    Asynchronous, non-blocking subscriber manager using aiofiles and asyncio.Lock.
    Guarantees the asyncio event loop is never blocked by disk I/O.
    """
    def __init__(self, filename: str):
        self.filename = filename
        self.subscribers: set = set()
        self._lock = asyncio.Lock()
        self._loaded: bool = False

    async def load(self) -> set:
        """Asynchronously load subscribers from disk using aiofiles."""
        if not os.path.exists(self.filename):
            self._loaded = True
            return set()
        try:
            async with aiofiles.open(self.filename, mode="r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)
                self.subscribers = set(data.get("subscribers", []))
                self._loaded = True
                return self.subscribers
        except (FileNotFoundError, json.JSONDecodeError):
            self._loaded = True
            return set()
        except Exception as e:
            print(f"[Error] Failed to load subscribers with aiofiles: {e}", flush=True)
            self._loaded = True
            return set()

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self.load()

    async def _save_subscribers(self) -> None:
        """Asynchronously write subscribers to disk using aiofiles."""
        try:
            async with self._lock:
                async with aiofiles.open(self.filename, mode="w", encoding="utf-8") as f:
                    payload = json.dumps({"subscribers": list(self.subscribers)}, indent=2)
                    await f.write(payload)
        except Exception as e:
            print(f"[Error] Failed to save subscribers with aiofiles: {e}", flush=True)

    async def add_subscriber(self, chat_id: str) -> bool:
        await self._ensure_loaded()
        if chat_id not in self.subscribers:
            self.subscribers.add(chat_id)
            await self._save_subscribers()
            return True
        return False

    async def remove_subscriber(self, chat_id: str) -> bool:
        await self._ensure_loaded()
        if chat_id in self.subscribers:
            self.subscribers.remove(chat_id)
            await self._save_subscribers()
            return True
        return False

    def get_all_subscribers(self) -> list:
        return list(self.subscribers)

    def count(self) -> int:
        return len(self.subscribers)


class TelegramBot:
    def __init__(
        self,
        token: str,
        admin_ids: List[str],
        subscriber_manager: SubscriberManager,
    ):
        self.token = token
        self.admin_ids = admin_ids
        self.sub_mgr = subscriber_manager
        self.update_offset = 0

    async def send_message(self, client: httpx.AsyncClient, chat_id: str, text: str) -> bool:
        if not self.token:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        try:
            res = await client.post(url, json=payload, timeout=5.0)
            if res.status_code == 400 and "can't parse entities" in res.text:
                payload.pop("parse_mode", None)
                res = await client.post(url, json=payload, timeout=5.0)
            res.raise_for_status()
            return True
        except Exception as e:
            print(f"\n[Telegram Error] Failed to send to {chat_id}: {e}", flush=True)
            return False

    async def broadcast(self, client: httpx.AsyncClient, text: str, admin_only: bool = False) -> None:
        if not self.token:
            return
        # If admin_only is True, strictly send to admin_ids to prevent sensitive leaks
        recipients = self.admin_ids if admin_only else self.sub_mgr.get_all_subscribers()
        if not recipients:
            return
        tasks = [self.send_message(client, chat_id, text) for chat_id in recipients]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def process_updates(
        self,
        client: httpx.AsyncClient,
        executor,
        nobitex: NobitexClient,
        bitpin: BitpinClient,
        wallex: WallexClient,
    ):
        if not self.token:
            return

        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params = {"offset": self.update_offset, "timeout": 0}

        try:
            res = await client.get(url, params=params, timeout=5.0)
            if res.status_code != 200:
                return
            data = res.json()
            updates = data.get("result", [])

            for upd in updates:
                upd_id = upd.get("update_id", 0)
                self.update_offset = max(self.update_offset, upd_id + 1)

                msg = upd.get("message")
                if not msg:
                    continue

                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip()
                is_admin = chat_id in self.admin_ids

                if text == "/start":
                    if await self.sub_mgr.add_subscriber(chat_id):
                        reply = (
                            "🚀 <b>Welcome to the High-Speed Arbitrage Bot!</b>\n\n"
                            "You will receive real-time alerts and execution receipts.\n\n"
                            "<b>Available Commands:</b>\n"
                            "/start - Subscribe to alerts\n"
                            "/stop - Unsubscribe\n"
                            "/status - View live bot settings\n"
                            "/balance - View exchange balances\n"
                            "/help - Show help menu"
                        )
                    else:
                        reply = "✅ You are already subscribed to alerts!"
                    await self.send_message(client, chat_id, reply)

                elif text == "/stop":
                    if await self.sub_mgr.remove_subscriber(chat_id):
                        reply = "👋 You have been unsubscribed. Send /start to rejoin."
                    else:
                        reply = "ℹ️ You were not subscribed."
                    await self.send_message(client, chat_id, reply)

                elif text == "/status":
                    mode = "🧪 PAPER TRADING (Simulation)" if config.dry_run else "⚡ LIVE TRADING (Real Money)"
                    halt_status = "🔴 HALTED" if executor.is_halted else "🟢 ACTIVE"
                    reply = (
                        f"📊 <b>Arbitrage Bot Status</b>\n\n"
                        f"• Engine: {halt_status}\n"
                        f"• Mode: {mode}\n"
                        f"• Monitored Coins: {len(config.symbols)}\n"
                        f"• Capital per Trade: {config.trade_amount_usdt:,.0f} USDT\n"
                        f"• Min Profit: {config.min_profit_usdt:.2f} USDT (Min {config.min_roi_pct:.2f}% ROI)\n"
                        f"• Cooldown: {config.trade_cooldown_seconds}s per coin\n"
                        f"• Total Executed: {executor.total_trades_count} trades\n"
                        f"• Session Profit: {executor.total_realized_profit_usdt:+.2f} USDT\n"
                        f"• Subscribers: {self.sub_mgr.count()}"
                    )
                    await self.send_message(client, chat_id, reply)

                elif text == "/balance":
                    if not is_admin:
                        await self.send_message(client, chat_id, "⛔ <b>Access Denied:</b> دسترسی به موجودی حساب تنها برای ادمین مجاز است.")
                        continue
                    await self.send_message(client, chat_id, "⏳ در حال استعلام موجودی از نوبیتکس، بیت‌پین و والکس...")
                    nob_bals, bp_bals, wlx_bals = await asyncio.gather(
                        nobitex.get_all_balances(client),
                        bitpin.get_all_balances(client),
                        wallex.get_all_balances(client),
                        return_exceptions=True,
                    )
                    def fmt_bals(bals, name, has_key):
                        if not has_key:
                            return f"🏛 <b>{name}</b>:\n  ⚠️ <i>کلید API در .env وارد نشده (موجودی شبیه‌سازی تستی):</i>\n  • USDT: 1,000.00"
                        if isinstance(bals, dict):
                            top_items = [f"{k}: {v:,.4g}" for k, v in list(bals.items())[:8]]
                            return f"🏛 <b>{name}</b>:\n" + ("\n".join(f"  • {i}" for i in top_items) if top_items else "  • (کیف‌پول خالی است)")
                        return f"🏛 <b>{name}</b>: خطا در استعلام: {bals}"

                    msg_bal = (
                        "💼 <b>موجودی حساب شما در صرافی‌ها:</b>\n\n"
                        f"{fmt_bals(nob_bals, 'Nobitex (نوبیتکس)', bool(nobitex.api_key))}\n\n"
                        f"{fmt_bals(bp_bals, 'Bitpin (بیت‌پین)', bool(bitpin.api_key))}\n\n"
                        f"{fmt_bals(wlx_bals, 'Wallex (والکس)', bool(wallex.api_key))}"
                    )
                    await self.send_message(client, chat_id, msg_bal)

                elif text.startswith("/dryrun") and is_admin:
                    parts = text.split()
                    if len(parts) > 1 and parts[1].lower() in ("off", "false", "0"):
                        config.dry_run = False
                        nobitex.dry_run = False
                        bitpin.dry_run = False
                        wallex.dry_run = False
                        await self.send_message(client, chat_id, "⚠️ <b>WARNING: LIVE TRADING ENABLED!</b> Real funds will be used.")
                    else:
                        config.dry_run = True
                        nobitex.dry_run = True
                        bitpin.dry_run = True
                        wallex.dry_run = True
                        await self.send_message(client, chat_id, "🧪 <b>SIMULATION / DRY-RUN ENABLED.</b> Paper trading only.")

                elif text.startswith("/capital") and is_admin:
                    try:
                        val = float(text.split()[1])
                        config.trade_amount_usdt = val
                        await self.send_message(client, chat_id, f"✅ Trade capital set to <b>{val:,.2f} USDT</b>")
                    except Exception:
                        await self.send_message(client, chat_id, "Usage: <code>/capital 150</code>")

                elif text.startswith("/minprofit") and is_admin:
                    try:
                        val = float(text.split()[1])
                        config.min_roi_pct = val
                        await self.send_message(client, chat_id, f"✅ Minimum ROI threshold set to <b>{val:.2f}%</b>")
                    except Exception:
                        await self.send_message(client, chat_id, "Usage: <code>/minprofit 0.45</code>")

                elif text == "/kill" and is_admin:
                    executor.is_halted = True
                    await self.send_message(client, chat_id, "🛑 <b>EMERGENCY HALT ACTIVATED!</b> All automated trading paused.")

                elif text == "/resume" and is_admin:
                    executor.is_halted = False
                    await self.send_message(client, chat_id, "▶️ <b>Trading resumed.</b>")

                elif text == "/help":
                    admin_help = (
                        "\n\n<b>Admin Controls:</b>\n"
                        "/dryrun [on|off] - Toggle simulation mode\n"
                        "/capital [usdt] - Change trade amount\n"
                        "/minprofit [roi%] - Change min profit threshold\n"
                        "/kill - Emergency stop\n"
                        "/resume - Resume trading"
                        if is_admin else ""
                    )
                    help_msg = (
                        "🤖 <b>Arbitrage Bot Commands:</b>\n\n"
                        "/start - Subscribe to alerts\n"
                        "/stop - Unsubscribe\n"
                        "/status - Check live status\n"
                        "/balance - View wallet balances\n"
                        "/help - Show this guide"
                        f"{admin_help}"
                    )
                    await self.send_message(client, chat_id, help_msg)

        except Exception as e:
            # Silently catch transient network issues
            pass
