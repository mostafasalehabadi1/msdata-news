"""news_flow.py - per-hour flow of the kala market for the owner's stats page (owner 1405-07-22):
how many traded commodities reached the writer, how many news were written and how many were published in each
Tehran hour of a day. Not cumulative. Read from this repo's own history, so past days can be built too.

stats/flow.json: {"updated": ..., "days": {"2026-10-13": {"date_fa": "1405/07/21", "kala": [24], "written": [24], "published": [24],
    "minutes": {"kala": [[minute, count], ...], "written": [...], "published": [...]},   minute 0-1439 of the Tehran day
    "lag_min": {"write": median minutes from arrival to written, "publish": written to published, "total": arrival to published}}}}
usage: python tools/news_flow.py            today and (before 03:00 Tehran) yesterday
       python tools/news_flow.py --days 20  the last 20 days (backfill)
"""
import datetime as dt
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "stats", "flow.json")
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))
KEEP = 120  # days


def git(*a):
    return subprocess.run(["git", "-C", ROOT] + list(a), capture_output=True).stdout.decode("utf-8-sig", "replace")


def history(path, day, symbols):
    """{symbol: Tehran time of the first commit of the day's file that holds it}"""
    d = dt.date.fromisoformat(day)
    since, until = f"{d - dt.timedelta(days=1)}T20:30:00Z", f"{d + dt.timedelta(days=1)}T06:00:00Z"
    first = {}
    for line in reversed(git("log", "--format=%H %ct", f"--since={since}", f"--until={until}", "--", path).split("\n")):
        if not line.strip():
            continue
        h, t = line.split()
        try:
            for sym in symbols(json.loads(git("show", f"{h}:{path}"))):
                first.setdefault(sym, dt.datetime.fromtimestamp(int(t), TEHRAN))
        except (ValueError, KeyError, TypeError):
            continue
    return first


def traded(j):  # same rows the writer takes: traded, not the secondary hall
    return {r["symbol"] for r in j["rows"] if float(r.get("traded_qty") or 0) > 0 and "فرعی" not in (r.get("talar") or "")}


def minute_of(t, day):
    """Tehran minute 0-1439 inside the day; a late commit after midnight counts in the last minute, an early one in the first."""
    return 1439 if t.date().isoformat() > day else 0 if t.date().isoformat() < day else t.hour * 60 + t.minute


def series(times, day):
    """-> (24 counts per hour, sparse [[minute, count], ...])"""
    hours, mins = [0] * 24, {}
    for t in times:
        m = minute_of(t, day)
        hours[m // 60] += 1
        mins[m] = mins.get(m, 0) + 1
    return hours, [[m, n] for m, n in sorted(mins.items())]


def median(xs):
    xs = sorted(xs)
    return None if not xs else round((xs[len(xs) // 2] + xs[(len(xs) - 1) // 2]) / 2, 1)


def build(day):
    try:
        cur = json.load(open(os.path.join(ROOT, "news", day + ".json"), encoding="utf-8"))
    except (OSError, ValueError):
        cur = {}
    pub = {}
    for it in cur.get("items") or []:
        try:
            pub[it["symbol"]] = dt.datetime.fromisoformat(it["published_at"].replace("Z", "+00:00")).astimezone(TEHRAN)
        except (KeyError, ValueError):
            continue
    kala = history(f"data/{day}/today.json", day, traded)
    wrote = history(f"queue/{day}.json", day, lambda j: {i["symbol"] for i in j["items"]})
    row, mins = {"date_fa": cur.get("date_fa")}, {}
    for k, times in (("kala", kala), ("written", wrote), ("published", pub)):
        row[k], mins[k] = series(times.values(), day)
    row["minutes"] = mins  # owner 1405-07-22: the exact minute, [[minute of the day in Tehran, count], ...]
    gap = lambda a, b: [(b[x] - a[x]).total_seconds() / 60 for x in b if x in a and b[x] >= a[x]]  # noqa: E731
    row["lag_min"] = {"write": median(gap(kala, wrote)), "publish": median(gap(wrote, pub)), "total": median(gap(kala, pub))}
    types = {"kala": typed(kala, wrote, pub, day)}  # designer stats-types-1028 (owner 1405-07-28): every news type
    for m in ("kish", "energy"):  # one news per traded row, queue -> news like kala
        arr = history(f"data/{m}/today.json", day, lambda j: {i["symbol"] for i in j.get("items") or [] if j.get("date") == day})
        wr = history(f"queue/{m}/{day}.json", day, lambda j: {i["symbol"] for i in j["items"]})
        types[m] = typed(arr, wr, published(f"news/{m}/{day}.json"), day)
    for m in ("fx", "macro", "us"):  # written and published at once: arrival = the commit that wrote the file
        first = {}
        for f in sorted(os.listdir(os.path.join(ROOT, "news", m))) if os.path.isdir(os.path.join(ROOT, "news", m)) else []:
            if f.startswith(day) and f.endswith(".json"):
                t = git("log", "--diff-filter=A", "--format=%ct", "-1", "--", f"news/{m}/{f}").strip()
                if t:
                    first[f] = dt.datetime.fromtimestamp(int(t), TEHRAN)
        types[m] = typed(first, first, first, day)
    row["types"] = {k: v for k, v in types.items() if v}
    return row if any(sum(row[k]) for k in ("kala", "written", "published")) or row["types"] else None


def published(path):
    try:
        cur = json.load(open(os.path.join(ROOT, path), encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for it in cur.get("items") or []:
        try:
            out[it["symbol"]] = dt.datetime.fromisoformat(it["published_at"].replace("Z", "+00:00")).astimezone(TEHRAN)
        except (KeyError, ValueError):
            continue
    return out


def typed(arr, wr, pub, day):
    """{"arrived","written","published": [[minute, count], ...], "lag_min": {...}} or None when the type had nothing that day"""
    if not (arr or wr or pub):
        return None
    gap = lambda a, b: [(b[x] - a[x]).total_seconds() / 60 for x in b if x in a and b[x] >= a[x]]  # noqa: E731
    return {"arrived": series(arr.values(), day)[1], "written": series(wr.values(), day)[1], "published": series(pub.values(), day)[1],
            "lag_min": {"write": median(gap(arr, wr)), "publish": median(gap(wr, pub)), "total": median(gap(arr, pub))}}


def main():
    now = dt.datetime.now(TEHRAN)
    n = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else (2 if now.hour < 3 else 1)
    try:
        doc = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        doc = {"days": {}}
    before = json.dumps(doc.get("days"), sort_keys=True)
    for i in range(n):
        day = (now.date() - dt.timedelta(days=i)).isoformat()
        row = build(day)
        if row:
            doc["days"][day] = row
    doc["days"] = dict(sorted(doc["days"].items())[-KEEP:])
    if json.dumps(doc["days"], sort_keys=True) == before and os.path.exists(OUT):
        print("news flow: no change")  # no new file, so the hourly release does not commit for a timestamp alone
        return
    doc["updated"] = now.isoformat(timespec="minutes")
    doc["note"] = "top level: kala market only (types: every news type); counts per Tehran hour, not cumulative; kala = traded commodities that reached the writer in that hour"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(doc, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, separators=(",", ":"))
    print(f"news flow: {len(doc['days'])} days")


if __name__ == "__main__":
    main()
