"""news_flow.py - per-hour flow of the kala market for the owner's stats page (owner 1405-07-22):
how many traded commodities reached the writer, how many news were written and how many were published in each
Tehran hour of a day. Not cumulative. Read from this repo's own history, so past days can be built too.

stats/flow.json: {"updated": ..., "days": {"2026-10-13": {"date_fa": "1405/07/21", "kala": [24], "written": [24], "published": [24]}}}
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


def history(path, day, count):
    """[(Tehran time, count)] of one file through the day's commits, oldest first."""
    d = dt.date.fromisoformat(day)
    since, until = f"{d - dt.timedelta(days=1)}T20:30:00Z", f"{d + dt.timedelta(days=1)}T06:00:00Z"
    pts = []
    for line in git("log", "--format=%H %ct", f"--since={since}", f"--until={until}", "--", path).split("\n"):
        if not line.strip():
            continue
        h, t = line.split()
        try:
            pts.append((dt.datetime.fromtimestamp(int(t), TEHRAN), count(json.loads(git("show", f"{h}:{path}")))))
        except (ValueError, KeyError, TypeError):
            continue
    return sorted(pts)


def traded(j):  # same rows the writer takes: traded, not the secondary hall
    return len({r["symbol"] for r in j["rows"] if float(r.get("traded_qty") or 0) > 0 and "فرعی" not in (r.get("talar") or "")})


def hour_of(t, day):
    """Tehran hour 0-23 inside the day; a late commit after midnight counts in the last hour, an early one in the first."""
    return 23 if t.date().isoformat() > day else 0 if t.date().isoformat() < day else t.hour


def per_hour(pts, day):
    out, last = [0] * 24, 0
    for t, n in pts:
        if n > last:
            out[hour_of(t, day)] += n - last
            last = n
    return out


def build(day):
    try:
        cur = json.load(open(os.path.join(ROOT, "news", day + ".json"), encoding="utf-8"))
    except (OSError, ValueError):
        cur = {}
    pub = [0] * 24
    for it in cur.get("items") or []:
        try:
            pub[hour_of(dt.datetime.fromisoformat(it["published_at"].replace("Z", "+00:00")).astimezone(TEHRAN), day)] += 1
        except (KeyError, ValueError):
            continue
    row = {"date_fa": cur.get("date_fa"), "kala": per_hour(history(f"data/{day}/today.json", day, traded), day),
           "written": per_hour(history(f"queue/{day}.json", day, lambda j: len(j["items"])), day), "published": pub}
    return row if any(sum(row[k]) for k in ("kala", "written", "published")) else None


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
    doc["note"] = "kala market only; counts per Tehran hour, not cumulative; kala = traded commodities that reached the writer in that hour"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(doc, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, separators=(",", ":"))
    print(f"news flow: {len(doc['days'])} days")


if __name__ == "__main__":
    main()
