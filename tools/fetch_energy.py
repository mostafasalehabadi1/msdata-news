"""fetch_energy.py - snapshot of the Iran Energy Exchange (physical) for the news writer (write_free.py --market energy).
data/energy/today.json   = /api/energy/raw-data.json (+ "date": Gregorian of trade_date)
data/energy/history.json = the "days" part of /api/energy-history/raw-data.json (full rows of the last ~60 days)
The contract symbol changes with every offer, so history is matched by commodity key (goods|producer|market), see key().
"""
import json
import os
import sys
import urllib.request

from fetch import jalali_to_gregorian

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "energy")
SRC = {"today": ("https://msdata.ir/api/energy/raw-data.json", "https://riz-api.msdata.ir/energy/raw-data"),
       "history": ("https://msdata.ir/api/energy-history/raw-data.json", "https://riz-api.msdata.ir/energy/history-bundle")}


def key(r):
    """stable commodity key: the source gives no fixed code (designer 1405-07-16), so name + producer + market."""
    # kala_energy f12f05e: commodity_key = "goods_name|producer|target_market"; older rows without it get the same string
    return r.get("commodity_key") or "|".join((r.get("goods_name") or "", r.get("producer") or "", r.get("target_market") or ""))


def get(urls):
    err = None
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "msdata-news/1.0"})
            j = json.load(urllib.request.urlopen(req, timeout=120))
            if j.get("status") == "ok":
                return j
            err = f"{u}: status {j.get('status')}"
        except Exception as e:  # noqa: BLE001
            err = f"{u}: {e}"
    raise RuntimeError(err)


def traded(t):
    return [r for r in t["items"] if (r.get("trade_volume") or 0) > 0]


def main():
    t = get(SRC["today"])
    jy, jm, jd = (int(x) for x in t["trade_date"].split("/"))
    t["date"] = jalali_to_gregorian(jy, jm, jd)
    if "--left" in sys.argv:  # release loop: how many traded rows of the latest day have no news yet (writes nothing)
        q = os.path.join(os.path.dirname(os.path.dirname(ROOT)), "queue", "energy", t["date"] + ".json")
        done = len(json.load(open(q, encoding="utf-8"))["items"]) if os.path.exists(q) else 0
        print(len(traded(t)) - done)
        return
    os.makedirs(ROOT, exist_ok=True)
    h = get(SRC["history"])
    hist = {}
    for d in sorted(h.get("days", {})):
        for r in h["days"][d].get("items", []):
            if (r.get("trade_volume") or 0) > 0:
                hist.setdefault(key(r), []).append(r)
    for name, j in (("today", t), ("history", {"series": hist})):
        tmp = os.path.join(ROOT, name + ".json.tmp")
        json.dump(j, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, os.path.join(ROOT, name + ".json"))
    print(f"ok energy {t['trade_date']} ({t['date']}) traded={len(traded(t))} history_keys={len(hist)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("ENERGY FETCH FAILED:", e, file=sys.stderr)
        sys.exit(1)
