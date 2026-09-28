"""validate.py - checks every news file before it can be published. Exit 1 on any error.

news/YYYY-MM-DD.json         {date_fa, date, items:[{symbol, trade_date, commodity, hall, producer,
                                                     title, slug, subtitle, lead, text}]}
news/report-YYYY-MM-DD.json  {date_fa, date, slug, title, subtitle, lead, text}
"""
import glob
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "news")
ITEM_FIELDS = ["symbol", "trade_date", "commodity", "hall", "producer", "title", "slug", "subtitle", "lead", "text"]
REPORT_FIELDS = ["date_fa", "date", "slug", "title", "subtitle", "lead", "text"]
FORBIDDEN = re.compile(r"mostafasalehabadi|<script|<\?php|https?://", re.I)
SLUG_BAD = re.compile(r"[\s/?#%&\\\"'<>]")


def words(t):
    return len(re.findall(r"\S+", t))


def paragraphs(t):
    return len([p for p in re.split(r"\n\s*\n", t) if p.strip()])


def check_text(where, d, errs, min_w, max_w, min_p, max_p):
    for k in ("title", "subtitle", "lead", "text", "slug"):
        if not isinstance(d.get(k), str) or not d[k].strip():
            errs.append(f"{where}: empty {k}")
            return
    w, p = words(d["text"]), paragraphs(d["text"])
    if not min_w <= w <= max_w:
        errs.append(f"{where}: text has {w} words (allowed {min_w}-{max_w})")
    if not min_p <= p <= max_p:
        errs.append(f"{where}: text has {p} paragraphs (allowed {min_p}-{max_p})")
    for k in ("title", "subtitle", "lead", "text"):
        if FORBIDDEN.search(d[k]):
            errs.append(f"{where}: forbidden content in {k}")
    if SLUG_BAD.search(d["slug"]):
        errs.append(f"{where}: bad slug")


def main():
    errs, n = [], 0
    for path in sorted(glob.glob(os.path.join(ROOT, "*.json"))):
        name = os.path.basename(path)
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: invalid JSON {e}")
            continue
        if re.fullmatch(r"report-\d{4}-\d{2}-\d{2}\.json", name):
            miss = [k for k in REPORT_FIELDS if k not in d]
            if miss:
                errs.append(f"{name}: missing {miss}")
                continue
            check_text(name, d, errs, 250, 600, 3, 7)
            n += 1
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", name):
            if not isinstance(d.get("items"), list) or not d.get("date_fa"):
                errs.append(f"{name}: needs date_fa and items[]")
                continue
            seen, subs = set(), {}
            for i, it in enumerate(d["items"]):
                where = f"{name}[{i}:{it.get('symbol')}]"
                miss = [k for k in ITEM_FIELDS if k not in it]
                if miss:
                    errs.append(f"{where}: missing {miss}")
                    continue
                if it["symbol"] in seen:
                    errs.append(f"{where}: duplicate symbol")
                seen.add(it["symbol"])
                if it["trade_date"] != d["date_fa"]:
                    errs.append(f"{where}: trade_date != date_fa")
                check_text(where, it, errs, 150, 200, 2, 3)
                subs[it["subtitle"]] = subs.get(it["subtitle"], 0) + 1
                n += 1
            # template detector: the same subtitle or the same opening sentence on many items = copy-paste news
            for s, c in subs.items():
                if c > 2:
                    errs.append(f"{name}: subtitle repeated {c} times: {s[:60]}")
        elif name != ".keep":
            errs.append(f"{name}: unexpected file name")
    for e in errs:
        print("ERROR", e)
    print(f"checked {n} articles, {len(errs)} errors")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
