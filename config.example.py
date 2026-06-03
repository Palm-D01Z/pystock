# config.example.py
# คัดลอกเป็น config.py แล้วแก้ตามต้องการ:   cp config.example.py config.py
#
# แต่ละ ticker ตั้ง key ได้ (ใส่เฉพาะที่ต้องการ ละได้):
#   "above"          : ราคาที่ >= แล้วเตือน (เป้าบน / take-profit)
#   "below"          : ราคาที่ <= แล้วเตือน (เป้าล่าง / ซื้อเพิ่ม / stop)
#   "rsi_overbought" : เกณฑ์ RSI overbought (default 70)
#   "rsi_oversold"   : เกณฑ์ RSI oversold  (default 30)

WATCHLIST = {
    "NVDA": {"above": 1400, "below": 1000},
    "MSFT": {"below": 400, "rsi_oversold": 35},
    "AAPL": {"above": 260, "below": 210},
    "GOOGL": {"below": 170},
    "AMD":  {"rsi_overbought": 75, "rsi_oversold": 30},
    "AVGO": {"above": 1900},
}

# --- ค่าทั่วไป ---
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70        # เกณฑ์เริ่มต้น (override รายตัวได้ใน WATCHLIST)
RSI_OVERSOLD = 30
HISTORY_PERIOD = "3mo"     # ช่วงข้อมูลที่ดึงมาคำนวณ RSI ("3mo" พอสำหรับ RSI 14)
CHECK_INTERVAL_MIN = 15    # ใช้เฉพาะโหมด --loop

# หมายเหตุ: เวอร์ชันนี้ใช้ Finnhub สำหรับราคา real-time (ต้องตั้ง env FINNHUB_API_KEY)
#           และ yfinance สำหรับแท่งราคารายวันที่ใช้คำนวณ RSI
