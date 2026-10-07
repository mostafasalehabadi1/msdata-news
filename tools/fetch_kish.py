"""fetch_kish.py - snapshot of the Kish export market for the news writer (write_free.py --market kish).
data/kish/today.json   = /api/kish/raw-data.json (+ "date": Gregorian of trade_date)
data/kish/history.json = /api/kish-history/raw-data.json (series per symbol from 1404/07/01, official IME export stats)
"""
import json
import os
import sys
import urllib.request

from fetch import jalali_to_gregorian

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "kish")
SRC = {"today": ("https://msdata.ir/api/kish/raw-data.json", "https://riz-api.msdata.ir/kish/raw-data"),
       "history": ("https://msdata.ir/api/kish-history/raw-data.json", "https://riz-api.msdata.ir/kish/history-bundle")}


def get(urls):
    err = None
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "msdata-news/1.0"})
            j = json.load(urllib.request.urlopen(req, timeout=90))
            if j.get("status") == "ok":
                return j
            err = f"{u}: status {j.get('status')}"
        except Exception as e:  # noqa: BLE001
            err = f"{u}: {e}"
    raise RuntimeError(err)


def main():
    t = get(SRC["today"])
    jy, jm, jd = (int(x) for x in t["trade_date"].split("/"))
    t["date"] = jalali_to_gregorian(jy, jm, jd)
    if "--left" in sys.argv:  # release loop: how many traded rows of the latest day have no news yet (writes nothing)
        q = os.path.join(os.path.dirname(os.path.dirname(ROOT)), "queue", "kish", t["date"] + ".json")
        done = len(json.load(open(q, encoding="utf-8"))["items"]) if os.path.exists(q) else 0
        print(sum(1 for r in t["items"] if (r.get("trade_volume") or 0) > 0) - done)
        return
    os.makedirs(ROOT, exist_ok=True)
    h = get(SRC["history"])
    for name, j in (("today", t), ("history", h)):
        tmp = os.path.join(ROOT, name + ".json.tmp")
        json.dump(j, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, os.path.join(ROOT, name + ".json"))
    traded = sum(1 for r in t["items"] if (r.get("trade_volume") or 0) > 0)
    print(f"ok kish {t['trade_date']} ({t['date']}) traded={traded} history_symbols={len(h.get('series', {}))}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("KISH FETCH FAILED:", e, file=sys.stderr)
        sys.exit(1)
