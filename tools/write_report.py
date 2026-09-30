"""write_report.py - the daily market report, written at 18:30 Tehran by the best model that answers.

Runs every release round (~5 min); does nothing until 18:30 Tehran on a day whose data is today's and whose
report is not written yet. Then it refreshes the data, builds a whole-market fact sheet and asks the models in the
owner's rating order; the first valid answer goes to queue/report-<date>.json, which release.py publishes at once.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import write_free as w  # noqa: E402
from factsheet import YEAR, fa_date, fa_int, fa_num, toman_billion  # noqa: E402
from validate import FORBIDDEN, SLUG_BAD, paragraphs, words  # noqa: E402

ROOT = w.ROOT
MIXED = re.compile(r"[؀-ۿ][A-Za-z]|[A-Za-z][؀-ۿ]")
TEHRAN = timezone(timedelta(hours=3, minutes=30))
# the owner's blind rating (2026-09-30/10-01), best first; models not listed come after them
RANK = ["llm7:DeepSeek-V4-Flash-0731", "cohere:command-a-03-2025", "gemini:gemma-4-31b-it",
        "kilo:dots-studio/dots-3-note-preview:free", "hf:deepseek-ai/DeepSeek-V3.1", "kilo:stepfun/step-3.7-flash:free",
        "zai:glm-4.5-flash", "kilo:nvidia/nemotron-3-ultra-550b-a55b:free", "cf:@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "cf:@cf/qwen/qwen2.5-coder-32b-instruct", "llm7:mistral-Nemo-Instruct-2407"]
REPORT_RULES = (
    "تو دبیر «گروه بورس کالای ام‌اس‌دیتا» هستی و گزارش پایانی روز بازار فیزیکی بورس کالا را به فارسی می‌نویسی. "
    "فقط عددهای فکت‌شیت را به کار ببر؛ علت، خبر بیرونی، پیش‌بینی و توصیه‌ی خرید و فروش ممنوع است. لحن خبری و بی‌طرف، جمله‌های کوتاه و روان، بی‌تکرار.\n"
    "- title: تیتر با مهم‌ترین فکت کل بازار امروز (ارزش کل یا رکورددار روز).\n"
    "- subtitle: یک جمله‌ی مکمل تیتر با فکتی دیگر.\n"
    "- lead: یک جمله خلاصه‌ی کل روز.\n"
    "- text: ۳۰۰ تا ۵۰۰ کلمه در ۴ تا ۶ پاراگراف (جدا با یک خط خالی). بند اول دقیقاً با «به گزارش گروه بورس کالای ام‌اس‌دیتا،» شروع شود و تصویر کل بازار را بدهد؛ "
    "بعد تالارها، بزرگ‌ترین معامله‌ها، رقابت و تقاضا، و تغییر نرخ‌ها؛ پایان: جمع‌بندی یک‌جمله‌ای، نه پرسش.\n"
    "روایت بنویس، نه فهرست: از هر بخش فکت‌شیت فقط ۲ یا ۳ مورد خبری‌تر را بیاور و با هم مقایسه کن. داوری کلی مثل «روند مثبت» یا «بازار داغ» ننویس.\n"
    "- slug: فارسی با خط تیره، بدون فاصله و علامت.\n"
    "فقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text و هیچ متن دیگری.")


def facts(rows, date_fa):
    YEAR[0] = date_fa[:4]
    val = lambda r: r.get("trade_value") or 0  # noqa: E731
    total = sum(val(r) for r in rows)
    comp = lambda r: (r["weighted_price"] / r["weighted_base_price"] - 1) * 100 if r.get("weighted_price") and r.get("weighted_base_price") else 0  # noqa: E731
    name = lambda r: f"{r.get('goods_name')} ({r.get('producer_name')})"  # noqa: E731
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
    L.append("بزرگ‌ترین معامله‌ها: " + "؛ ".join(f"{name(r)}: {toman_billion(val(r))}" for r in sorted(rows, key=val, reverse=True)[:5]))
    hot = [r for r in sorted(rows, key=comp, reverse=True) if comp(r) > 0][:5]
    if hot:
        L.append("بیشترین رقابت (نرخ معامله بالاتر از قیمت پایه): " + "؛ ".join(f"{name(r)}: {fa_num(comp(r))} درصد" for r in hot))
    at_base = sum(1 for r in rows if abs(comp(r)) < 0.05)
    more_demand = sum(1 for r in rows if (r.get("demand_qty") or 0) > (r.get("offered_qty") or 0))
    L.append(f"{fa_int(at_base)} نماد روی قیمت پایه معامله شد؛ در {fa_int(more_demand)} نماد سفارش خریداران از عرضه بیشتر بود.")
    ch = [r for r in rows if r.get("price_change_pct") is not None]
    up = sorted(ch, key=lambda r: r["price_change_pct"], reverse=True)[:3]
    down = sorted(ch, key=lambda r: r["price_change_pct"])[:3]
    if up:
        L.append("بیشترین افزایش نرخ نسبت به معامله‌ی قبلی همان نماد: " + "؛ ".join(f"{name(r)}: {fa_num(r['price_change_pct'])} درصد" for r in up if r["price_change_pct"] > 0))
    if down:
        L.append("بیشترین کاهش نرخ نسبت به معامله‌ی قبلی همان نماد: " + "؛ ".join(f"{name(r)}: {fa_num(abs(r['price_change_pct']))} درصد" for r in down if r["price_change_pct"] < 0))
    return "\n".join(L)


def check(d):
    for k in ("title", "slug", "subtitle", "lead", "text"):
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise ValueError(f"empty {k}")
    d["text"] = d["text"].replace("\r", "").strip()
    n, p = words(d["text"]), paragraphs(d["text"])
    if not 250 <= n <= 600 or not 3 <= p <= 7:
        raise ValueError(f"{n} words / {p} paragraphs")
    if not d["text"].startswith("به گزارش گروه بورس کالای ام‌اس‌دیتا"):
        raise ValueError("bad opening")
    if any(FORBIDDEN.search(d[k]) for k in ("title", "subtitle", "lead", "text")):
        raise ValueError("forbidden content")
    if MIXED.search(d["text"]):  # a model that slips Latin letters into a Persian word (سولfurیک)
        raise ValueError("mixed-script word")
    d["slug"] = SLUG_BAD.sub("-", d["slug"].strip())
    return d


def main():
    now = datetime.now(TEHRAN)
    if (now.hour, now.minute) < (18, 30) and "--now" not in sys.argv:
        return
    latest = json.load(open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8"))
    date = latest["date"]
    if date != now.strftime("%Y-%m-%d") and "--now" not in sys.argv:
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
    prompt = "فکت‌شیت کل بازار امروز (فقط همین عددها را به کار ببر):\n" + facts(rows, date_fa)
    w.RULES = REPORT_RULES
    provs = w.providers()
    provs.sort(key=lambda p: RANK.index(p[0]) if p[0] in RANK else len(RANK))
    for name, call in provs:
        if name in w.exhausted():
            continue
        t0 = time.time()
        try:
            raw = w.retry(call, prompt)
            try:
                d = check(json.loads(raw[raw.index("{"):raw.rindex("}") + 1]))
            except ValueError as e:
                if "words" not in str(e):
                    raise
                d = w.retry(call, prompt + "\n\nپیش‌نویس قبلی تو:\n" + raw + f"\n\nایراد: متن text {e}. همان گزارش را با text بین ۳۰۰ تا ۵۰۰ کلمه در ۴ تا ۶ پاراگراف بازنویسی کن و فقط JSON برگردان.")
                d = check(json.loads(d[d.index("{"):d.rindex("}") + 1]))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            json.dump({"date_fa": date_fa, "date": date, **d, "model": name}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"report {date_fa} <- {name} ({time.time() - t0:.0f}s)")
            return
        except Exception as e:  # noqa: BLE001 - try the next model
            print(f"  report {name}: {type(e).__name__}: {str(e)[:200]}")
    print("report: no model answered; next round tries again")


if __name__ == "__main__":
    main()
