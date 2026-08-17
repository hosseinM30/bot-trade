#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

COINS = {
    "BTCUSDT": {"symbol": "BTC", "name": "Bitcoin"},
    "ETHUSDT": {"symbol": "ETH", "name": "Ethereum"},
    "SOLUSDT": {"symbol": "SOL", "name": "Solana"},
    "BNBUSDT": {"symbol": "BNB", "name": "BNB"},
    "XRPUSDT": {"symbol": "XRP", "name": "XRP"},
}

INTERVAL = os.getenv("INTERVAL", "15m")
STARTING_CASH = 22.0
POLL_SECONDS = 60

SHORT_PERIOD = 10
LONG_PERIOD = 30
RSI_PERIOD = 14
ATR_PERIOD = 14
VOLUME_PERIOD = 20

RSI_BUY_MIN = 45.0
RSI_BUY_MAX = 68.0
VOLUME_MULTIPLIER = 1.05

STOP_ATR_MULTIPLIER = 1.5
TARGET_ATR_MULTIPLIER = 2.5
TRAILING_ATR_MULTIPLIER = 1.5
TRAILING_ACTIVATION_R = 1.0

FEE_RATE = 0.001
SLIPPAGE_RATE = 0.0005

API_BASE = "https://api.binance.com/api/v3"
TIMEOUT = 15
STATE_FILE = Path(__file__).resolve().parent / "portfolio_state.json"

session = requests.Session()
session.headers["User-Agent"] = "TelegramPaperTrader/1.0"

@dataclass
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float

def parse_klines(rows):
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]

