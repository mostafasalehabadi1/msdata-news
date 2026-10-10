"""fetch.py - snapshot of the latest kala (IME physical market) trading day for the news writer.

Runs inside GitHub Actions (foreign IP -> Iranian API; no Iranian machine ever talks to GitHub).
Writes data/<YYYY-MM-DD>/today.json and data/<YYYY-MM-DD>/symbols/<SYMBOL>.json, and data/latest.json.
Only the latest trading day is kept in the snapshot (older days are not written - user rule: no news for past days).
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

SOURCES = [
    "https://riz-api2.msdata.ir/kala-fizik/raw-data",   # HiWeb (primary)
    "https://riz-api.msdata.ir/kala-fizik/raw-data",    # Asiatech (backup)
]
HISTORY_ROWS = 40
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def get_json(url, tries=3):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - network errors of any kind are retried
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def fetch(qs=""):
    errors = []
    for base in SOURCES:
        try:
            d = get_json(base + qs)
            if d.get("status") == "ok":
                return d, base
            errors.append(f"{base}: status={d.get('status')}")
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    raise RuntimeError(" | ".join(errors))


def jalali_to_gregorian(jy, jm, jd):
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd
    days += (jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    for gm, ml in enumerate([31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], 1):
        if gd <= ml:
            return f"{gy:04d}-{gm:02d}-{gd:02d}"
        gd -= ml
    raise ValueError("bad date")


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def main():
    today, src = fetch()
    date_fa = today["trade_date"]
    jy, jm, jd = (int(x) for x in date_fa.split("/"))
    date = jalali_to_gregorian(jy, jm, jd)
    rows = [r for r in today.get("today", []) if (r.get("traded_qty") or 0) > 0]
    day_dir = os.path.join(ROOT, date)
    write(os.path.join(day_dir, "today.json"),
          {"date_fa": date_fa, "date": date, "source": src, "rows": rows})
    for r in rows:
        sym = r["symbol"]
        h, _ = fetch("?symbol=" + urllib.parse.quote(sym))
        full = h.get("history", [])
        ytd = sum(x.get("total_value") or 0 for x in full if (x.get("trade_date") or "") >= f"{jy}/01/01")
        write(os.path.join(day_dir, "symbols", sym + ".json"), {"symbol": sym, "ytd_value": ytd, "history": full[-HISTORY_ROWS:]})
    write(os.path.join(ROOT, "latest.json"), {"date_fa": date_fa, "date": date, "symbols": len(rows)})
    # keep only the latest day in the snapshot
    for d in os.listdir(ROOT):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p) and d != date and d not in ("kish", "energy"):  # other markets keep their own snapshot and stable-<date>.json (fetch_kish.py, fetch_energy.py); deleting data/energy every hour reset the 20-min settle clock, so same-day energy news were never written
            for dirpath, _, files in os.walk(p, topdown=False):
                for fn in files:
                    os.remove(os.path.join(dirpath, fn))
                os.rmdir(dirpath)
    print(f"ok {date_fa} ({date}) symbols={len(rows)} source={src}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("FETCH FAILED:", e, file=sys.stderr)
        sys.exit(1)
