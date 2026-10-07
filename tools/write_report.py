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
import facts_report  # noqa: E402
from validate import FORBIDDEN, SLUG_BAD, paragraphs, words  # noqa: E402

ROOT = w.ROOT
# optional fixed headline: --headline "..." or the first line of report-now.txt after "headline:"
HEADLINE = ""
FX = [""]  # the facts and tables given to the model (names with Latin letters in them are allowed)
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
    "- همه‌ی حقیقت‌ها را کد حساب کرده (F1، F2، …). هیچ حساب، درصد، مقایسه یا رتبه‌ای خودت نساز؛ عددها را عیناً از حقیقت‌ها بنویس. "
    "لازم نیست همه را بیاوری؛ مهم‌ترین‌ها را انتخاب کن. علت‌ها فقط از «خبرهای رسانه‌ها» با نام رسانه در متن؛ اگر علتی نیست، حدس نزن.\n"
    "- text: ۴۵۰ تا ۶۵۰ کلمه؛ پاراگراف‌ها جدا با یک خط خالی؛ دست‌کم ۲ میان‌تیتر، هر میان‌تیتر یک خط کوتاه جدا (۲ تا ۷ کلمه، بدون نقطه). لینک، آدرس اینترنتی و HTML در متن نگذار.\n"
    "- table: فهرست ۱ تا ۳ شناسه از «جدول‌های پیشنهادی» (مثلاً [\"T2\", \"T4\"]) که با متن جور است؛ همان جدول‌ها کنار گزارش منتشر می‌شوند و می‌توانی در متن به آن‌ها اشاره کنی.\n"
    "- scenarios: ۲ یا ۳ سناریوی قابل‌بررسی به شکل JSON بخش ۲-۱۵.\n"
    "- links: برای هر معامله‌ای که در متن نام بردی {\"title\": عبارت دقیقاً همان‌طور که در متن آمده، \"symbol\": نماد از فکت‌شیت}.\n"
    "- sources: برای هر خبر بیرونی که استفاده کردی {\"title\": نام رسانه همان‌طور که در متن آمده، \"url\": آدرس همان خبر از فهرست}.\n"
    "- slug: فارسی با خط تیره، بدون فاصله و علامت.\n"
    "فقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text, table, scenarios, links, sources و هیچ متن دیگری.")


from facts_report import val  # noqa: E402,F401 - news() ranks queries by trade value


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


def subheads(text):
    """a sub-heading = a paragraph of one short line without final punctuation."""
    return sum(1 for p in re.split(r"\n\s*\n", text) if p.strip() and "\n" not in p.strip() and words(p) <= 8
               and not re.search(r"[.!؟?:،]$", p.strip()))


