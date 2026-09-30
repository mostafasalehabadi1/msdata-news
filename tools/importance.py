"""importance.py - the table of every commodity symbol ever traded, ranked by trade value since the start of the Jalali year.

data/importance.json = {symbol: {"value": trade value since 1 Farvardin, "first": first date seen, "tier": 0|1}}.
Updated with every writer run from the latest day: top 50% of symbols by value = 0 (important), the rest = 1.
A symbol seen for the first time is 1 that day, then ranked like the rest from the next update.
"""
import json
import os
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PATH = os.path.join(ROOT, "data", "importance.json")


def load():
    return json.load(open(PATH, encoding="utf-8")) if os.path.exists(PATH) else {}


def update(date, rows):
    first_run = not os.path.exists(PATH)  # bootstrap: rank today's symbols normally, none is "new"
    table = load()
    new = set()
    for r in rows:
        sym = r["symbol"]
        hp = os.path.join(ROOT, "data", date, "symbols", f"{sym}.json")
        s = json.load(open(hp, encoding="utf-8")) if os.path.exists(hp) else {}
        value = s.get("ytd_value")
        if value is None:  # snapshot from before ytd_value existed: fall back to the 40 recorded trades
            value = sum(x.get("total_value") or 0 for x in s.get("history", [])) or r.get("trade_value") or 0
        if sym not in table and not first_run:
            new.add(sym)
        e = table.setdefault(sym, {"first": date})
        e.pop("days", None)
        e["value"] = value
        if e.get("seen_date") != date:  # when the writer first saw this trade: important news may wait at most 2h for a strong model
            e["seen_date"], e["seen_t"] = date, int(time.time())
    ranked = sorted(table, key=lambda s: -table[s]["value"])
    n = max(1, len(ranked))
    for i, s in enumerate(ranked):
        table[s]["tier"] = 1 if s in new else 0 if i < n * 0.50 else 1
    json.dump(table, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    return table
