#!/usr/bin/env python3
from __future__ import annotations
import os, requests
from trader import load_state, save_state, run_cycle, COINS, INTERVAL, STARTING_CASH, STATE_FILE

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER_CHAT_ID = str(os.environ["OWNER_CHAT_ID"])
API = f"https://api.telegram.org/bot{TOKEN}"
TIMEOUT = 20

def tg(method, **data):
    r = requests.post(f"{API}/{method}", json=data, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def keyboard():
    return {
        "keyboard": [
            [{"text":"▶️ شروع"}, {"text":"⏹ توقف"}],
            [{"text":"💰 موجودی"}, {"text":"📊 وضعیت"}],
            [{"text":"📜 معاملات اخیر"}],
        ],
        "resize_keyboard": True,
    }

def send(text):
    tg("sendMessage", chat_id=OWNER_CHAT_ID, text=text, reply_markup=keyboard())

def money(x):
    return f"${x:,.2f}"

def status_text(state):
    total = 0.0
    lines = [f"📊 وضعیت Paper Trader\nTimeframe: {INTERVAL}",
             f"وضعیت: {'🟢 در حال اجرا' if state['running'] else '🔴 متوقف'}\n"]
    for cid, meta in COINS.items():
        cs = state["coins"][cid]
        pos = cs.get("position")
        # Without a fresh market price, report cash/position state.
        if pos:
            val = float(pos["entry_value"])
            text = f"LONG | entry {money(pos['entry_price'])}"
        else:
            val = float(cs["cash"])
            text = "CASH"
        total += val
        lines.append(f"{meta['symbol']}: {money(val)} | {text}")
    lines.append(f"\nسرمایه اسمی اولیه: {money(STARTING_CASH*len(COINS))}")
    lines.append("توجه: موجودی دقیق mark-to-market در چرخه بعدی به‌روزرسانی می‌شود.")
    return "\n".join(lines)

def balance_text(state):
    total_cash = 0.0
    total_entry = 0.0
    lines = ["💰 موجودی Paper Trader\n"]
    for cid, meta in COINS.items():
        cs = state["coins"][cid]
        pos = cs.get("position")
        if pos:
            value = float(pos["entry_value"])
            total_entry += value
            lines.append(f"{meta['symbol']}: {money(value)} | 🟢 پوزیشن باز")
        else:
            value = float(cs["cash"])
            total_cash += value
            lines.append(f"{meta['symbol']}: {money(value)} | 💵 نقد")
    lines.append(f"\nCash: {money(total_cash)}")
    lines.append(f"Capital in open positions: {money(total_entry)}")
    return "\n".join(lines)

def recent_text(state):
    rows = []
    for cid, meta in COINS.items():
        for t in state["coins"][cid].get("trades", [])[-3:]:
            pnl = t.get("pnl_usd")
            if t["type"] == "BUY":
                rows.append(f"🟢 {meta['symbol']} BUY {money(t['price'])}")
            else:
                icon = "🟢" if pnl >= 0 else "🔴"
                rows.append(f"{icon} {meta['symbol']} SELL {money(t['price'])} | P&L {pnl:+.2f} | {t.get('reason','')}")
    return "📜 آخرین معاملات\n\n" + ("\n".join(rows[-10:]) if rows else "هنوز معامله‌ای ثبت نشده.")

def format_event(cid, result):
    meta = COINS[cid]["symbol"]
    ev = result["event"]
    if ev["type"] == "BUY":
        return (f"🟢 BUY — {meta}\n\n"
                f"Price: {money(ev['price'])}\n"
                f"Amount: {ev['amount']:.8f}\n"
                f"Reason: {ev['reason']}")
    tr = ev["trade"]
    pnl = tr["pnl_usd"]
    icon = "💰" if pnl >= 0 else "🔴"
    return (f"{icon} {'PROFIT' if pnl >= 0 else 'LOSS'} — {meta}\n\n"
            f"Entry/Exit: {money(0)} → {money(tr['price'])}\n"
            f"P&L: {pnl:+.2f} ({tr['pnl_pct']:+.2f}%)\n"
            f"Reason: {tr['reason']}")

def process_commands():
    # Process a small batch of pending updates. Offset is intentionally stored in a file
    # so GitHub Actions runs do not repeat old commands.
    state = load_state()
    offset_file = STATE_FILE.with_name("telegram_offset.txt")
    try:
        offset = int(offset_file.read_text().strip())
    except Exception:
        offset = 0

    data = tg("getUpdates", offset=offset, timeout=1, allowed_updates=["message"])
    updates = data.get("result", [])
    if not updates:
        return

    for u in updates:
        offset = max(offset, int(u["update_id"]) + 1)
        msg = u.get("message") or {}
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != OWNER_CHAT_ID:
            continue
        text = (msg.get("text") or "").strip()

        if text in ("/start", "▶️ شروع"):
            state["running"] = True
            save_state(state)
            send("▶️ Paper Trader شروع شد.\nاستراتژی روی کندل‌های بسته‌شده 15m اجرا می‌شود.")
        elif text in ("/stop", "⏹ توقف"):
            state["running"] = False
            save_state(state)
            send("⏹ Paper Trader متوقف شد.\nوضعیت و پوزیشن‌ها حفظ شدند.")
        elif text in ("/balance", "💰 موجودی"):
            send(balance_text(state))
        elif text in ("/status", "📊 وضعیت"):
            send(status_text(state))
        elif text in ("/trades", "📜 معاملات اخیر"):
            send(recent_text(state))
        elif text in ("/help", "کمک"):
            send("از دکمه‌ها استفاده کن:\n▶️ شروع\n⏹ توقف\n💰 موجودی\n📊 وضعیت\n📜 معاملات اخیر")
        else:
            send("دستور نامعتبر است. از دکمه‌های پایین استفاده کن.")

    offset_file.write_text(str(offset), encoding="utf-8")

def main():
    process_commands()
    state, events = run_cycle()
    for cid, result in events:
        if cid == "ERROR":
            send(f"⚠️ خطا در بررسی بازار\n{result['error']}")
        else:
            send(format_event(cid, result))

if __name__ == "__main__":
    main()
