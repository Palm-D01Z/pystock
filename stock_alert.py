#!/usr/bin/env python3
"""
stock_alert.py — แจ้งเตือนหุ้น Nasdaq/US tech เมื่อราคาแตะเป้า หรือ RSI สุดขั้ว

แหล่งข้อมูล (hybrid):
  - ราคาปัจจุบัน : Finnhub /quote  (real-time, ฟรี ผ่านฟีด IEX) -> ใช้เช็คราคาแตะเป้า
  - RSI          : yfinance แท่งรายวัน + แทนค่าแท่งล่าสุดด้วยราคา live จาก Finnhub
                   (RSI(14) รายวันขยับช้า ดีเลย์ historical แทบไม่มีผล)

ออกแบบให้ "รันครั้งเดียวจบ" (run-once) เพื่อเข้ากับ cron / GitHub Actions
มีโหมด --loop สำหรับรันค้างในเครื่องตัวเอง

ใช้:
    python stock_alert.py            # ตรวจรอบเดียวแล้วจบ
    python stock_alert.py --loop     # ตรวจวนทุก CHECK_INTERVAL_MIN นาที

ตั้งค่า watchlist และเกณฑ์ใน config.py (คัดลอกจาก config.example.py)
ตั้ง secret ผ่าน environment variable:
    FINNHUB_API_KEY            (จำเป็น — สมัครฟรีที่ finnhub.io)
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (ถ้าไม่ตั้ง จะแจ้งเตือนทาง console)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

try:
    import requests
except ImportError:
    print("ต้องติดตั้ง requests ก่อน:  pip install requests")
    sys.exit(1)

try:
    import config
except ImportError:
    config = None

STATE_FILE = Path(__file__).parent / "alert_state.json"
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"

# ---------- ค่าเริ่มต้น (override ได้ใน config.py) ----------
RSI_PERIOD = getattr(config, "RSI_PERIOD", 14) if config else 14
HISTORY_PERIOD = getattr(config, "HISTORY_PERIOD", "3mo") if config else "3mo"
DEFAULT_OVERBOUGHT = getattr(config, "RSI_OVERBOUGHT", 70) if config else 70
DEFAULT_OVERSOLD = getattr(config, "RSI_OVERSOLD", 30) if config else 30
CHECK_INTERVAL_MIN = getattr(config, "CHECK_INTERVAL_MIN", 15) if config else 15


# ---------- การคำนวณ ----------
def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI แบบ Wilder's smoothing. ราคานิ่งสนิทคืนค่า 50 (เป็นกลาง)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    # loss = 0 แต่ gain > 0 -> 100
    rsi = rsi.where(avg_loss != 0, 100.0)
    # gain และ loss เป็น 0 พร้อมกัน (ราคานิ่ง) -> เป็นกลาง 50
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return rsi


# ---------- แหล่งข้อมูล ----------
def fetch_price(ticker: str) -> float:
    """ราคาปัจจุบันแบบ real-time จาก Finnhub."""
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        raise RuntimeError("ยังไม่ได้ตั้ง FINNHUB_API_KEY (สมัครฟรีที่ finnhub.io)")
    r = requests.get(FINNHUB_QUOTE_URL, params={"symbol": ticker, "token": key}, timeout=20)
    r.raise_for_status()
    data = r.json()
    price = data.get("c")  # current price; 0 = ไม่มีข้อมูล/symbol ผิด
    if not price:
        raise ValueError(f"Finnhub ไม่คืนราคาของ {ticker} (อาจผิด symbol หรือเกิน rate limit)")
    return float(price)


def fetch_daily_closes(ticker: str) -> pd.Series:
    """แท่งปิดรายวันจาก yfinance ใช้เป็นฐานคำนวณ RSI."""
    df = yf.Ticker(ticker).history(period=HISTORY_PERIOD, interval="1d")
    return df["Close"].dropna()


def live_rsi(ticker: str, live_price: float) -> float:
    """คำนวณ RSI โดยแทนค่าแท่งปิดล่าสุดด้วยราคา live."""
    closes = fetch_daily_closes(ticker)
    if closes.empty:
        return float("nan")
    closes = closes.copy()
    closes.iloc[-1] = live_price  # อัปเดตแท่งวันนี้ให้เป็นราคาปัจจุบัน
    return float(compute_rsi(closes, RSI_PERIOD).iloc[-1])


# ---------- State (กันเตือนซ้ำ) ----------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def edge(state: dict, key: str, condition: bool) -> bool:
    """คืน True เฉพาะตอนเงื่อนไข 'เพิ่งเปลี่ยนเป็นจริง' (rising edge) — กันสแปม."""
    was_active = state.get(key, False)
    if condition and not was_active:
        state[key] = True
        return True
    if not condition:
        state[key] = False
    return False


# ---------- เช็คทีละตัว ----------
def check_ticker(ticker: str, cfg: dict, state: dict) -> list:
    alerts = []
    try:
        price = fetch_price(ticker)
    except Exception as e:
        print(f"  [warn] ดึงราคา {ticker} ไม่ได้: {e}")
        return alerts

    # --- เงื่อนไข: ราคาแตะเป้า ---
    above = cfg.get("above")
    if above is not None and edge(state, f"{ticker}:above:{above}", price >= above):
        alerts.append(f"📈 {ticker} แตะ/เกินเป้าบน ${above:,.2f} — ราคาปัจจุบัน ${price:,.2f}")

    below = cfg.get("below")
    if below is not None and edge(state, f"{ticker}:below:{below}", price <= below):
        alerts.append(f"📉 {ticker} หลุด/ต่ำกว่าเป้าล่าง ${below:,.2f} — ราคาปัจจุบัน ${price:,.2f}")

    # --- เงื่อนไข: RSI สุดขั้ว ---
    rsi = float("nan")
    try:
        rsi = live_rsi(ticker, price)
    except Exception as e:
        print(f"  [warn] คำนวณ RSI ของ {ticker} ไม่ได้: {e}")

    if not pd.isna(rsi):
        ob = cfg.get("rsi_overbought", DEFAULT_OVERBOUGHT)
        os_ = cfg.get("rsi_oversold", DEFAULT_OVERSOLD)
        if edge(state, f"{ticker}:rsi_ob:{ob}", rsi >= ob):
            alerts.append(f"🔴 {ticker} RSI({RSI_PERIOD}) = {rsi:.1f} → overbought (≥{ob}) | ราคา ${price:,.2f}")
        if edge(state, f"{ticker}:rsi_os:{os_}", rsi <= os_):
            alerts.append(f"🟢 {ticker} RSI({RSI_PERIOD}) = {rsi:.1f} → oversold (≤{os_}) | ราคา ${price:,.2f}")

    rsi_str = f"{rsi:.1f}" if not pd.isna(rsi) else "n/a"
    print(f"  {ticker}: ${price:,.2f} | RSI {rsi_str}")
    return alerts


# ---------- ช่องทางแจ้งเตือน ----------
def send_telegram(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message},
            timeout=20,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"  [warn] ส่ง Telegram ไม่สำเร็จ: {e}")
        return False


def notify(alerts: list) -> None:
    if not alerts:
        return
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    message = f"⏰ Stock Alert  {ts}\n" + "\n".join(alerts)
    print("\n" + message)
    if send_telegram(message):
        print("  → ส่ง Telegram แล้ว")


# ---------- รอบการทำงานหลัก ----------
def run_once() -> None:
    state = load_state()
    all_alerts = []
    watchlist = getattr(config, "WATCHLIST", {}) if config else {}
    print(f"ตรวจ watchlist ({len(watchlist)} ตัว)...")
    for ticker, cfg in watchlist.items():
        all_alerts += check_ticker(ticker, cfg, state)
    save_state(state)
    notify(all_alerts)
    if not all_alerts:
        print("\nไม่มีเงื่อนไขเข้าเกณฑ์รอบนี้")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="รันวนทุก CHECK_INTERVAL_MIN นาที")
    args = parser.parse_args()

    if args.loop:
        print(f"โหมด loop — ตรวจทุก {CHECK_INTERVAL_MIN} นาที (Ctrl+C เพื่อหยุด)")
        while True:
            run_once()
            time.sleep(CHECK_INTERVAL_MIN * 60)
    else:
        run_once()


if __name__ == "__main__":
    main()
