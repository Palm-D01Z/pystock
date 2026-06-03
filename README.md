# Stock Alert (Nasdaq / US Tech) — Real-time via Finnhub

แจ้งเตือนเมื่อหุ้น US tech **ราคาแตะเป้า** หรือ **RSI สุดขั้ว** (overbought/oversold)

**แหล่งข้อมูล (hybrid):**
- ราคาปัจจุบัน → **Finnhub** `/quote` (real-time ฟรี ผ่านฟีด IEX, latency ต่ำ) — ใช้เช็คราคาแตะเป้า
- RSI → **yfinance** แท่งรายวัน แล้วแทนค่าแท่งล่าสุดด้วยราคา live (RSI(14) รายวันขยับช้า ดีเลย์ historical แทบไม่มีผล)

> ทำไมต้อง hybrid: Finnhub `/quote` ฟรีและ real-time แต่ endpoint candle ย้อนหลังเป็น premium
> จึงดึงราคา live จาก Finnhub + ฐาน RSI จาก yfinance

## 1. ติดตั้ง

```bash
pip install -r requirements.txt
cp config.example.py config.py      # แล้วแก้ watchlist
```

## 2. สมัคร Finnhub (ฟรี)

1. สมัครที่ https://finnhub.io → ได้ **API key**
2. ตั้ง environment variable:
```bash
export FINNHUB_API_KEY="d8fc4e9r01qub7kgv980d8fc4e9r01qub7kgv98g"
```
> free tier: real-time หุ้น US ผ่าน REST, rate limit ~60 calls/นาที (พอเหลือสำหรับ watchlist ส่วนตัว)
> ฟีดเป็น IEX (ตลาดเดียว ไม่ใช่ราคารวม SIP) — เกาะราคาจริงใกล้มาก เหมาะกับ alert ที่ไม่ใช่ day-trade

## 3. ตั้ง Telegram (แนะนำ — ฟรีและง่ายสุด)

1. ทักหา **@BotFather** → `/newbot` → ได้ **bot token**
2. ทักบอทที่สร้าง 1 ครั้ง
3. หา **chat id**: เปิด `https://api.telegram.org/bot<TOKEN>/getUpdates` ดู `chat.id`
4. ตั้ง env:
```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="987654321"
```
> ถ้าไม่ตั้ง จะแจ้งเตือนทาง console อย่างเดียว

## 4. รัน

```bash
python stock_alert.py          # ตรวจรอบเดียวแล้วจบ (เหมาะกับ cron / GitHub Actions)
python stock_alert.py --loop   # ตรวจวนทุก 15 นาที (ค้างไว้ในเครื่อง)
```

ไฟล์ `alert_state.json` สร้างอัตโนมัติ เก็บสถานะกันเตือนซ้ำ —
เตือนเฉพาะตอนเงื่อนไข **เพิ่งเปลี่ยนเป็นจริง** และรีเซ็ตเมื่อกลับเป็นเท็จ

## 5. ตั้งให้รันอัตโนมัติ

### cron (Mac/Linux) — ทุก 5 นาที ในเวลาตลาดเปิด (จ.–ศ.)
```cron
*/5 13-21 * * 1-5  cd /path/to/project && /usr/bin/python3 stock_alert.py >> alert.log 2>&1
```
(ปรับช่วงชั่วโมงตาม timezone เครื่อง — ตลาด US 09:30–16:00 ET)

### GitHub Actions (ฟรี ไม่ต้องเปิดเครื่องทิ้ง)
`.github/workflows/alert.yml`:
```yaml
name: stock-alert
on:
  schedule:
    - cron: "*/15 13-21 * * 1-5"   # UTC
  workflow_dispatch:
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      - run: python stock_alert.py
        env:
          FINNHUB_API_KEY: ${{ secrets.FINNHUB_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```
ใส่ secret ใน repo → Settings → Secrets → Actions
> state ไม่ถูกเก็บข้ามรอบบน Actions — ถ้าอยากกันเตือนซ้ำข้ามรอบ ใช้ actions/cache หรือ commit state กลับ

## 6. เปลี่ยนไปใช้ Alpaca แทน Finnhub (ทางเลือก)

แก้แค่ `fetch_price()`:
```python
def fetch_price(ticker):
    h = {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"],
         "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]}
    url = f"https://data.alpaca.markets/v2/stocks/{ticker}/trades/latest"
    r = requests.get(url, headers=h, timeout=20); r.raise_for_status()
    return float(r.json()["trade"]["p"])
```

## 7. ต่อยอด

- เพิ่มเงื่อนไขจาก CLAUDE.md: ตัด/หลุด 200-day MA, MACD, % เปลี่ยนรายวัน → เพิ่มใน `check_ticker()`
- เปลี่ยนช่องทาง: Discord webhook / email → แก้ `notify()`
- หุ้น SET50: yfinance ใช้ suffix `.BK` แต่ Finnhub ฟรีไม่ครอบคลุมหุ้นไทย — ต้องใช้แหล่งอื่น

## ข้อจำกัด

- Finnhub ฟรีใช้ฟีด IEX (ราคาตลาดเดียว ไม่ใช่ราคารวม SIP) — ใกล้ราคาจริงมาก แต่ไม่ใช่ทางการ 100%
- เหมาะกับ swing/position ไม่เหมาะ day-trade ที่ต้องการ tick ต่อวินาที
- ไม่ใช่คำแนะนำการลงทุน
