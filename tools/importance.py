"""importance.py - the table of every commodity symbol ever traded, ranked by total trade value.

data/importance.json = {symbol: {"value": total trade value over its recorded history, "first": first date seen,
"days": trading days counted, "tier": 0|1|2}}. Updated with every writer run from the latest day:
top 50% of symbols by value = 0 (important), next 35% = 1 (medium), bottom 15% = 2 (low).
A symbol seen for the first time is medium (1) that day, then ranked like the rest from the next update.
"""
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PATH = os.path.join(ROOT, "data", "importance.json")


def update(date, rows):
    first_run = not os.path.exists(PATH)  # bootstrap: rank today's symbols normally, none is "new"
    table = {} if first_run else json.load(open(PATH, encoding="utf-8"))
    new = set()
    for r in rows:
        sym = r["symbol"]
        hp = os.path.join(ROOT, "data", date, "symbols", f"{sym}.json")
        h = json.load(open(hp, encoding="utf-8")).get("history", []) if os.path.exists(hp) else []
        vals = [x.get("total_value") or 0 for x in h if x.get("total_value")] or [r.get("trade_value") or 0]
        if sym not in table and not first_run:
            new.add(sym)
        e = table.setdefault(sym, {"first": date})
        e["value"], e["days"] = sum(vals), len(vals)
    ranked = sorted(table, key=lambda s: -table[s]["value"])
    n = max(1, len(ranked))
    for i, s in enumerate(ranked):
        table[s]["tier"] = 1 if s in new else 0 if i < n * 0.50 else 1 if i < n * 0.85 else 2
    json.dump(table, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    return {s: e["tier"] for s, e in table.items()}
