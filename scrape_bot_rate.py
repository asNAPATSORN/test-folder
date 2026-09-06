"""
Scrape the USD/THB exchange rate published by the Bank of Thailand
(https://www.bot.or.th/th/statistics/exchange-rate.html) and append the
latest rate to a text file, one line per day.

The page itself renders its table with JavaScript, but the table is backed
by a plain JSON endpoint that we can call directly:

  https://www.bot.or.th/content/bot/th/statistics/exchange-rate/jcr:content/
  root/container/statisticstable1.results.level3cache.daily.
  {start:BE-date}.{end:BE-date}.USD.json

Run it daily (e.g. with Windows Task Scheduler) to build up a history file.
"""

import re
from datetime import date, timedelta
from pathlib import Path

import requests

OUTPUT_FILE = Path(__file__).parent / "usd_thb_rates.txt"
LOOKBACK_DAYS = 10  # buffer to cover weekends/holidays when BOT publishes no rate
BE_OFFSET = 543  # Buddhist Era = Gregorian year + 543

THAI_MONTHS = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4,
    "พ.ค.": 5, "มิ.ย.": 6, "ก.ค.": 7, "ส.ค.": 8,
    "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}

THAI_MONTHS_FULL = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
    "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8,
    "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
}

WEIGHTED_AVERAGE_URL = (
    "https://www.bot.or.th/content/bot/th/statistics/exchange-rate/"
    "jcr:content/root/container/statisticstable2.results.level3cache.json"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def be_date_str(d: date) -> str:
    return f"{d.year + BE_OFFSET:04d}-{d.month:02d}-{d.day:02d}"


def thai_period_to_iso(period: str) -> str:
    # e.g. "04 ก.ย. 2569" -> "2026-09-04"
    day_str, month_str, year_str = period.split()
    day = int(day_str)
    month = THAI_MONTHS[month_str]
    year = int(year_str) - BE_OFFSET
    return f"{year:04d}-{month:02d}-{day:02d}"


def thai_full_date_to_iso(text: str) -> str:
    # e.g. "ประจำวันที่ 04 กันยายน 2569" -> "2026-09-04"
    match = re.search(r"(\d{1,2})\s+(\S+)\s+(\d{4})", text)
    if not match:
        raise ValueError(f"Could not parse Thai date from: {text!r}")
    day, month_name, year = match.groups()
    month = THAI_MONTHS_FULL[month_name]
    return f"{int(year) - BE_OFFSET:04d}-{month:02d}-{int(day):02d}"


def fetch_latest_usd_rate() -> dict:
    today = date.today()
    start = today - timedelta(days=LOOKBACK_DAYS)

    url = (
        "https://www.bot.or.th/content/bot/th/statistics/exchange-rate/"
        "jcr:content/root/container/statisticstable1.results.level3cache."
        f"daily.{be_date_str(start)}.{be_date_str(today)}.USD.json"
    )

    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    entries = data.get("responseContent", [])
    if not entries:
        raise RuntimeError(f"No exchange rate data returned for range {start} to {today}")

    # Entries come back most-recent-first; sort defensively just in case.
    entries.sort(key=lambda e: thai_period_to_iso(e["period"]), reverse=True)
    return entries[0]


def fetch_weighted_average_rate() -> tuple[str, str]:
    """Return (iso_date, rate) for the weighted-average interbank USD/THB rate."""
    resp = requests.get(WEIGHTED_AVERAGE_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    rate_match = re.search(r"(\d+\.\d+)", data["description"])
    if not rate_match:
        raise RuntimeError(f"Could not find weighted-average rate in: {data['description']!r}")

    iso_date = thai_full_date_to_iso(data["date"])
    return iso_date, rate_match.group(1)


def append_rate_to_file(entry: dict, weighted_average: str | None) -> None:
    iso_date = thai_period_to_iso(entry["period"])
    buying_transfer = entry["buying_transfer"]
    selling = entry["selling"]

    line = (
        f"{iso_date},USD,buying_transfer={buying_transfer},selling={selling},"
        f"weighted_average={weighted_average}\n"
    )

    existing = OUTPUT_FILE.read_text(encoding="utf-8") if OUTPUT_FILE.exists() else ""
    if f"{iso_date},USD," in existing:
        print(f"Rate for {iso_date} already recorded, skipping.")
        return

    with OUTPUT_FILE.open("a", encoding="utf-8") as f:
        f.write(line)

    print(f"Appended: {line.strip()}")


def main() -> None:
    entry = fetch_latest_usd_rate()

    weighted_average = None
    try:
        wa_date, wa_rate = fetch_weighted_average_rate()
        if wa_date == thai_period_to_iso(entry["period"]):
            weighted_average = wa_rate
        else:
            print(f"Weighted-average rate is for {wa_date}, not {thai_period_to_iso(entry['period'])}; skipping it.")
    except Exception as exc:
        print(f"Could not fetch weighted-average rate: {exc}")

    append_rate_to_file(entry, weighted_average)


if __name__ == "__main__":
    main()