def check(d, symbols, urls, tables, known):
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
    # a model that slips Latin letters into a Persian word (سولfurیک); names given in the facts (پی وی سی SE-950) are fine
    mixed = [x for x in re.findall(r"\S*(?:[؀-ۿ][A-Za-z]|[A-Za-z][؀-ۿ])\S*", d["text"]) if x.strip("«»()،.؛:") not in FX[0]]
    if mixed:
        raise ValueError("mixed-script word: " + " ".join(mixed[:5]))
    d["slug"] = SLUG_BAD.sub("-", d["slug"].strip())
    # keep only links the site can resolve: the anchor must be in the text, the symbol a trade of today
    d["links"] = [{"title": x["title"], "symbol": x["symbol"]} for x in d.get("links") or []
                  if isinstance(x, dict) and x.get("symbol") in symbols and x.get("title") and x["title"] in d["text"]]
    # and only sources that were really given to the model (no invented addresses)
    d["sources"] = [{"title": x["title"], "url": x["url"]} for x in d.get("sources") or []
                    if isinstance(x, dict) and x.get("url") in urls and x.get("title") and x["title"] in d["text"]]
    if not d["links"]:
        raise ValueError("no trade links")
    d["scenarios"] = [x for x in d.get("scenarios") or [] if isinstance(x, dict)]
    probs = markers.check(d["text"], "" if HEADLINE else d["title"], d["lead"], table=False, full=True)
    if subheads(d["text"]) < 2:
        probs.append("دست‌کم ۲ میان‌تیتر لازم است (هر کدام یک خط کوتاه جدا، بدون نقطه)")
    ids = d.get("table") if isinstance(d.get("table"), list) else [d.get("table")]
    ids = list(dict.fromkeys(str(x).strip().upper() for x in ids if x))
    if not 1 <= len(ids) <= 3 or any(x not in tables for x in ids):
        probs.append(f"table باید فهرست ۱ تا ۳ شناسه از {', '.join(tables)} باشد")
    else:
        d["table"] = [tables[x] for x in ids]
    # every number must come from the facts, the tables or the news given (the model does no arithmetic)
    bad = sorted({n for k in ("title", "subtitle", "lead", "text") for n in w.NUM.findall(d[k]) if n not in known})
    if bad:
        probs.append("عدد بیرون از حقیقت‌ها: " + "، ".join(bad))
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
    # --market kish: the Kish export-market report, same rules and timing (owner 1405-07-15); files under queue|news/kish/
    kish = "--market" in sys.argv and sys.argv[sys.argv.index("--market") + 1] == "kish"
    sub = "kish" if kish else ""
    if kish:
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "fetch_kish.py")], check=False)
        k = json.load(open(os.path.join(ROOT, "data", "kish", "today.json"), encoding="utf-8"))
        latest = {"date": k["date"], "date_fa": k["trade_date"]}
    else:
        latest = json.load(open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8"))
    date, today = latest["date"], now.strftime("%Y-%m-%d")
    # today's report from 18:30; a report still missing after midnight is written then (it never skips to the next day)
    if not force and (date > today or (date == today and (now.hour, now.minute) < (18, 30))):
        return
    out = os.path.join(ROOT, "queue", sub, f"report-{date}.json")
    if os.path.exists(out) or os.path.exists(os.path.join(ROOT, "news", sub, f"report-{date}.json")):
        return
    if kish:
        import facts_report_kish
        h = json.load(open(os.path.join(ROOT, "data", "kish", "history.json"), encoding="utf-8"))
        date_fa, items = k["trade_date"], k["items"]
        if not items:
            return
        prev = max((d for d in h.get("days", {}) if d < date_fa), default=None)
        rows = [{"symbol": r["symbol"], "goods_name": facts_report_kish.clean_name(r.get("goods_name")),
                 "trade_value": r.get("total_value_usd") or 0} for r in items]  # for news() and links only
        ctx = news(rows)
        fx, symmap, tables = facts_report_kish.build(items, h.get("series", {}), h["days"].get(prev) if prev else None, date_fa, date)
        global REPORT_RULES
        REPORT_RULES = REPORT_RULES.replace("گزارش پایان روز بازار فیزیکی بورس کالا", "گزارش پایان روز بازار صادراتی بورس کالا") + \
            "\n\nقواعد بازار صادراتی:\n" + w.STYLE_KISH
    else:
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "fetch.py")], check=False)  # the day's final numbers
        latest = json.load(open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8"))
        date, date_fa = latest["date"], latest["date_fa"]
        rows = json.load(open(os.path.join(ROOT, "data", date, "today.json"), encoding="utf-8"))["rows"]
        if not rows:
            return
        ctx = news(rows)
        fx, symmap, tables = facts_report.build(rows, date_fa, date, os.path.join(ROOT, "data", date, "symbols"))
    ctx_txt = "\n".join(f"- {x['site']} | {x['title']} | {x['summary']} | {x['url']}" for x in ctx) or "- خبری پیدا نشد."
    prompt = (f"تیتر گزارش از پیش تعیین شده: «{HEADLINE}»؛ متن را با همین تیتر هماهنگ بنویس.\n\n" if HEADLINE else "") + (
              "حقیقت‌های کل بازار امروز (فقط از این‌ها انتخاب کن؛ عددها را عیناً بنویس):\n" + fx +
              "\n\nنماد هر معامله (فقط برای links؛ هرگز در متن ننویس): " + symmap +
              "\n\nجدول‌های پیشنهادی (۱ تا ۳ تا را انتخاب کن):\n" + json.dumps(tables, ensure_ascii=False) +
              "\n\nخبرهای امروز در رسانه‌ها (فقط برای علت‌ها؛ هر کدام را استفاده کردی در sources بیاور):\n" + ctx_txt)
    FX[0] = fx + json.dumps(tables, ensure_ascii=False) + symmap
    known = set(w.NUM.findall(fx + json.dumps(tables, ensure_ascii=False) + ctx_txt)) | set("۰۱۲۳۴۵۶۷۸۹0123456789")
    symbols, urls = {r["symbol"] for r in rows}, {x["url"] for x in ctx}
    w.RULES = REPORT_RULES
    w.RULES_GEMMA = REPORT_RULES.replace(w.style_for("ب"), w.compact_for("ب"))
    provs = w.providers()
    provs.sort(key=lambda p: RANK.index(p[0]) if p[0] in RANK else len(RANK))
    if date == today and (now.hour, now.minute) < FALLBACK_FROM and not force:
        provs = [p for p in provs if p[0] == REPORT_MODEL]  # the report's own model; others only after 19:30
    for model, call in provs:
        if model in w.exhausted():
            continue
        t0 = time.time()
        try:
            raw = w.retry(call, prompt)
            try:
                d = check(json.loads(raw[raw.index("{"):raw.rindex("}") + 1]), symbols, urls, tables, known)
            except ValueError as e:
                if not re.search(r"words|links|markers|table", str(e)):
                    raise
                raw = w.retry(call, prompt + "\n\nپیش‌نویس قبلی تو:\n" + raw + f"\n\nایراد: {e}. همان گزارش را با text بین ۶۵۰ تا ۷۵۰ کلمه "
                              "در ۶ تا ۹ پاراگراف و با links برای هر معامله‌ی نام‌برده بازنویسی کن و فقط JSON برگردان.")
                d = check(json.loads(raw[raw.index("{"):raw.rindex("}") + 1]), symbols, urls, tables, known)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            json.dump({"date_fa": date_fa, "date": date, **d, "author": "مصطفی صالح‌آبادی", "author_title": "کارشناس اقتصادی", "model": model}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            w.save_scenarios(d["scenarios"], "report", date_fa)
            print(f"report {date_fa} <- {model} ({time.time() - t0:.0f}s, {len(d['links'])} links, {len(d['sources'])} sources)")
            return
        except Exception as e:  # noqa: BLE001 - try the next model
            print(f"  report {model}: {type(e).__name__}: {str(e)[:200]}")
    print("report: no model answered; next round tries again")


if __name__ == "__main__":
    main()
