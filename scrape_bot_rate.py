"""
Scrape the USD/THB and KRW/THB exchange rates published by the Bank of
Thailand (https://www.bot.or.th/th/statistics/exchange-rate.html) and append
the latest rate of each currency to its own text file, one line per day.

The page itself renders its table with JavaScript, but the table is backed
by a plain JSON endpoint that we can call directly:

  https://www.bot.or.th/content/bot/th/statistics/exchange-rate/jcr:content/
  root/container/statisticstable1.results.level3cache.daily.
  {start:BE-date}.{end:BE-date}.{CURRENCY}.json

Run it daily (e.g. with Windows Task Scheduler) to build up a history file.
"""

import re
from datetime import date, timedelta
from pathlib import Path

import requests

# ไฟล์ปลายทางที่จะเก็บประวัติอัตราแลกเปลี่ยนของแต่ละสกุลเงิน (อยู่โฟลเดอร์เดียวกับสคริปต์นี้)
OUTPUT_FILES = {
    "USD": Path(__file__).parent / "usd_thb_rates.txt",
    "KRW": Path(__file__).parent / "krw_thb_rates.txt",
}
# จำนวนวันย้อนหลังที่จะขอข้อมูล เผื่อกรณีวันหยุด/เสาร์-อาทิตย์ที่ ธปท. ไม่ประกาศอัตรา
LOOKBACK_DAYS = 10
# ปี พ.ศ. = ปี ค.ศ. + 543 (URL ของ ธปท. ใช้ปี พ.ศ.)
BE_OFFSET = 543

# ตารางแปลงชื่อเดือนไทยแบบย่อ -> เลขเดือน (ใช้กับฟิลด์ "period" เช่น "04 ก.ย. 2569")
THAI_MONTHS = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4,
    "พ.ค.": 5, "มิ.ย.": 6, "ก.ค.": 7, "ส.ค.": 8,
    "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}

# ตารางแปลงชื่อเดือนไทยแบบเต็ม -> เลขเดือน (ใช้กับฟิลด์ "date" เช่น "ประจำวันที่ 04 กันยายน 2569")
THAI_MONTHS_FULL = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
    "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8,
    "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
}

# endpoint ของ "อัตราแลกเปลี่ยนถัวเฉลี่ยถ่วงน้ำหนักระหว่างธนาคาร" (weighted-average rate)
# endpoint นี้ไม่ต้องใส่ช่วงวันที่ในตัว URL เพราะเซิร์ฟเวอร์จะคืนค่าล่าสุดให้เสมอ
WEIGHTED_AVERAGE_URL = (
    "https://www.bot.or.th/content/bot/th/statistics/exchange-rate/"
    "jcr:content/root/container/statisticstable2.results.level3cache.json"
)

# ใส่ User-Agent ปลอมเป็นเบราว์เซอร์ทั่วไป กัน ธปท. บล็อกคำขอที่ไม่มี header นี้
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def be_date_str(d: date) -> str:
    # แปลงวันที่ (ปี ค.ศ.) เป็นสตริงรูปแบบ YYYY-MM-DD แต่ใช้ปี พ.ศ. ตามที่ URL ของ ธปท. ต้องการ
    return f"{d.year + BE_OFFSET:04d}-{d.month:02d}-{d.day:02d}"


def thai_period_to_iso(period: str) -> str:
    # e.g. "04 ก.ย. 2569" -> "2026-09-04"
    # 1) แยกสตริงด้วยช่องว่างเป็น วัน / เดือน(ย่อ) / ปี(พ.ศ.)
    day_str, month_str, year_str = period.split()
    # 2) แปลงวันเป็นตัวเลข
    day = int(day_str)
    # 3) แปลงชื่อเดือนไทยย่อเป็นเลขเดือนจากตาราง THAI_MONTHS
    month = THAI_MONTHS[month_str]
    # 4) แปลงปี พ.ศ. กลับเป็นปี ค.ศ.
    year = int(year_str) - BE_OFFSET
    # 5) ประกอบกลับเป็นรูปแบบวันที่สากล YYYY-MM-DD
    return f"{year:04d}-{month:02d}-{day:02d}"


