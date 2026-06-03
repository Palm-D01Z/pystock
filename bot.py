#!/usr/bin/env python3
"""
bot.py — Telegram bot จัดการ watchlist + แจ้งเตือนหุ้นอัตโนมัติ

คำสั่ง (พิมพ์ใน Telegram):
  /list                    — ดู watchlist ปัจจุบัน
  /set TICKER above PRICE  — ตั้งเป้าบน (เตือนเมื่อราคา >= PRICE)
  /set TICKER below PRICE  — ตั้งเป้าล่าง (เตือนเมื่อราคา <= PRICE)
  /set TICKER rsi_ob VALUE — ตั้ง RSI overbought
  /set TICKER rsi_os VALUE — ตั้ง RSI oversold
  /remove TICKER           — ลบ ticker ทั้งหมด
  /remove TICKER above     — ลบเฉพาะเงื่อนไข above
  /check                   — ตรวจราคาทันที
  /help                    — แสดงคำสั่งทั้งหมด

รัน:
  python bot.py
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# import ฟังก์ชันตรวจหุ้นจาก stock_alert.py
from stock_alert import (
    CHECK_INTERVAL_MIN,
    check_ticker,
    load_state,
    save_state,
)

WATCHLIST_FILE = Path(__file__).parent / "watchlist.json"
_wl_lock = threading.Lock()

HELP_TEXT = (
    "📊 Stock Alert Bot — คำสั่งทั้งหมด\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    "📋 /list\n"
    "ดู watchlist ปัจจุบันพร้อมเงื่อนไขทุกตัว\n\n"

    "➕ /set TICKER เงื่อนไข ค่า\n"
    "เพิ่มหุ้นใหม่หรือแก้เงื่อนไขที่มีอยู่\n"
    "เงื่อนไขที่ใช้ได้:\n"
    "  above  — เตือนเมื่อราคา >= ค่า (take-profit)\n"
    "  below  — เตือนเมื่อราคา <= ค่า (stop-loss / ซื้อเพิ่ม)\n"
    "  rsi_ob — เตือนเมื่อ RSI >= ค่า (overbought, ปกติ 70)\n"
    "  rsi_os — เตือนเมื่อ RSI <= ค่า (oversold, ปกติ 30)\n"
    "ตัวอย่าง:\n"
    "  /set NVDA below 200\n"
    "  /set TSLA above 400\n"
    "  /set AMD rsi_ob 75\n\n"

    "❌ /remove TICKER\n"
    "ลบหุ้นออกจาก watchlist ทั้งหมด\n"
    "ตัวอย่าง: /remove NVDA\n\n"

    "❌ /remove TICKER เงื่อนไข\n"
    "ลบเฉพาะเงื่อนไขนั้น (ตัวอื่นยังอยู่)\n"
    "ตัวอย่าง: /remove NVDA above\n\n"

    "🔍 /check\n"
    "ตรวจราคาและ RSI ทุกตัวใน watchlist ทันที\n"
    "ไม่ต้องรอรอบอัตโนมัติ\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "⏰ Bot ตรวจราคาอัตโนมัติทุก 15 นาที\n"
    "แจ้งเตือนเฉพาะตอนเงื่อนไขเพิ่งเข้าเกณฑ์\n"
    "(ไม่สแปมซ้ำถ้าราคายังอยู่ในโซนเดิม)"
)

KEY_ALIASES = {
    "above": "above",
    "below": "below",
    "rsi_ob": "rsi_overbought",
    "rsi_overbought": "rsi_overbought",
    "rsi_os": "rsi_oversold",
    "rsi_oversold": "rsi_oversold",
}


# ---------- env ----------
def _token() -> str:
    t = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not t:
        raise RuntimeError("ยังไม่ได้ตั้ง TELEGRAM_BOT_TOKEN")
    return t


def _chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "")


# ---------- Telegram API ----------
def _tg(method: str, **kwargs) -> dict:
    r = requests.post(
        f"https://api.telegram.org/bot{_token()}/{method}",
        json=kwargs,
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def send(chat_id, text: str) -> None:
    try:
        _tg("sendMessage", chat_id=chat_id, text=text)
    except Exception as e:
        print(f"[send error] {e}")


# ---------- Watchlist (JSON) ----------
def load_watchlist() -> dict:
    if WATCHLIST_FILE.exists():
        try:
            return json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    # fallback: อ่านจาก config.py ครั้งแรก
    try:
        import config
        return {k: dict(v) for k, v in config.WATCHLIST.items()}
    except ImportError:
        return {}


def save_watchlist(wl: dict) -> None:
    WATCHLIST_FILE.write_text(json.dumps(wl, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------- คำสั่ง ----------
def cmd_list(chat_id, wl: dict) -> None:
    if not wl:
        send(chat_id, "Watchlist ว่างเปล่า — ใช้ /set TICKER below PRICE เพื่อเพิ่ม")
        return
    lines = ["📋 Watchlist ปัจจุบัน:"]
    for ticker, cfg in wl.items():
        parts = []
        if "above" in cfg:
            parts.append(f"above ${cfg['above']:,.2f}")
        if "below" in cfg:
            parts.append(f"below ${cfg['below']:,.2f}")
        if "rsi_overbought" in cfg:
            parts.append(f"RSI OB ≥{cfg['rsi_overbought']}")
        if "rsi_oversold" in cfg:
            parts.append(f"RSI OS ≤{cfg['rsi_oversold']}")
        lines.append(f"  {ticker}: {', '.join(parts) if parts else '(ไม่มีเงื่อนไข)'}")
    send(chat_id, "\n".join(lines))


def cmd_set(chat_id, args: list, wl: dict) -> dict:
    if len(args) != 3:
        send(chat_id, "รูปแบบ: /set TICKER above/below PRICE\nเช่น: /set NVDA below 200")
        return wl
    ticker, raw_key, val_str = args[0].upper(), args[1].lower(), args[2]
    key = KEY_ALIASES.get(raw_key)
    if not key:
        send(chat_id, f"key ไม่ถูกต้อง ใช้ได้: above, below, rsi_ob, rsi_os")
        return wl
    try:
        val = float(val_str)
    except ValueError:
        send(chat_id, f"ค่าต้องเป็นตัวเลข เช่น: /set NVDA below 200")
        return wl
    with _wl_lock:
        if ticker not in wl:
            wl[ticker] = {}
        wl[ticker][key] = val
        save_watchlist(wl)
    send(chat_id, f"✅ บันทึกแล้ว\n{ticker} {key} = {val:,.2f}")
    return wl


def cmd_remove(chat_id, args: list, wl: dict) -> dict:
    if not args:
        send(chat_id, "รูปแบบ: /remove TICKER\nหรือ /remove TICKER above เพื่อลบเฉพาะเงื่อนไข")
        return wl
    ticker = args[0].upper()
    if ticker not in wl:
        send(chat_id, f"{ticker} ไม่อยู่ใน watchlist")
        return wl
    with _wl_lock:
        if len(args) >= 2:
            raw_key = args[1].lower()
            key = KEY_ALIASES.get(raw_key, raw_key)
            if key in wl[ticker]:
                del wl[ticker][key]
                if not wl[ticker]:
                    del wl[ticker]
                save_watchlist(wl)
                send(chat_id, f"✅ ลบ {ticker} {raw_key} แล้ว")
            else:
                send(chat_id, f"{ticker} ไม่มีเงื่อนไข {raw_key}")
        else:
            del wl[ticker]
            save_watchlist(wl)
            send(chat_id, f"✅ ลบ {ticker} ออกจาก watchlist แล้ว")
    return wl


def cmd_check(chat_id, wl: dict) -> None:
    if not wl:
        send(chat_id, "Watchlist ว่างเปล่า")
        return
    send(chat_id, "⏳ กำลังตรวจ...")
    state = load_state()
    alerts = []
    for ticker, cfg in wl.items():
        alerts += check_ticker(ticker, cfg, state)
    save_state(state)
    if alerts:
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        send(chat_id, f"⏰ Stock Alert  {ts}\n" + "\n".join(alerts))
    else:
        send(chat_id, "✅ ตรวจแล้ว — ไม่มีเงื่อนไขเข้าเกณฑ์")


# ---------- message handler ----------
def handle(msg: dict, wl: dict) -> dict:
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()
    if not text.startswith("/"):
        return wl
    parts = text.split()
    cmd, args = parts[0].lower().split("@")[0], parts[1:]  # ตัด @botname ออก
    if cmd in ("/help", "/start"):
        send(chat_id, HELP_TEXT)
    elif cmd == "/list":
        cmd_list(chat_id, wl)
    elif cmd == "/set":
        wl = cmd_set(chat_id, args, wl)
    elif cmd == "/remove":
        wl = cmd_remove(chat_id, args, wl)
    elif cmd == "/check":
        cmd_check(chat_id, wl)
    else:
        send(chat_id, f"ไม่รู้จักคำสั่ง {cmd}\nพิมพ์ /help ดูคำสั่งทั้งหมด")
    return wl


# ---------- auto-check loop (background thread) ----------
def _auto_check(wl_ref: list) -> None:
    chat_id = _chat_id()
    interval = CHECK_INTERVAL_MIN * 60
    print(f"Auto-check ทุก {CHECK_INTERVAL_MIN} นาที")
    while True:
        time.sleep(interval)
        wl = wl_ref[0]
        if not wl or not chat_id:
            continue
        print(f"[{datetime.now().strftime('%H:%M')}] ตรวจ {len(wl)} ตัว...")
        state = load_state()
        alerts = []
        for ticker, cfg in list(wl.items()):
            alerts += check_ticker(ticker, cfg, state)
        save_state(state)
        if alerts:
            ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
            msg = f"⏰ Stock Alert  {ts}\n" + "\n".join(alerts)
            try:
                _tg("sendMessage", chat_id=chat_id, text=msg)
                print(f"  → ส่ง Telegram แล้ว ({len(alerts)} alert)")
            except Exception as e:
                print(f"  [warn] ส่ง Telegram ไม่สำเร็จ: {e}")
        else:
            print("  → ไม่มีเงื่อนไขเข้าเกณฑ์")


# ---------- main ----------
def main() -> None:
    wl = load_watchlist()
    wl_ref = [wl]  # mutable container เพื่อแชร์กับ thread

    print(f"Bot เริ่มทำงาน | watchlist: {list(wl.keys()) or '(ว่าง)'}")
    print("พิมพ์คำสั่งใน Telegram หรือ Ctrl+C เพื่อหยุด\n")

    threading.Thread(target=_auto_check, args=(wl_ref,), daemon=True).start()

    # Telegram long-poll loop
    offset = None
    token = _token()
    while True:
        try:
            params: dict = {"timeout": 30, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params=params,
                timeout=40,
            )
            for update in r.json().get("result", []):
                offset = update["update_id"] + 1
                if "message" in update:
                    wl_ref[0] = handle(update["message"], wl_ref[0])
        except KeyboardInterrupt:
            print("\nหยุดทำงาน")
            break
        except Exception as e:
            print(f"[poll error] {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
