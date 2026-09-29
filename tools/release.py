"""release.py - drip-publishes queued news: each run moves up to 7 items (7 while more than 20 wait, a random 3-7 while 7-20 wait, all when fewer than 7) from queue/ into news/.

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
    for qpath in sorted(glob.glob(os.path.join(Q, "????-??-??.json"))):  # oldest day first
        name = os.path.basename(qpath)
        q = json.load(open(qpath, encoding="utf-8"))
        npath = os.path.join(N, name)
        n = json.load(open(npath, encoding="utf-8")) if os.path.exists(npath) else {**q, "items": []}
        out = {i["symbol"] for i in n["items"]}
        pending = [i for i in q["items"] if i["symbol"] not in out]
        take = pending[:budget]
        if take:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            take = [{**i, "published_at": now} for i in take]  # exact release moment, for the site's hot-news ranking
            n["items"].extend(take)
            json.dump(n, open(npath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            released += [f"{name}:{i['symbol']}" for i in take]
            budget -= len(take)
        if budget <= 0:
            break
    print(f"released {len(released)}: {', '.join(released)}")


if __name__ == "__main__":
    main()
