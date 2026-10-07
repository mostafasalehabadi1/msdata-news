"""release.py - drip-publishes queued news: each run moves up to 7 items (7 while more than 20 wait, a random 3-7 while 7-20 wait, all when fewer than 7) from queue/ into news/,
the most important commodities first (data/importance.json), not in the order they traded or were written.

queue/<date>.json holds every written item of a day; news/<date>.json holds only the released ones, so the
site grows a few articles at a time like a human newsroom instead of hundreds at once. A daily report
(queue/report-<date>.json) is released in the first run after it is queued.
"""
import glob
import json
import os
import random
import shutil
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
Q, N = os.path.join(ROOT, "queue"), os.path.join(ROOT, "news")


def backlog():
    total = 0
    for qpath in glob.glob(os.path.join(Q, "????-??-??.json")):
        npath = os.path.join(N, os.path.basename(qpath))
        out = {i["symbol"] for i in json.load(open(npath, encoding="utf-8"))["items"]} if os.path.exists(npath) else set()
        total += sum(1 for i in json.load(open(qpath, encoding="utf-8"))["items"] if i["symbol"] not in out)
    return total


def main():
    # >20 waiting: 7 | 7-20 waiting: random 3-7 | under 7 waiting: all of them
    waiting = backlog()
    budget = 7 if waiting > 20 else random.randint(3, 7) if waiting >= 7 else waiting
    os.makedirs(N, exist_ok=True)
    released = []
    # a daily report goes out in the very first run after it enters the queue (it does not use the budget)
    for rep in sorted(glob.glob(os.path.join(Q, "report-*.json"))):
        dst = os.path.join(N, os.path.basename(rep))
        if not os.path.exists(dst):
            shutil.copy(rep, dst)
            released.append(os.path.basename(rep))
    # most important commodity first (data/importance.json: tier, then trade value this year), whatever day or order it was written in
    imp = json.load(open(os.path.join(ROOT, "data", "importance.json"), encoding="utf-8")) if os.path.exists(os.path.join(ROOT, "data", "importance.json")) else {}
    days, cands = {}, []
    for qpath in sorted(glob.glob(os.path.join(Q, "????-??-??.json"))):
        name = os.path.basename(qpath)
        q = json.load(open(qpath, encoding="utf-8"))
        npath = os.path.join(N, name)
        n = json.load(open(npath, encoding="utf-8")) if os.path.exists(npath) else {**q, "items": []}
        days[name] = (npath, n)
        out = {i["symbol"] for i in n["items"]}
        cands += [(name, i) for i in q["items"] if i["symbol"] not in out]
    cands.sort(key=lambda c: (imp.get(c[1]["symbol"], {}).get("tier", 1), -imp.get(c[1]["symbol"], {}).get("value", 0)))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    touched = set()
    for name, i in cands[:budget]:
        days[name][1]["items"].append({**i, "published_at": now})  # exact release moment, for the site's hot-news ranking
        touched.add(name)
        released.append(f"{name}:{i['symbol']}")
    for name in touched:
        npath, n = days[name]
        json.dump(n, open(npath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"released {len(released)}: {', '.join(released)}")
    release_kish()
    apply_edits()
    apply_edits(os.path.join(ROOT, "edits", "kish"), os.path.join(N, "kish"))


EDITABLE = ("title", "subtitle", "lead", "text", "slug", "table")


def release_kish():
    """Kish export market (owner 1405-07-15): queue/kish/<date>.json -> news/kish/<date>.json, every written item at once
    (a few dozen a day, no drip). Released items are never removed."""
    qk, nk = os.path.join(Q, "kish"), os.path.join(N, "kish")
    os.makedirs(nk, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for rep in sorted(glob.glob(os.path.join(qk, "report-*.json"))):  # daily report: first run after it is queued, like kala's
        dst = os.path.join(nk, os.path.basename(rep))
        if not os.path.exists(dst):
            shutil.copy(rep, dst)
            print(f"kish report released: {os.path.basename(rep)}")
    for qpath in sorted(glob.glob(os.path.join(qk, "????-??-??.json"))):
        q = json.load(open(qpath, encoding="utf-8"))
        npath = os.path.join(nk, os.path.basename(qpath))
        n = json.load(open(npath, encoding="utf-8")) if os.path.exists(npath) else {**q, "items": []}
        out = {i["symbol"] for i in n["items"]}
        new = [{**i, "published_at": now} for i in q["items"] if i["symbol"] not in out]
        if new:
            n["items"] += new
            json.dump(n, open(npath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"kish released {len(new)}: {os.path.basename(qpath)}")


def apply_edits(root=None, ndir=None):
    """edits/<YYYY-MM-DD>/<symbol>.json (content agent's corrections, owner-approved 1405-07-14) override the released item
    in news/<date>.json on every run, so a later release or rewrite can never bring the old text back.
    Only EDITABLE fields are taken; the item gets edited=true and edited_at. Nothing is deleted."""
    root = root or os.path.join(ROOT, "edits")  # edits/kish/<date>/<symbol>.json -> news/kish/<date>.json
    for day in sorted(glob.glob(os.path.join(root, "????-??-??"))):
        npath = os.path.join(ndir or N, os.path.basename(day) + ".json")
        if not os.path.exists(npath):
            continue
        n = json.load(open(npath, encoding="utf-8"))
        changed = 0
        for ep in sorted(glob.glob(os.path.join(day, "*.json"))):
            try:
                e = json.load(open(ep, encoding="utf-8"))
            except ValueError as err:
                print(f"edit skipped (bad JSON) {ep}: {err}")
                continue
            sym = os.path.basename(ep)[:-5]
            for it in n["items"]:
                if it.get("symbol") != sym:
                    continue
                new = {k: e[k] for k in EDITABLE if k in e and isinstance(e[k], (str, dict, list)) and e[k] != it.get(k)}
                if new:
                    it.update(new)
                    it["edited"] = True
                    it["edited_at"] = e.get("edited_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
                    changed += 1
        if changed:
            json.dump(n, open(npath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"edits applied: {os.path.basename(npath)} x{changed}")


if __name__ == "__main__":
    main()
