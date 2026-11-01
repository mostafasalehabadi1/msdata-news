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


def history(path, day, symbols, until_day=None):
    """{symbol: Tehran time of the first commit of the day's file that holds it}, looked up to the end of until_day"""
    d, u = dt.date.fromisoformat(day), dt.date.fromisoformat(until_day or day)
    since, until = f"{d - dt.timedelta(days=1)}T20:30:00Z", f"{u + dt.timedelta(days=1)}T06:00:00Z"
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
    """Tehran minute 0-1439, or None when t is on another day (owner 1405-08-10: count by the real time, whatever day the news is about)."""
    return t.hour * 60 + t.minute if t.date().isoformat() == day else None


def series(times, day):
    """-> (24 counts per hour, sparse [[minute, count], ...])"""
    hours, mins = [0] * 24, {}
    for t in times:
        m = minute_of(t, day)
        if m is None:
            continue
        hours[m // 60] += 1
        mins[m] = mins.get(m, 0) + 1
    return hours, [[m, n] for m, n in sorted(mins.items())]


def median(xs):
    xs = sorted(xs)
    return None if not xs else round((xs[len(xs) // 2] + xs[(len(xs) - 1) // 2]) / 2, 1)


BACK = 10  # days: a news file of an older day written or published today counts today


def jalali(day):
    """2026-11-01 -> 1405/08/10 (for a day with no news file of its own)"""
    gy, gm, gd = map(int, day.split("-"))
    g2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (g2 + 3) // 4 - (g2 + 99) // 100 + (g2 + 399) // 400 + gd + [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334][gm - 1]
    jy = -1595 + 33 * (days // 12053); days %= 12053
    jy += 4 * (days // 1461); days %= 1461
    if days > 365:
        jy += (days - 1) // 365; days = (days - 1) % 365
    jm = 1 + days // 31 if days < 186 else 7 + (days - 186) // 30
    jd = 1 + (days % 31 if days < 186 else (days - 186) % 30)
    return f"{jy}/{jm:02d}/{jd:02d}"


def on_day(times, day):
    return {k: t for k, t in times.items() if t.date().isoformat() == day}


def build(day):
    """every event of the Tehran day, from the files of that day and the BACK days before it"""
    rows = [build_src((dt.date.fromisoformat(day) - dt.timedelta(days=i)).isoformat(), day) for i in range(BACK + 1)]
    row = {"date_fa": rows[0]["date_fa"] or jalali(day)}
    ev = {k: {} for k in ("kala", "written", "published")}
    types = {}
    for i, r in enumerate(rows):
        for k in ev:
            ev[k].update({(i, s): t for s, t in on_day(r["ev"][k], day).items()})
        for m, (a, w, p) in r["types"].items():
            x = types.setdefault(m, ({}, {}, {}))
            for dst, src in zip(x, (a, w, p)):
                dst.update({(i, s): t for s, t in on_day(src, day).items()})
    mins = {}
    for k in ev:
        row[k], mins[k] = series(ev[k].values(), day)
    row["minutes"] = mins
    gap = lambda a, b: [(b[x] - a[x]).total_seconds() / 60 for x in b if x in a and b[x] >= a[x]]  # noqa: E731
    row["lag_min"] = {"write": median(gap(ev["kala"], ev["written"])), "publish": median(gap(ev["written"], ev["published"])), "total": median(gap(ev["kala"], ev["published"]))}
    row["types"] = {k: v for k, v in ((m, typed(*x, day)) for m, x in types.items()) if v}
    return row if any(sum(row[k]) for k in ("kala", "written", "published")) or row["types"] else None


def build_src(day, until):
    """raw event times of the files of one news day, looked up to the end of until"""
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
    kala = history(f"data/{day}/today.json", day, traded, until)
    wrote = history(f"queue/{day}.json", day, lambda j: {i["symbol"] for i in j["items"]}, until)
    types = {"kala": (kala, wrote, pub)}  # designer stats-types-1028 (owner 1405-07-28): every news type
    for m in ("kish", "energy"):  # one news per traded row, queue -> news like kala
        arr = history(f"data/{m}/today.json", day, lambda j: {i["symbol"] for i in j.get("items") or [] if j.get("date") == day}, until)
        wr = history(f"queue/{m}/{day}.json", day, lambda j: {i["symbol"] for i in j["items"]}, until)
        types[m] = (arr, wr, published(f"news/{m}/{day}.json"))
    for m in ("fx", "macro", "us"):  # written and published at once: arrival = the commit that wrote the file
        first = {}
        for f in sorted(os.listdir(os.path.join(ROOT, "news", m))) if os.path.isdir(os.path.join(ROOT, "news", m)) else []:
            if f.startswith(day) and f.endswith(".json"):
                t = git("log", "--diff-filter=A", "--format=%ct", "-1", "--", f"news/{m}/{f}").strip()
                if t:
                    first[f] = dt.datetime.fromtimestamp(int(t), TEHRAN)
        types[m] = (first, first, first)
    return {"date_fa": cur.get("date_fa"), "ev": {"kala": kala, "written": wrote, "published": pub}, "types": types}


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