def thai_full_date_to_iso(text: str) -> str:
    # e.g. "ประจำวันที่ 04 กันยายน 2569" -> "2026-09-04"
    # 1) ใช้ regex ดึงเฉพาะส่วน "วัน เดือน(เต็ม) ปี" ออกจากประโยคภาษาไทย
    match = re.search(r"(\d{1,2})\s+(\S+)\s+(\d{4})", text)
    if not match:
        raise ValueError(f"Could not parse Thai date from: {text!r}")
    day, month_name, year = match.groups()
    # 2) แปลงชื่อเดือนไทยแบบเต็มเป็นเลขเดือนจากตาราง THAI_MONTHS_FULL
    month = THAI_MONTHS_FULL[month_name]
    # 3) แปลงปี พ.ศ. เป็น ค.ศ. แล้วประกอบเป็นรูปแบบ YYYY-MM-DD
    return f"{int(year) - BE_OFFSET:04d}-{month:02d}-{int(day):02d}"


def fetch_latest_rate(currency: str) -> dict:
    # ขั้นตอนการดึงอัตราซื้อ/ขายเงินสกุลที่ระบุ (buying/selling) ล่าสุดจาก ธปท.
    # หมายเหตุ: สำหรับสกุลเงินมูลค่าน้อยอย่างเงินวอนเกาหลี (KRW) ธปท. จะประกาศอัตราต่อ 100 หน่วย

    # 1) คำนวณช่วงวันที่ที่จะขอข้อมูล: ตั้งแต่ (วันนี้ - LOOKBACK_DAYS) ถึงวันนี้
    today = date.today()
    start = today - timedelta(days=LOOKBACK_DAYS)

    # 2) ประกอบ URL ของ API โดยแปลงวันที่เริ่มต้น/สิ้นสุดเป็นรูปแบบปี พ.ศ. ตามที่ ธปท. ต้องการ
    url = (
        "https://www.bot.or.th/content/bot/th/statistics/exchange-rate/"
        "jcr:content/root/container/statisticstable1.results.level3cache."
        f"daily.{be_date_str(start)}.{be_date_str(today)}.{currency}.json"
    )

    # 3) ยิง GET request ไปยัง API และเช็คว่าไม่มี error (เช่น 4xx/5xx)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    # 4) แปลง response body ที่เป็น JSON string ให้เป็น dict ของ Python
    data = resp.json()

    # 5) ดึงรายการอัตราแลกเปลี่ยนแต่ละวันออกมาจาก key "responseContent"
    entries = data.get("responseContent", [])
    if not entries:
        # ถ้าไม่มีข้อมูลเลยในช่วงวันที่ที่ขอ ให้โยน error ออกไปทันที
        raise RuntimeError(f"No {currency} exchange rate data returned for range {start} to {today}")

    # 6) เรียงรายการตามวันที่จากมากไปน้อย (ป้องกันกรณี API ไม่ได้เรียงลำดับมาให้)
    entries.sort(key=lambda e: thai_period_to_iso(e["period"]), reverse=True)
    # 7) คืนค่ารายการแรก ซึ่งคือวันที่ล่าสุดที่มีข้อมูล
    return entries[0]


