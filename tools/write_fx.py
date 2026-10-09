"""write_fx.py - daily «ارز و طلا» news (owner 1405-07-18): one news every day at 20:00 Tehran from the data of the
/dollar/ and /fx/ pages - free market (bonbast) and ice.ir havaleh of 7 currencies, USDT (wallex), emami coin and 18k gold,
each against the previous day. Free models, writing framework v2 (tools/style.md, full mode), 200-300 words in 3-4 paragraphs.
Output: news/fx/<YYYY-MM-DD>.json (published at once; edits only through edits/fx/). One news per day: an existing file is kept.
usage: python tools/write_fx.py [--dry]
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
from write_report import RANK  # noqa: E402
from validate import FORBIDDEN, SLUG_BAD, paragraphs, words  # noqa: E402

ROOT = w.ROOT
API = "https://msdata.ir/api/prices/"
TEHRAN = timezone(timedelta(hours=3, minutes=30))
OUT = os.path.join(ROOT, "news", "fx")
TAG = "ارز و طلا"
CUR = {"USD": "دلار آمریکا", "EUR": "یورو", "AED": "درهم امارات", "CNY": "یوان چین", "RUB": "روبل روسیه", "INR": "روپیه‌ی هند",
       "JPY": "ین ژاپن"}
SRC_FREE = "بازار آزاد تهران (گردآوری ام‌اس‌دیتا)"
SRC_ICE = "مرکز مبادله‌ی ارز و طلای ایران (حواله‌ی توافقی)"
SRC_USDT = "صرافی والکس"


def get(name):
    req = urllib.request.Request(API + name, headers={"User-Agent": "msdata-news/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8-sig"))


WEEK = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]


def j2g(jy, jm, jd):  # Jalali -> Gregorian (same algorithm as the designer's dataset_ld.js)
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd + ((jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186)
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    sal = [0, 31, 29 if (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal[gm]:
        gd -= sal[gm]
        gm += 1
    return gy, gm, gd


def weekday(jd):  # 1405/07/17 -> جمعه (the first sample wrote «پنجشنبه» by itself)
    from datetime import date
    y, m, d = (int(x) for x in re.findall(r"\d+", jd)[:3])
    return WEEK[date(*j2g(y, m, d)).weekday()]


def pct(a, b):
    return round((a - b) / b * 100, 2) if a and b else None


def facts():
    """-> (trade date of the free market, facts dict); every number carries its source."""
    raw, hd = get("raw-data.json"), get("history-daily.json")
    lt = raw["latest"]
    day = lt["trade_date"]
    prev = [r for r in hd["rows"] if r["date"] < day]
    p = prev[-1] if prev else {}
    cur, pc = lt["prices"]["currency"], p.get("currency_close") or {}
    free = {}
    for k, name in CUR.items():
        s = (cur.get(k) or {}).get("sell")
        if s:
            free[name] = {"قیمت فروش امروز (تومان)": s, "قیمت روز قبل (تومان)": pc.get(k), "تغییر (درصد)": pct(s, pc.get(k))}
    coin, gold = lt["prices"].get("coin") or {}, lt["prices"].get("gold") or {}
    cc, gc = p.get("coin_close") or {}, p.get("gold_close") or {}
    metal = {}
    if (coin.get("emami1") or {}).get("sell"):
        s = coin["emami1"]["sell"]
        metal["سکه امامی (تومان)"] = {"امروز": s, "روز قبل": cc.get("emami1"), "تغییر (درصد)": pct(s, cc.get("emami1"))}
    if gold.get("gol18"):
        metal["طلای ۱۸ عیار هر گرم (تومان)"] = {"امروز": gold["gol18"], "روز قبل": gc.get("gol18"), "تغییر (درصد)": pct(gold["gol18"], gc.get("gol18"))}
    if gold.get("ounce"):
        metal["انس جهانی طلا (دلار)"] = {"امروز": gold["ounce"], "روز قبل": gc.get("ounce"), "تغییر (درصد)": pct(gold["ounce"], gc.get("ounce"))}
    ice = {}
    try:
        for k, it in (get("ice_havaleh.json").get("items") or {}).items():
            if k in CUR and it.get("sell_price"):
                q = it.get("unit_qty") or 1
                ice[CUR[k]] = {"قیمت فروش حواله (تومان" + (f"، هر {q} واحد" if q != 1 else "") + ")": round(it["sell_price"] / 10),
                               "روز قبل (تومان)": round(it["prev_sell"] / 10) if it.get("prev_sell") else None,
                               "تغییر (درصد)": it.get("change_pct"), "تاریخ (میلادی)": it.get("date")}
    except Exception as e:  # noqa: BLE001 - the free market alone is still news
        print(f"ice_havaleh: {e}")
    usdt = {}
    try:
        h = get("usdt_history.json")["history"]
        if len(h) >= 2:
            usdt = {"تاریخ": h[-1][0], "قیمت فروش (تومان)": h[-1][2], "روز قبل (تومان)": h[-2][2], "تغییر (درصد)": pct(h[-1][2], h[-2][2])}
    except Exception as e:  # noqa: BLE001
        print(f"usdt_history: {e}")
    f = {"تاریخ داده‌ی بازار آزاد": day, "روز هفته": weekday(day), "تاریخ روز قبل": p.get("date"),
         "بازار آزاد ارز": {"منبع": SRC_FREE, "ارزها": free},
         "سکه و طلا": {"منبع": SRC_FREE, "قیمت‌ها": metal}}
    if ice:
        f["حواله‌ی توافقی"] = {"منبع": SRC_ICE, "ارزها": ice}
        fu, iu = (free.get(CUR["USD"]) or {}).get("قیمت فروش امروز (تومان)"), (ice.get(CUR["USD"]) or {}).get("قیمت فروش حواله (تومان)")
        if fu and iu:  # content agent 1405-07-19: the free-minus-havaleh gap of the dollar is the axis of the news
            f["فاصله‌ی دلار آزاد و حواله"] = {"آزاد منهای حواله (تومان)": fu - iu, "درصد بالاتر بودن آزاد از حواله": round((fu - iu) / iu * 100, 1)}
    if usdt:
        f["تتر"] = {"منبع": SRC_USDT, **usdt}
    return day, f


BANNED = ("نرخ", "جدول زیر", "ردیف‌های جدول", "ردیف های جدول", "گران شد", "ارزان شد", "بالا کشید", "پرید", "ریخت")  # content agent 1405-07-19

RULES = ("تو خبرنگار بازار ارز و طلای msdata.ir هستی و هر روز یک خبر فارسی درباره‌ی قیمت ارز، سکه و طلا می‌نویسی. "
         "چارچوب نگارش زیر را مو به مو رعایت کن، در «حالت کامل» (بخش ۲-۱۴)، با «قالب ه» (خبر داده‌ی شش‌بندی):\n\n")


def rules():
    return (RULES + w.style_for("ه") + "\n\n"
            "- text: ۲۰۰ تا ۳۰۰ کلمه در ۳ یا ۴ پاراگراف، جدا با یک خط خالی. عددها فقط از «داده». هر عدد را با منبعش بیاور "
            "(«در بازار آزاد»، «در مرکز مبادله»، «در صرافی والکس»). همیشه «قیمت» بنویس، نه «نرخ». لینک، آدرس اینترنتی و HTML ننویس.\n"
            "- table: ۳ تا ۸ ردیف از داده، هر ردیف یک شیء با نام ستون‌ها.\n"
            "- slug: فارسی با خط تیره، بدون فاصله و علامت.\n"
            "فقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text, table و هیچ متن دیگری.")


def check(d):
    for k in ("title", "slug", "subtitle", "lead", "text"):
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise ValueError(f"empty {k}")
    d["text"] = d["text"].replace("\r", "").strip()
    n, p = words(d["text"]), paragraphs(d["text"])
    if not 200 <= n <= 320 or not 3 <= p <= 4:
        raise ValueError(f"{n} words / {p} paragraphs (need 200-300 words, 3-4 paragraphs)")
    if any(FORBIDDEN.search(d[k]) for k in ("title", "subtitle", "lead", "text")):
        raise ValueError("forbidden content")
    all_text = " ".join(d[k] for k in ("title", "subtitle", "lead", "text"))
    bad = [w_ for w_ in BANNED if w_ in all_text]
    if "؟" in d["text"] or "?" in d["text"]:
        bad.append("جمله‌ی پرسشی")
    if bad:
        raise ValueError("markers: این‌ها ممنوع است: " + "، ".join(bad))
    d["table"] = [x for x in d.get("table") or [] if isinstance(x, dict)][:8]
    probs = markers.check(d["text"], d["title"], d["lead"], table=bool(d["table"]), full=True)
    if len(d["table"]) < 3:
        probs.append("table باید ۳ تا ۸ ردیف داشته باشد")
    if probs:
        raise ValueError("markers: " + " | ".join(probs))
    d["slug"] = SLUG_BAD.sub("-", d["slug"].strip())
    return d


def write(day, f):
    now = datetime.now(TEHRAN)
    prompt = (f"موضوع: قیمت ارز، سکه و طلا در {day}\n"
              "داده (فقط همین عددها را به کار ببر؛ تغییرها نسبت به روز قبل است):\n" + json.dumps(f, ensure_ascii=False, indent=1))
    w.RULES = rules()
    w.RULES_GEMMA = w.RULES.replace(w.style_for("ه"), w.compact_for("ه"))
    provs = w.providers()
    provs.sort(key=lambda p: RANK.index(p[0]) if p[0] in RANK else len(RANK))
    for model, call in provs:
        if model in w.exhausted():
            continue
        try:
            raw = w.retry(call, prompt)
            for attempt in range(3):  # style.md section 6: hits go back to the model, up to 2 rewrites
                try:
                    d = check(json.loads(raw[raw.index("{"):raw.rindex("}") + 1]))
                    break
                except ValueError as e:
                    if attempt == 2 or not re.search(r"words|markers|table", str(e)):
                        raise
                    raw = w.retry(call, prompt + "\n\nپیش‌نویس قبلی تو:\n" + raw + f"\n\nایرادها: {e}. همان خبر را با رفع همه‌ی این ایرادها "
                                  "بازنویسی کن و فقط JSON برگردان.")
            return {"date": now.strftime("%Y-%m-%d"), "date_fa": day, "series": "fx-daily", "key": day, "tag": TAG, "kind": "news",
                    **d, "sources": [SRC_FREE, SRC_ICE, SRC_USDT], "author": "مصطفی صالح‌آبادی", "author_title": "کارشناس اقتصادی",
                    "model": model, "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        except Exception as e:  # noqa: BLE001 - next model
            print(f"  fx {model}: {type(e).__name__}: {str(e)[:1500]}")
            if isinstance(e, w.QuotaError):
                open(w.EXHAUSTED_FILE, "a", encoding="utf-8").write(model + "\n")
    return None


def main():
    day, f = facts()
    path = os.path.join(OUT, datetime.now(TEHRAN).strftime("%Y-%m-%d") + ".json")  # one news per day (Tehran date)
    print(f"fx {day}: {len(f['بازار آزاد ارز']['ارزها'])} free-market currencies, ice={'حواله‌ی توافقی' in f}, usdt={'تتر' in f}")
    if "--dry" in sys.argv:
        print(json.dumps(f, ensure_ascii=False, indent=1))
        return
    if os.path.exists(path):
        print(f"{path}: already written")
        return
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    item = write(day, f)
    if not item:
        print("fx: no model answered; next run tries again")
        sys.exit(1)
    json.dump(item, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"fx {day} <- {item['model']} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
