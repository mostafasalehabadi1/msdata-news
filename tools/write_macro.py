"""write_macro.py - automatic «اقتصاد کلان» news: every time a new macro / open-market (bazarbaz) data point appears on
msdata.ir, one news is written with the free models under the writing framework v2 (tools/style.md, full mode).

Every series is weekly or less often and gets a ~800-word
report. Output: news/macro/<date>-<series>-<key>.json (published at once, like the daily report) with tag «اقتصاد کلان».
State: data/macro_seen.json (last key of every series). On the first run the state is only initialised, nothing is written,
so old data never floods the site.
usage: python tools/write_macro.py [--dry]
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import markers  # noqa: E402
import write_free as w  # noqa: E402
from write_report import RANK, REPORT_MODEL  # noqa: E402
GEMMA_ONLY = 3600  # same rule as the daily report: only Gemma 4 31B for the first hour, then the owner's rating order
from validate import FORBIDDEN, SLUG_BAD, paragraphs, words  # noqa: E402

ROOT = w.ROOT
API = "https://msdata.ir/api/bazarbaz/"
TEHRAN = timezone(timedelta(hours=3, minutes=30))
SEEN = os.path.join(ROOT, "data", "macro_seen.json")
OUT = os.path.join(ROOT, "news", "macro")
TAG = "اقتصاد کلان"


def get(name):
    req = urllib.request.Request(API + name, headers={"User-Agent": "msdata-news/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def jkey(k):  # 1405/07/04 -> sortable
    return tuple(int(x) for x in re.findall(r"\d+", str(k)))


def series():
    """-> {name: (subject, long?, key of the newest point, facts dict)}; a series whose file fails is skipped this round."""
    out = {}
    try:
        raw = get("raw-data.json")
        # weekly open-market report of the central bank: repo auction + standing (rule-based) credit facility, one report together
        a, c = raw.get("auctions") or {}, raw.get("credit_days") or {}
        if a and c:
            ka, kc = sorted(a, key=jkey), sorted(c, key=jkey)
            key = max(ka[-1], kc[-1], key=jkey)
            out["openmarket-week"] = ("گزارش هفتگی بازار باز بانک مرکزی: حراج ریپو و اعتبار قاعده‌مند", True, key,
                                      {"حراج ریپوی این هفته": a[ka[-1]], "حراج‌های قبلی": [a[k] for k in ka[-6:-1]],
                                       "اعتبار قاعده‌مند روزهای این گزارش": {k: c[k] for k in kc[-7:]},
                                       "اعتبار قاعده‌مند روزهای قبل": {k: c[k] for k in kc[-21:-7]}})
    except Exception as e:  # noqa: BLE001
        print(f"raw-data: {e}")
    try:
        p = get("cpi-lite.json")
        rows, cols = p.get("rows") or [], p.get("columns") or []
        nat = [r for r in rows if r[0] == "national"]  # area_id, group_id, month, index, mom, yoy, 12m
        if nat:
            last = max(r[2] for r in nat)
            tot = sorted((r for r in nat if r[1] == "total"), key=lambda r: r[2])
            out["cpi"] = ("شاخص قیمت مصرف‌کننده و تورم کل کشور", True, last.replace("-", "/"),
                          {"ستون‌ها": cols, "کل کشور، ۱۳ ماه اخیر": tot[-13:],
                           "گروه‌ها در آخرین ماه": [r for r in nat if r[2] == last and r[1] != "total"],
                           "شهری و روستایی در آخرین ماه": [r for r in rows if r[0] in ("urban", "rural") and r[2] == last and r[1] == "total"],
                           "منبع": p.get("source"), "پایه": p.get("base"), "واحد": p.get("unit")})
    except Exception as e:  # noqa: BLE001
        print(f"cpi-lite: {e}")
    return out


def rules(long):
    size = "۷۰۰ تا ۹۰۰ کلمه با میان‌تیتر (هر میان‌تیتر یک خط کوتاه جدا، بدون نقطه)" if long else "۲۵۰ تا ۴۵۰ کلمه"
    return ("تو خبرنگار اقتصاد کلان msdata.ir هستی و درباره‌ی یک داده‌ی تازه‌ی اقتصاد کلان خبر فارسی می‌نویسی. "
            "چارچوب نگارش زیر را مو به مو رعایت کن، در «حالت کامل» (بخش ۲-۱۴)، با «قالب ه» (خبر داده‌ی شش‌بندی)"
            + (" و تحلیل و سناریوی بخش ۲-۱۵" if long else "") + ":\n\n" + w.STYLE + "\n\n"
            f"- text: {size}؛ پاراگراف‌ها جدا با یک خط خالی. عددها فقط از «داده». لینک، آدرس اینترنتی و HTML ننویس.\n"
            "- table: ۳ تا ۸ ردیف از داده، هر ردیف یک شیء با نام ستون‌ها (بخش ۲-۱۶).\n"
            "- scenarios: " + ("۲ یا ۳ سناریوی قابل‌بررسی به شکل JSON بخش ۲-۱۵.\n" if long else "فهرست خالی، مگر سناریوی روشن و قابل‌بررسی داشته باشی.\n") +
            "- slug: فارسی با خط تیره، بدون فاصله و علامت.\n"
            "فقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text, table, scenarios و هیچ متن دیگری.")


def check(d, long):
    for k in ("title", "slug", "subtitle", "lead", "text"):
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise ValueError(f"empty {k}")
    d["text"] = d["text"].replace("\r", "").strip()
    n, p = words(d["text"]), paragraphs(d["text"])
    lo, hi = (650, 950) if long else (230, 470)
    if not lo <= n <= hi or p < 3:
        raise ValueError(f"{n} words / {p} paragraphs")
    if any(FORBIDDEN.search(d[k]) for k in ("title", "subtitle", "lead", "text")):
        raise ValueError("forbidden content")
    d["table"] = [x for x in d.get("table") or [] if isinstance(x, dict)][:8]
    d["scenarios"] = [x for x in d.get("scenarios") or [] if isinstance(x, dict)]
    probs = markers.check(d["text"], d["title"], d["lead"], table=bool(d["table"]), full=True)
    if len(d["table"]) < 3:
        probs.append("table باید ۳ تا ۸ ردیف داشته باشد")
    if probs:
        raise ValueError("markers: " + " | ".join(probs))
    d["slug"] = SLUG_BAD.sub("-", d["slug"].strip())
    return d


def write(name, subject, long, key, facts, first_seen):
    now = datetime.now(TEHRAN)
    prompt = (f"موضوع: {subject}\nتاریخ انتشار: {now.strftime('%Y-%m-%d')} (میلادی؛ در متن تاریخ شمسی داده را بنویس)\n"
              "داده (فقط همین عددها را به کار ببر):\n" + json.dumps(facts, ensure_ascii=False, indent=1))
    w.RULES = rules(long)
    provs = w.providers()
    provs.sort(key=lambda p: RANK.index(p[0]) if p[0] in RANK else len(RANK))
    if time.time() - first_seen < GEMMA_ONLY:
        provs = [p for p in provs if p[0] == REPORT_MODEL]
    for model, call in provs:
        if model in w.exhausted():
            continue
        try:
            raw = w.retry(call, prompt)
            for attempt in range(3):  # style.md section 6: hits go back to the model, up to 2 rewrites
                try:
                    d = check(json.loads(raw[raw.index("{"):raw.rindex("}") + 1]), long)
                    break
                except ValueError as e:
                    if attempt == 2 or not re.search(r"words|markers|table", str(e)):
                        raise
                    raw = w.retry(call, prompt + "\n\nپیش‌نویس قبلی تو:\n" + raw + f"\n\nایرادها: {e}. همان خبر را با رفع همه‌ی این ایرادها "
                                  "بازنویسی کن و فقط JSON برگردان.")
            item = {"date": now.strftime("%Y-%m-%d"), "series": name, "key": key, "tag": TAG, "kind": "report" if long else "news",
                    **d, "model": model, "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            w.save_scenarios(d["scenarios"], name, key)
            return item
        except Exception as e:  # noqa: BLE001 - next model
            print(f"  {name} {model}: {type(e).__name__}: {str(e)[:200]}")
            if isinstance(e, w.QuotaError):
                open(w.EXHAUSTED_FILE, "a", encoding="utf-8").write(model + "\n")
    return None


def main():
    seen = json.load(open(SEEN, encoding="utf-8")) if os.path.exists(SEEN) else None
    cur = series()
    if seen is None:  # first run: remember where the data is, write nothing
        json.dump({k: v[2] for k, v in cur.items()}, open(SEEN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"initialised {len(cur)} series")
        return
    new = {k: v for k, v in cur.items() if k not in seen or jkey(v[2]) > jkey(seen[k])}
    print(f"{len(new)} new macro data: {', '.join(f'{k}={v[2]}' for k, v in new.items()) or '-'}")
    if "--dry" in sys.argv:
        return
    os.makedirs(OUT, exist_ok=True)
    pend = seen.setdefault("_pending", {})
    for name, (subject, long, key, facts) in new.items():
        t0 = time.time()
        first = pend.setdefault(f"{name}:{key}", int(t0))
        json.dump(seen, open(SEEN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        item = write(name, subject, long, key, facts, first)
        if not item:
            print(f"{name}: no model answered; next round tries again")
            continue
        path = os.path.join(OUT, f"{item['date']}-{name}-{re.sub(r'[^0-9]', '', str(key))}.json")
        json.dump(item, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        seen[name] = key
        pend.pop(f"{name}:{key}", None)
        json.dump(seen, open(SEEN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"macro {name} {key} <- {item['model']} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