def sma_series(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    running = sum(values[:period])
    out[period-1] = running / period
    for i in range(period, len(values)):
        running += values[i] - values[i-period]
        out[i] = running / period
    return out

def rsi_series(closes, period=RSI_PERIOD):
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains, losses = [], []
    for i in range(1, period+1):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100/(1 + avg_gain/avg_loss)
    for i in range(period+1, len(closes)):
        d = closes[i] - closes[i-1]
        avg_gain = ((avg_gain*(period-1)) + max(d,0.0)) / period
        avg_loss = ((avg_loss*(period-1)) + max(-d,0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100/(1 + avg_gain/avg_loss)
    return out

def true_range_series(candles):
    out = []
    for i, c in enumerate(candles):
        if i == 0:
            out.append(c.high-c.low)
        else:
            p = candles[i-1].close
            out.append(max(c.high-c.low, abs(c.high-p), abs(c.low-p)))
    return out

def atr_series(candles, period=ATR_PERIOD):
    tr = true_range_series(candles)
    out = [None] * len(tr)
    if len(tr) < period:
        return out
    atr = sum(tr[:period]) / period
    out[period-1] = atr
    for i in range(period, len(tr)):
        atr = ((atr*(period-1)) + tr[i]) / period
        out[i] = atr
    return out

def crossover_up(shorts, longs, i):
    if i < 1 or None in (shorts[i], longs[i], shorts[i-1], longs[i-1]):
        return False
    return shorts[i-1] <= longs[i-1] and shorts[i] > longs[i]

def crossover_down(shorts, longs, i):
    if i < 1 or None in (shorts[i], longs[i], shorts[i-1], longs[i-1]):
        return False
    return shorts[i-1] >= longs[i-1] and shorts[i] < longs[i]

def indicators(candles):
    closes = [c.close for c in candles]
    vols = [c.volume for c in candles]
    return {
        "sma_short": sma_series(closes, SHORT_PERIOD),
        "sma_long": sma_series(closes, LONG_PERIOD),
        "rsi": rsi_series(closes),
        "atr": atr_series(candles),
        "volume_avg": sma_series(vols, VOLUME_PERIOD),
    }

def buy_signal(candles, ind, i):
    c = candles[i]
    ss, sl = ind["sma_short"][i], ind["sma_long"][i]
    rsi, atr, va = ind["rsi"][i], ind["atr"][i], ind["volume_avg"][i]
    if None in (ss, sl, rsi, atr, va):
        return False, "not enough history"
    if not crossover_up(ind["sma_short"], ind["sma_long"], i):
        return False, "no bullish crossover"
    prev_sl = ind["sma_long"][i-1]
    if prev_sl is None or c.close <= sl or sl <= prev_sl:
        return False, "trend filter failed"
    if not (RSI_BUY_MIN <= rsi <= RSI_BUY_MAX):
        return False, f"RSI {rsi:.1f} outside {RSI_BUY_MIN:.0f}-{RSI_BUY_MAX:.0f}"
    if c.volume < va * VOLUME_MULTIPLIER:
        return False, "volume confirmation failed"
    return True, f"SMA{SHORT_PERIOD}>{LONG_PERIOD} cross + trend + RSI {rsi:.1f} + volume"

def exit_reason(candles, ind, i, pos):
    c = candles[i]
    entry = float(pos["entry_price"])
    stop = float(pos["stop_price"])
    target = float(pos["target_price"])
    atr = ind["atr"][i]
    peak = max(float(pos.get("peak_price", entry)), c.high)
    pos["peak_price"] = peak

    if c.low <= stop:
        return "ATR stop loss"
    if c.high >= target:
        return "ATR take profit"

    risk = max(entry - float(pos["initial_stop_price"]), 0.0)
    if risk > 0 and peak >= entry + risk * TRAILING_ACTIVATION_R and atr is not None:
        new_trail = peak - atr * TRAILING_ATR_MULTIPLIER
        pos["stop_price"] = max(float(pos["stop_price"]), new_trail)
        if c.low <= float(pos["stop_price"]):
            return "trailing stop"

    if crossover_down(ind["sma_short"], ind["sma_long"], i):
        return "bearish SMA crossover"
    rsi = ind["rsi"][i]
    if rsi is not None and rsi >= 75:
        return f"RSI overbought ({rsi:.1f})"
    return None

def default_coin_state():
    return {"cash": STARTING_CASH, "position": None, "trades": []}

def default_state():
    return {
        "running": False,
        "last_processed_candle": {},
        "coins": {cid: default_coin_state() for cid in COINS},
    }

def load_state():
    if not STATE_FILE.exists():
        return default_state()
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        base = default_state()
        base.update({k:v for k,v in s.items() if k in ("running","last_processed_candle")})
        for cid in COINS:
            if cid in s.get("coins", {}):
                base["coins"][cid].update(s["coins"][cid])
        return base
    except Exception:
        return default_state()

def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)

def fetch_candles(symbol, limit=250):
    r = session.get(
        f"{API_BASE}/klines",
        params={"symbol": symbol, "interval": INTERVAL, "limit": min(limit, 1000)},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    candles = parse_klines(r.json())
    if len(candles) >= 2:
        candles = candles[:-1]  # only closed candles
    if len(candles) < LONG_PERIOD + 2:
        raise ValueError("not enough closed candles")
    return candles

def current_value(cs, price):
    if not cs.get("position"):
        return float(cs["cash"])
    return float(cs["cash"]) + float(cs["position"]["amount"]) * price

def execution_buy_price(close):
    return close * (1 + SLIPPAGE_RATE)

def execution_sell_price(close):
    return close * (1 - SLIPPAGE_RATE)

def open_position(cs, candle, atr, reason):
    cash = float(cs["cash"])
    if cash <= 0.01:
        return
    entry = execution_buy_price(candle.close)
    stop = max(entry - atr * STOP_ATR_MULTIPLIER, entry * 0.5)
    target = entry + atr * TARGET_ATR_MULTIPLIER
    fee = cash * FEE_RATE
    amount = max(cash - fee, 0.0) / entry
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cs["cash"] = 0.0
    cs["position"] = {
        "amount": amount, "entry_price": entry, "entry_value": cash,
        "stop_price": stop, "initial_stop_price": stop,
        "target_price": target, "peak_price": entry, "entry_time": now
    }
    cs["trades"].append({
        "time": now, "type": "BUY", "price": entry, "amount": amount,
        "value_usd": cash, "fee_usd": fee, "reason": reason
    })

def close_position(cs, candle, reason):
    pos = cs.get("position")
    if not pos:
        return None
    amount = float(pos["amount"])
    exit_price = execution_sell_price(candle.close)
    gross = amount * exit_price
    fee = gross * FEE_RATE
    gained = max(gross - fee, 0.0)
    pnl = gained - float(pos["entry_value"])
    pnl_pct = pnl / float(pos["entry_value"]) * 100 if pos["entry_value"] else 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cs["cash"] = gained
    cs["position"] = None
    trade = {
        "time": now, "type": "SELL", "price": exit_price, "amount": amount,
        "value_usd": gained, "fee_usd": fee, "pnl_usd": pnl,
        "pnl_pct": pnl_pct, "reason": reason
    }
    cs["trades"].append(trade)
    return trade

def evaluate_coin(cs, candles):
    ind = indicators(candles)
    i = len(candles)-1
    candle = candles[i]
    signal = "HOLD"
    event = None

    if cs.get("position"):
        reason = exit_reason(candles, ind, i, cs["position"])
        if reason:
            trade = close_position(cs, candle, reason)
            signal = "SELL"
            event = {"type": "SELL", "trade": trade}
    else:
        atr = ind["atr"][i]
        if atr is not None:
            ok, why = buy_signal(candles, ind, i)
            if ok:
                open_position(cs, candle, atr, why)
                signal = "BUY"
                event = {"type": "BUY", "price": cs["position"]["entry_price"],
                         "amount": cs["position"]["amount"], "reason": why}

    return {
        "price": candle.close,
        "signal": signal,
        "rsi": ind["rsi"][i],
        "value": current_value(cs, candle.close),
        "candle_time": candle.open_time,
        "event": event,
    }

def run_cycle():
    state = load_state()
    if not state["running"]:
        return state, []

    notifications = []
    for cid in COINS:
        try:
            candles = fetch_candles(cid)
            latest_time = candles[-1].open_time
            # Process each closed candle only once.
            if state["last_processed_candle"].get(cid) == latest_time:
                continue
            result = evaluate_coin(state["coins"][cid], candles)
            state["last_processed_candle"][cid] = latest_time
            if result["event"]:
                notifications.append((cid, result))
        except Exception as e:
            notifications.append(("ERROR", {"error": f"{COINS[cid]['symbol']}: {e}"}))

    save_state(state)
    return state, notifications