def fetch_weighted_average_rate() -> tuple[str, str]:
    """Return (iso_date, rate) for the weighted-average interbank USD/THB rate."""
    # ขั้นตอนการดึง "อัตราแลกเปลี่ยนถัวเฉลี่ยถ่วงน้ำหนักระหว่างธนาคาร" (weighted-average rate)

    # 1) ยิง GET request ไปยัง endpoint คงที่ (ไม่ต้องระบุช่วงวันที่ เพราะคืนค่าล่าสุดเสมอ)
    resp = requests.get(WEIGHTED_AVERAGE_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    # 2) ตัวเลขอัตราถ่วงน้ำหนักไม่ได้มาเป็นฟิลด์แยก แต่ฝังอยู่ใน HTML ข้อความ
    #    ในฟิลด์ "description" เช่น "...ถ่วงน้ำหนักระหว่างธนาคาร <span>32.932</span> บาท..."
    #    จึงต้องใช้ regex ดึงตัวเลขทศนิยมตัวแรกที่เจอออกมา
    rate_match = re.search(r"(\d+\.\d+)", data["description"])
    if not rate_match:
        raise RuntimeError(f"Could not find weighted-average rate in: {data['description']!r}")

    # 3) แปลงฟิลด์ "date" (ข้อความวันที่แบบเต็มภาษาไทย) ให้เป็นรูปแบบ YYYY-MM-DD
    iso_date = thai_full_date_to_iso(data["date"])
    # 4) คืนค่าเป็นคู่ (วันที่, อัตราถ่วงน้ำหนัก) ให้ผู้เรียกใช้งานต่อ
    return iso_date, rate_match.group(1)


def append_rate_to_file(currency: str, entry: dict, weighted_average: str | None) -> None:
    # ขั้นตอนการต่อท้าย (append) ข้อมูลอัตราแลกเปลี่ยนของวันนั้นลงไฟล์ txt ของสกุลเงินนั้น ๆ

    output_file = OUTPUT_FILES[currency]

    # 1) แปลงวันที่ของ entry ให้เป็นรูปแบบ YYYY-MM-DD และดึงค่าอัตราซื้อโอน/ขายออกมา
    iso_date = thai_period_to_iso(entry["period"])
    buying_transfer = entry["buying_transfer"]
    selling = entry["selling"]

    # 2) ประกอบเป็นบรรทัดข้อความ 1 บรรทัด รวมทั้งอัตราถ่วงน้ำหนัก (ถ้ามี)
    line = (
        f"{iso_date},{currency},buying_transfer={buying_transfer},selling={selling},"
        f"weighted_average={weighted_average}\n"
    )

    # 3) อ่านไฟล์เดิม (ถ้ามี) เพื่อตรวจสอบว่าวันที่นี้เคยถูกบันทึกไว้แล้วหรือยัง
    existing = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
    if f"{iso_date},{currency}," in existing:
        # ถ้ามีอยู่แล้ว ให้ข้ามการเขียนซ้ำ (กันข้อมูลซ้ำเวลารันสคริปต์หลายรอบต่อวัน)
        print(f"Rate for {iso_date} ({currency}) already recorded, skipping.")
        return

    # 4) เปิดไฟล์ในโหมด append ("a") แล้วเขียนบรรทัดใหม่ต่อท้ายไฟล์
    with output_file.open("a", encoding="utf-8") as f:
        f.write(line)

    print(f"Appended: {line.strip()}")


def main() -> None:
    # ขั้นตอนหลักของสคริปต์: ดึงอัตราแลกเปลี่ยนของแต่ละสกุลเงินที่ตั้งค่าไว้ใน OUTPUT_FILES แล้วบันทึกลงไฟล์

    for currency in OUTPUT_FILES:
        try:
            # 1) ดึงอัตราซื้อ/ขายเงินสกุลนั้นล่าสุด
            entry = fetch_latest_rate(currency)
        except Exception as exc:
            # ถ้าดึงอัตราของสกุลเงินนี้ไม่สำเร็จ ให้แจ้งเตือนแล้วข้ามไปทำสกุลเงินถัดไป
            print(f"Could not fetch {currency} rate: {exc}")
            continue

        # 2) อัตราถ่วงน้ำหนักระหว่างธนาคารมีประกาศเฉพาะสกุลเงินดอลลาร์สหรัฐเท่านั้น
        weighted_average = None
        if currency == "USD":
            # พยายามดึงอัตราถ่วงน้ำหนักล่าสุดเพิ่มเติม (ถ้าดึงไม่ได้ก็ไม่ทำให้สคริปต์ล้มเหลวทั้งหมด)
            try:
                wa_date, wa_rate = fetch_weighted_average_rate()
                # ใช้อัตราถ่วงน้ำหนักเฉพาะกรณีที่วันที่ตรงกับอัตราซื้อ/ขายที่ดึงมาข้างต้นเท่านั้น
                if wa_date == thai_period_to_iso(entry["period"]):
                    weighted_average = wa_rate
                else:
                    print(f"Weighted-average rate is for {wa_date}, not {thai_period_to_iso(entry['period'])}; skipping it.")
            except Exception as exc:
                # ถ้าดึงอัตราถ่วงน้ำหนักไม่สำเร็จ (เช่น เว็บเปลี่ยนโครงสร้าง) ให้แจ้งเตือนแล้วทำงานต่อโดยไม่มีค่านี้
                print(f"Could not fetch weighted-average rate: {exc}")

        # 3) บันทึกผลลัพธ์ของสกุลเงินนี้ลงไฟล์ txt ของตัวเอง
        append_rate_to_file(currency, entry, weighted_average)


if __name__ == "__main__":
    # จุดเริ่มต้นเมื่อรันไฟล์นี้โดยตรง (เช่น `python scrape_bot_rate.py`)
    main()
