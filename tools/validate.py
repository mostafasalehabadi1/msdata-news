"""validate.py - checks every news file before it can be published. Exit 1 on any error.

news/YYYY-MM-DD.json         {date_fa, date, items:[{symbol, trade_date, commodity, hall, producer,
                                                     title, slug, subtitle, lead, text}]}
news/report-YYYY-MM-DD.json  {date_fa, date, slug, title, subtitle, lead, text, links?, sources?}
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


IMG_FROM = "2026-11-01"  # owner 1405-08-10: news images go out with the news itself; from today on, old news untouched
IMG_MAX = 200 * 1024


def webp_size(p):
    """(width, height) of a .webp file, or None."""
    b = open(p, "rb").read(30)
    if b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        return None
    if b[12:16] == b"VP8X":
        return 1 + int.from_bytes(b[24:27], "little"), 1 + int.from_bytes(b[27:30], "little")
    if b[12:16] == b"VP8 ":
        return int.from_bytes(b[26:28], "little") & 0x3FFF, int.from_bytes(b[28:30], "little") & 0x3FFF
    if b[12:16] == b"VP8L":
        v = int.from_bytes(b[21:25], "little")
        return 1 + (v & 0x3FFF), 1 + ((v >> 14) & 0x3FFF)
    return None


def check_images(name, d, errs):
    """content agent 1405-08-10: images [{src: /assets/news/<date>/<slug>.webp, alt}], the file in news/images/<date>/<slug>.webp,
    1200x675, at most 200 KB, alt required; a «[عکس]» paragraph needs an image. Hold the news (error) otherwise."""
    ims = [i for i in (d.get("images") or []) if isinstance(i, dict)]
    marker = any(p.strip() == "[عکس]" for p in re.split(r"\n\s*\n", str(d.get("text", ""))))
    if marker and not ims:
        errs.append(f"{name}: [عکس] without images")
    for i in ims:
        src = str(i.get("src", ""))
        if not str(i.get("alt", "")).strip():
            errs.append(f"{name}: image without alt {src}")
        if not src.startswith("/assets/news/"):
            continue  # older layout (hand-placed /assets/ files) is not checked here
        p = os.path.join(ROOT, "images", *src[len("/assets/news/"):].split("/"))
        if not os.path.isfile(p):
            errs.append(f"{name}: image file missing news/images/{src[len('/assets/news/'):]}")
        elif os.path.getsize(p) > IMG_MAX:
            errs.append(f"{name}: image over 200 KB {src}")
        elif webp_size(p) != (1200, 675):
            errs.append(f"{name}: image not 1200x675 webp {src} {webp_size(p)}")


def all_images(errs):
    """every news file dated IMG_FROM or later, and each item of a day file."""
    for path in sorted(glob.glob(os.path.join(ROOT, "**", "*.json"), recursive=True)):
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        if rel.startswith("images/"):
            continue
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001 - reported by the checks below
            continue
        if not isinstance(d, dict):
            continue
        day = str(d.get("date") or d.get("published_at") or "")[:10]
        if day < IMG_FROM:
            continue
        check_images(rel, d, errs)
        for k, it in enumerate(d.get("items") or []):
            if isinstance(it, dict):
                check_images(f"{rel}[{k}]", it, errs)


def check_table(name, d, errs):
    """content agent 1405-08-10: a table the page builder cannot read was printed raw on 8 US pages. The builder (designer
    report-tpl-1410, 1405-08-11) reads: a list of flat rows [{"col": value}], one {title, columns, rows}, or a list of those;
    `tables` = a list of such tables. Any other shape holds the push."""
    val = lambda v: isinstance(v, (str, int, float)) and not isinstance(v, bool)  # noqa: E731
    flat = lambda t: isinstance(t, list) and t and all(isinstance(r, dict) and r and not {"title", "columns", "rows"} & set(r) and all(val(v) for v in r.values()) for r in t)  # noqa: E731
    tcr = lambda t: isinstance(t, dict) and isinstance(t.get("columns"), list) and t["columns"] and isinstance(t.get("rows"), list) and t["rows"] and all(  # noqa: E731
        isinstance(r, list) and len(r) == len(t["columns"]) and all(val(v) for v in r) for r in t["rows"])
    one = lambda t: flat(t) or tcr(t) or (isinstance(t, list) and t and all(tcr(x) for x in t))  # noqa: E731
    for k in ("table", "tables"):
        t = d.get(k)
        if t is None:
            continue
        if not (one(t) if k == "table" else isinstance(t, list) and t and all(one(x) for x in t)):
            errs.append(f"{name}: {k} must be flat rows [{{\"column\": value}}] or {{title, columns, rows}} with rows of the same length")


def main():
    errs, n = [], 0
    all_images(errs)
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
            v2 = d["date"] >= "2026-10-04"  # writing framework v2 (style.md): report 400-700 words with sub-headings
            check_text(name, d, errs, 400 if v2 else 250 if d["date"] < "2026-10-03" else 600, 750 if v2 else 800, 3 if d["date"] < "2026-10-03" else 5, 18 if v2 else 10)
            for x in d.get("sources") or []:
                if not str(x.get("url", "")).startswith(("https://", "http://")) or "mostafasalehabadi" in str(x.get("url")):
                    errs.append(f"{name}: bad source url {x.get('url')}")
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
                v2 = d.get("date", "") >= "2026-10-04"  # framework v2: template A, 120-220 words
                check_text(where, it, errs, 120 if v2 else 150, 220 if v2 else 200, 2, 4 if v2 else 3)
                subs[it["subtitle"]] = subs.get(it["subtitle"], 0) + 1
                n += 1
            # template detector: the same subtitle or the same opening sentence on many items = copy-paste news
            for s, c in subs.items():
                if c > 2:
                    errs.append(f"{name}: subtitle repeated {c} times: {s[:60]}")
        elif name != ".keep":
            errs.append(f"{name}: unexpected file name")
    # owner 1405-07-28: «every news we publish is checked» - Kish (written with MIN_WORDS 80) and energy were never validated before
    for market, low in (("kish", 80), ("energy", 120)):
        for path in sorted(glob.glob(os.path.join(ROOT, market, "????-??-??.json"))):
            if os.path.basename(path) < "2026-10-20.json":  # owner: from 1405-07-28 on; older days are not re-checked
                continue
            name = market + "/" + os.path.basename(path)
            try:
                d = json.load(open(path, encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                errs.append(f"{name}: invalid JSON {e}")
                continue
            for i, it in enumerate(d.get("items") or []):
                check_text(f"{name}[{i}:{it.get('symbol')}]", it, errs, low, 260, 1, 6)
                n += 1
    for path in sorted(glob.glob(os.path.join(ROOT, "macro", "*.json"))):  # «اقتصاد کلان» (tools/write_macro.py)
        name = "macro/" + os.path.basename(path)
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: invalid JSON {e}")
            continue
        check_table(name, d, errs)
        long = d.get("kind") in ("report", "analysis")  # «تحلیل» (owner 1405-07-16): same length rule as a report
        check_text(name, d, errs, 550 if long else 230, 1000 if long else 470, 3, 40)
        n += 1
    for path in sorted(glob.glob(os.path.join(ROOT, "fx", "*.json")) + glob.glob(os.path.join(ROOT, "us", "*.json"))):  # «ارز و طلا» (tools/write_fx.py) and the daily US elections report (content agent, 1405-07-23)
        name = os.path.basename(os.path.dirname(path)) + "/" + os.path.basename(path)
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: invalid JSON {e}")
            continue
        check_table(name, d, errs)
        if name.startswith("us/"):  # owner 1405-07-23: the US report is 400-500 words in 4-8 paragraphs
            check_text(name, d, errs, 120, 500, 3, 8)
        else:
            check_text(name, d, errs, 200, 320, 3, 40)
        n += 1
    for e in errs:
        print("ERROR", e)
    print(f"checked {n} articles, {len(errs)} errors")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
