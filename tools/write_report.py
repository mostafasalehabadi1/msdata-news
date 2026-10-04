"""write_report.py - the daily market report (600-800 words, analysis, causes from the news, outlook), 18:30 Tehran.

Runs from report.yml; does nothing before 18:30 Tehran, on a day whose data is not today's, or when the report exists.
Then it refreshes the data, builds a whole-market fact sheet (every trade named with its symbol), searches Bing News
for today's context of the biggest trades, and asks Gemma 4 31B - the model kept for this report only. Until 19:30
only Gemma writes it; after that the other models follow in the owner's rating order so the day never goes without one.
The answer carries links (trade -> symbol, for the site to link its page) and sources (external news it used).
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import write_free as w  # noqa: E402
import markers  # noqa: E402
from factsheet import YEAR, fa_date, fa_int, fa_num, toman_billion  # noqa: E402
from validate import FORBIDDEN, SLUG_BAD, paragraphs, words  # noqa: E402

ROOT = w.ROOT
# optional fixed headline: --headline "..." or the first line of report-now.txt after "headline:"
HEADLINE = ""
MIXED = re.compile(r"[؀-ۿ][A-Za-z]|[A-Za-z][؀-ۿ]")
TEHRAN = timezone(timedelta(hours=3, minutes=30))
REPORT_MODEL = "gemini:gemma-4-31b-it"
FALLBACK_FROM = (19, 30)
# the owner's blind rating (2026-09-30/10-01), best first; models not listed come after them
RANK = [REPORT_MODEL, "llm7:DeepSeek-V4-Flash-0731", "cohere:command-a-03-2025",
        "kilo:dots-studio/dots-3-note-preview:free", "hf:deepseek-ai/DeepSeek-V3.1", "kilo:stepfun/step-3.7-flash:free",
        "zai:glm-4.5-flash", "kilo:nvidia/nemotron-3-ultra-550b-a55b:free", "cf:@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "cf:@cf/qwen/qwen2.5-coder-32b-instruct", "llm7:mistral-Nemo-Instruct-2407"]
REPORT_RULES = (
    "تو سردبیر بورس کالای msdata.ir هستی و گزارش پایان روز بازار فیزیکی بورس کالا را به فارسی می‌نویسی. "
    "چارچوب نگارش زیر را مو به مو رعایت کن، در «حالت کامل» (بخش ۲-۱۴) و با «قالب ب»:\n\n" + w.style_for("ب") + "\n\n"
    "- عددها فقط از «فکت‌شیت»؛ علت‌ها فقط از «خبرهای رسانه‌ها» با نام رسانه در متن.\n"
    "- text: ۴۵۰ تا ۶۵۰ کلمه؛ پاراگراف‌ها جدا با یک خط خالی؛ هر میان‌تیتر یک خط کوتاه جدا (بدون نقطه) است. لینک، آدرس اینترنتی و HTML در متن نگذار.\n"
    "- table: ۳ تا ۸ ردیف عدد مهم روز از فکت‌شیت، هر ردیف یک شیء با نام ستون‌ها (بخش ۲-۱۶).\n"
    "- scenarios: ۲ یا ۳ سناریوی قابل‌بررسی به شکل JSON بخش ۲-۱۵.\n"
    "- links: برای هر معامله‌ای که در متن نام بردی {\"title\": عبارت دقیقاً همان‌طور که در متن آمده، \"symbol\": نماد از فکت‌شیت}.\n"
    "- sources: برای هر خبر بیرونی که استفاده کردی {\"title\": نام رسانه همان‌طور که در متن آمده، \"url\": آدرس همان خبر از فهرست}.\n"
    "- slug: فارسی با خط تیره، بدون فاصله و علامت.\n"
    "فقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text, table, scenarios, links, sources و هیچ متن دیگری.")


def val(r):
    return r.get("trade_value") or 0


def comp(r):
    return (r["weighted_price"] / r["weighted_base_price"] - 1) * 100 if r.get("weighted_price") and r.get("weighted_base_price") else 0


def name(r):
    return f"{r.get('goods_name')} {r.get('producer_name')}"


def facts(rows, date_fa):
    YEAR[0] = date_fa[:4]
    total = sum(val(r) for r in rows)
    L = [f"تاریخ: {fa_date(date_fa)} {date_fa[:4].translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))}",
         f"کل بازار: {fa_int(len(rows))} نماد معامله شد، از {fa_int(len({r.get('goods_name') for r in rows}))} کالا و "
         f"{fa_int(len({r.get('producer_name') for r in rows}))} عرضه‌کننده؛ ارزش کل معاملات {toman_billion(total)}."]
    halls = {}
    for r in rows:
        halls.setdefault(r.get("talar") or "نامشخص", []).append(r)
    for h, rs in sorted(halls.items(), key=lambda x: -sum(val(r) for r in x[1])):
        v = sum(val(r) for r in rs)
        share = v / total * 100 if total else 0
        share = "کمتر از ۰٫۱" if 0 < share < 0.1 else fa_num(share)
        L.append(f"{h}: {fa_int(len(rs))} نماد، " + (f"ارزش {toman_billion(v)} ({share} درصد کل بازار)." if v else "ارزش معامله ثبت نشده."))
    L.append("بزرگ‌ترین معامله‌ها: " + "؛ ".join(f"{name(r)}: {toman_billion(val(r))}، رقابت {fa_num(comp(r))} درصد" for r in sorted(rows, key=val, reverse=True)[:8]))
    hot = [r for r in sorted(rows, key=comp, reverse=True) if comp(r) > 0][:5]
    if hot:
        L.append("بیشترین رقابت (نرخ معامله بالاتر از قیمت پایه): " + "؛ ".join(f"{name(r)}: {fa_num(comp(r))} درصد" for r in hot))
    at_base = sum(1 for r in rows if abs(comp(r)) < 0.05)
    more_demand = sum(1 for r in rows if (r.get("demand_qty") or 0) > (r.get("offered_qty") or 0))
    L.append(f"{fa_int(at_base)} نماد روی قیمت پایه معامله شد؛ در {fa_int(more_demand)} نماد سفارش خریداران از عرضه بیشتر بود.")
    ch = [r for r in rows if r.get("price_change_pct") is not None]
    up = [r for r in sorted(ch, key=lambda r: r["price_change_pct"], reverse=True)[:4] if r["price_change_pct"] > 0]
    down = [r for r in sorted(ch, key=lambda r: r["price_change_pct"])[:4] if r["price_change_pct"] < 0]
    if up:
        L.append("بیشترین افزایش نرخ نسبت به معامله‌ی قبلی همان نماد: " + "؛ ".join(f"{name(r)}: {fa_num(r['price_change_pct'])} درصد" for r in up))
    if down:
        L.append("بیشترین کاهش نرخ نسبت به معامله‌ی قبلی همان نماد: " + "؛ ".join(f"{name(r)}: {fa_num(abs(r['price_change_pct']))} درصد" for r in down))
    named = sorted(rows, key=val, reverse=True)[:8] + hot + up + down
    L.append("\nنماد هر معامله (فقط برای links؛ هرگز در متن ننویس): " +
             "؛ ".join(f"{name(r)} = {r['symbol']}" for r in {r["symbol"]: r for r in named}.values()))
    return "\n".join(L)


def news(rows, days=3):
    """today's context from Bing News RSS (no key): the biggest trades' commodities + the market in general."""
    queries = ["بورس کالا"] + list(dict.fromkeys(f"{r.get('goods_name')} بورس کالا" for r in sorted(rows, key=val, reverse=True)[:6]))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out, seen = [], set()
    for q in queries:
        url = "https://www.bing.com/news/search?format=rss&setlang=fa&q=" + urllib.parse.quote(q)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
                xml = r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - context is optional; the report still has the numbers
            print(f"  news search failed ({q}): {e}")
            continue
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:6]:
            get = lambda tag: unescape((re.search(rf"<{tag}>(.*?)</{tag}>", item, re.S) or [None, ""])[1]).strip()  # noqa: E731
            link = get("link")
            real = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("url", [link])[0]
            try:
                when = parsedate_to_datetime(get("pubDate"))
            except Exception:  # noqa: BLE001
                continue
            if when < since or real in seen or not real.startswith("http") or FORBIDDEN.search(real.replace("http", "", 1)):
                continue
            seen.add(real)
            host = urllib.parse.urlparse(real).netloc.replace("www.", "")
            out.append({"title": re.sub(r"<[^>]+>", "", get("title")), "summary": re.sub(r"<[^>]+>", "", get("description"))[:300],
                        "site": host, "url": real})
    return out[:15]


def check(d, symbols, urls):
    for k in ("title", "slug", "subtitle", "lead", "text"):
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise ValueError(f"empty {k}")
    d["text"] = d["text"].replace("\r", "").strip()
    n, p = words(d["text"]), paragraphs(d["text"])
    if not 400 <= n <= 750 or not 5 <= p <= 18:
        raise ValueError(f"{n} words / {p} paragraphs")
    if any(FORBIDDEN.search(d[k]) for k in ("title", "subtitle", "lead", "text")):
        raise ValueError("forbidden content")
    if re.search(r"[A-Z]{2,}-[A-Z0-9.]+-\d\d", d["text"]):
        raise ValueError("symbol code in text")
    if MIXED.search(d["text"]):  # a model that slips Latin letters into a Persian word (سولfurیک)
        raise ValueError("mixed-script word")
    d["slug"] = SLUG_BAD.sub("-", d["slug"].strip())
    # keep only links the site can resolve: the anchor must be in the text, the symbol a trade of today
    d["links"] = [{"title": x["title"], "symbol": x["symbol"]} for x in d.get("links") or []
                  if isinstance(x, dict) and x.get("symbol") in symbols and x.get("title") and x["title"] in d["text"]]
    # and only sources that were really given to the model (no invented addresses)
    d["sources"] = [{"title": x["title"], "url": x["url"]} for x in d.get("sources") or []
                    if isinstance(x, dict) and x.get("url") in urls and x.get("title") and x["title"] in d["text"]]
    if not d["links"]:
        raise ValueError("no trade links")
    d["table"] = [x for x in d.get("table") or [] if isinstance(x, dict)][:8]
    d["scenarios"] = [x for x in d.get("scenarios") or [] if isinstance(x, dict)]
    probs = markers.check(d["text"], "" if HEADLINE else d["title"], d["lead"], table=False, full=True)
    if probs:
        raise ValueError("markers: " + " | ".join(probs))
    if HEADLINE:
        d["title"] = HEADLINE
    return d


def main():
    now = datetime.now(TEHRAN)
    force = "--now" in sys.argv
    global HEADLINE
    if "--headline" in sys.argv:
        HEADLINE = sys.argv[sys.argv.index("--headline") + 1].strip()
    else:
        hp = os.path.join(ROOT, "report-now.txt")
        m = re.search(r"^headline:\s*(.+)$", open(hp, encoding="utf-8").read(), re.M) if os.path.exists(hp) else None
        HEADLINE = m.group(1).strip() if m and force else ""
    if (now.hour, now.minute) < (18, 30) and not force:
        return
    latest = json.load(open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8"))
    date = latest["date"]
    if date != now.strftime("%Y-%m-%d") and not force:
        return  # no trading today
    out = os.path.join(ROOT, "queue", f"report-{date}.json")
    if os.path.exists(out) or os.path.exists(os.path.join(ROOT, "news", f"report-{date}.json")):
        return
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "fetch.py")], check=False)  # the day's final numbers
    latest = json.load(open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8"))
    date, date_fa = latest["date"], latest["date_fa"]
    rows = json.load(open(os.path.join(ROOT, "data", date, "today.json"), encoding="utf-8"))["rows"]
    if not rows:
        return
    ctx = news(rows)
    prompt = (f"تیتر گزارش از پیش تعیین شده: «{HEADLINE}»؛ متن را با همین تیتر هماهنگ بنویس.\n\n" if HEADLINE else "") + ("فکت‌شیت کل بازار امروز (فقط همین عددها را به کار ببر):\n" + facts(rows, date_fa) +
              "\n\nخبرهای امروز در رسانه‌ها (فقط برای علت‌ها؛ هر کدام را استفاده کردی در sources بیاور):\n" +
              ("\n".join(f"- {x['site']} | {x['title']} | {x['summary']} | {x['url']}" for x in ctx) or "- خبری پیدا نشد."))
    symbols, urls = {r["symbol"] for r in rows}, {x["url"] for x in ctx}
    w.RULES = REPORT_RULES
    w.RULES_GEMMA = REPORT_RULES.replace(w.style_for("ب"), w.compact_for("ب"))
    provs = w.providers()
    provs.sort(key=lambda p: RANK.index(p[0]) if p[0] in RANK else len(RANK))
    if (now.hour, now.minute) < FALLBACK_FROM and not force:
        provs = [p for p in provs if p[0] == REPORT_MODEL]  # the report's own model; others only after 19:30
    for model, call in provs:
        if model in w.exhausted():
            continue
        t0 = time.time()
        try:
            raw = w.retry(call, prompt)
            try:
                d = check(json.loads(raw[raw.index("{"):raw.rindex("}") + 1]), symbols, urls)
            except ValueError as e:
                if "words" not in str(e) and "links" not in str(e):
                    raise
                raw = w.retry(call, prompt + "\n\nپیش‌نویس قبلی تو:\n" + raw + f"\n\nایراد: {e}. همان گزارش را با text بین ۶۵۰ تا ۷۵۰ کلمه "
                              "در ۶ تا ۹ پاراگراف و با links برای هر معامله‌ی نام‌برده بازنویسی کن و فقط JSON برگردان.")
                d = check(json.loads(raw[raw.index("{"):raw.rindex("}") + 1]), symbols, urls)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            json.dump({"date_fa": date_fa, "date": date, **d, "model": model}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            w.save_scenarios(d["scenarios"], "report", date_fa)
            print(f"report {date_fa} <- {model} ({time.time() - t0:.0f}s, {len(d['links'])} links, {len(d['sources'])} sources)")
            return
        except Exception as e:  # noqa: BLE001 - try the next model
            print(f"  report {model}: {type(e).__name__}: {str(e)[:200]}")
    print("report: no model answered; next round tries again")


if __name__ == "__main__":
    main()
