"""release.py - drip-publishes queued news: each run moves up to 7 items (always 7 while more than 20 wait, else a random 3-7) from queue/ into news/.

queue/<date>.json holds every written item of a day; news/<date>.json holds only the released ones, so the
site grows a few articles at a time like a human newsroom instead of hundreds at once. The daily report
(queue/report-<date>.json) is released after all of that day's items are out.
"""
import glob
import json
import os
import random
import shutil

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
    # at most 7 per run; with more than 20 waiting always the maximum, otherwise a random 3-7
    budget = 7 if backlog() > 20 else random.randint(3, 7)
    os.makedirs(N, exist_ok=True)
    released = []
    for qpath in sorted(glob.glob(os.path.join(Q, "????-??-??.json"))):  # oldest day first
        name = os.path.basename(qpath)
        q = json.load(open(qpath, encoding="utf-8"))
        npath = os.path.join(N, name)
        n = json.load(open(npath, encoding="utf-8")) if os.path.exists(npath) else {**q, "items": []}
        out = {i["symbol"] for i in n["items"]}
        pending = [i for i in q["items"] if i["symbol"] not in out]
        take = pending[:budget]
        if take:
            n["items"].extend(take)
            json.dump(n, open(npath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            released += [f"{name}:{i['symbol']}" for i in take]
            budget -= len(take)
        rep = os.path.join(Q, "report-" + name)
        if len(pending) == len(take) and os.path.exists(rep) and not os.path.exists(os.path.join(N, "report-" + name)):
            shutil.copy(rep, os.path.join(N, "report-" + name))
            released.append("report-" + name)
        if budget <= 0:
            break
    print(f"released {len(released)}: {', '.join(released)}")


if __name__ == "__main__":
    main()
