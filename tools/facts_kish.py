"""facts_kish.py - facts for the Kish export-market symbol news (owner 1405-07-15), same idea as facts_kala.py:
every number and sentence is computed here; the model only picks and phrases.
build(row, series, day_rows, date_fa, date) -> (facts_text, {table_id: table})
row/series items: /api/kish/raw-data.json and /api/kish-history/raw-data.json (official IME export stats).
"""
import datetime as dt
import re

from factsheet import FA, fa_date, fa_int, fa_num, YEAR
from facts_kala import WEEKDAYS, pct, signed

# delivery border -> first destination country (content agent 1405-07-15). A border is the first stop only.
BORDERS = {"نوردوز": "ارمنستان", "دوغارون": "افغانستان", "میلک": "افغانستان", "میل78": "افغانستان", "میل ۷۸": "افغانستان",
           "خواف": "افغانستان", "شلمچه": "عراق", "حاج عمران": "عراق", "خسروی": "عراق", "مهران": "عراق", "سومار": "عراق",
           "پرویزخان": "عراق", "رازی": "ترکیه", "سرو": "ترکیه", "میرجاوه": "پاکستان", "پیشین": "پاکستان", "کوهک": "پاکستان",
           "سرخس": "ترکمنستان"}
UNKNOWN = []  # borders not in BORDERS, logged by the writer


def delivery(place):
    """'FCA-نوردوز' -> ('تحویل در مرز نوردوز', 'ارمنستان'); ports and factory have no country."""
    p = (place or "").strip()
    rest = re.sub(r"^(FCA|FOB|CPT|DAP|EXW|Exwork)\s*-?\s*", "", p, flags=re.I).strip()
    if re.match(r"(?i)^(exw|exwork)", p) or "انبار کارخانه" in rest or not rest:
        return "تحویل درِ کارخانه", None
    if re.match(r"(?i)^fob", p):
        port = re.sub(r"^بندر\s*", "", rest)
        return f"تحویل روی کشتی در بندر {port}", None
    name = rest.replace("میل78", "میل ۷۸")
    for b, c in BORDERS.items():
        if b in rest:
            return f"تحویل در مرز {name}، مرز {c}", c
    UNKNOWN.append(p)
    return f"تحویل در {name}", None


def unit_fa(row):
    """'دلار / 1000 کیلوگرم' -> 'دلار در هر تن'."""
    cur = "ریال صادراتی" if not row.get("is_usd") else "دلار"
    u = row.get("unit") or ""
    return f"{cur} در هر تن" if "1000" in u or "تن" in u else f"{cur} در هر {u}"


def clean_name(n):
    return re.sub(r"\s*-\s*صادراتی\s*$", "", n or "").strip()


def build(row, series, day_rows, date_fa, date):
    YEAR[0] = date_fa[:4]
    goods, prod = clean_name(row.get("goods_name")), row.get("producer") or ""
    usd = bool(row.get("is_usd"))
    pu = unit_fa(row)
    price, base = row.get("weighted_price") or 0, row.get("base_price") or 0
    off, dem, trd = row.get("offer_volume") or 0, row.get("demand_volume") or 0, row.get("trade_volume") or 0
    wd = WEEKDAYS[dt.date.fromisoformat(date).weekday()]
    deliv, country = delivery(row.get("delivery_place"))
    F = [f"بازار صادراتی بورس کالا (عرضه برای خریدار خارجی یا صادرکننده). نام کالا: {goods}؛ تولیدکننده: شرکت {prod}.",
         f"روز معامله: {wd} {fa_date(date_fa)}.",
         f"شرط تحویل: {deliv}." + (" مرز فقط مقصد اول است؛ از مصرف کالا در آن کشور حرفی نزن." if country else ""),
         ("کشور مقصد برای تیتر: " + country + " (در تیتر «به " + country + "» بیاور).") if country else
         "کشور مقصد مشخص نیست؛ در تیتر کشوری نیاور.",
         ("نرخ‌های این معامله دلاری است." if usd else "این قرارداد «ریال صادراتی» است؛ صریح بنویس «با نرخ ریال صادراتی» و آن را با معامله‌های دلاری مقایسه یا جمع نکن.")]
    # volumes in the source are in kg-units of the contract unit (1000 kg) -> tons
    # volumes are in contract units ("1000 کیلوگرم" in >99% of rows; a few 840/880 kg) -> tons
    m = re.search(r"(\d+)\s*کیلوگرم", row.get("unit") or "")
    kg = int(m.group(1)) if m else 1000
    vol = lambda v: fa_num(v * kg / 1000)
    F.append(f"شرکت {prod} {vol(off)} تن {goods} عرضه کرد و خریداران {vol(dem)} تن خواستند؛ {vol(trd)} تن فروش رفت.")
    if off:
        r = dem / off
        if r >= 1.5:
            F.append(f"تقاضا {fa_num(r)} برابر عرضه بود.")
        elif r < 1:
            F.append(f"تقاضا فقط {fa_num(r * 100, 0)} درصد عرضه بود.")
    if trd and off and trd < off - 1e-9:
        F.append(f"فقط {fa_num(trd / off * 100, 0)} درصد عرضه فروش رفت.")
    F.append(f"نرخ میانگین معامله {fa_int(price)} {pu} بود و قیمت پایه {fa_int(base)} {pu}.")
    c = pct(price, base)
    if c is not None:
        F.append("نرخ با قیمت پایه برابر بود." if abs(c) < 0.05 else
                 f"رقابت نرخ را {signed(c)} بالاتر از قیمت پایه برد." if c > 0 else f"نرخ {signed(c)} پایین‌تر از قیمت پایه بود.")
    tv = row.get("total_value") or 0
    if usd:
        F.append(f"ارزش این معامله {fa_int(round(tv))} دلار بود.")
        tot = sum(x.get("total_value_usd") or 0 for x in day_rows if x.get("is_usd"))
        if tot:
            F.append(f"سهم این معامله از کل فروش دلاری بازار صادراتی در این روز {fa_num(tv / tot * 100, 1)} درصد بود "
                     f"(کل روز: {fa_int(round(tot))} دلار).")
    else:
        F.append(f"ارزش این معامله {fa_int(round(tv))} ریال صادراتی بود.")
    # same goods, other producer or other delivery the same day (same currency only)
    for x in day_rows:
        if x is row or bool(x.get("is_usd")) != usd or clean_name(x.get("goods_name")) != goods or not x.get("trade_volume"):
            continue
        d2, _ = delivery(x.get("delivery_place"))
        who = f"شرکت {x.get('producer')}" if x.get("producer") != prod else "همین شرکت"
        F.append(f"در همین روز {who} همین کالا را با {d2} به نرخ {fa_int(x.get('weighted_price') or 0)} {pu} فروخت.")
        break
    # history: only when the symbol has at least 5 past trades (content agent rule)
    past = [h for h in (series or []) if (h.get("trade_date") or "") < date_fa and (h.get("trade_volume") or 0) > 0
            and bool(h.get("is_usd")) == usd]
    tables = {}
    if len(past) >= 5:
        prices = [(h["trade_date"], h.get("weighted_price") or 0) for h in past]
        last_d, last_p = prices[-1]
        cl = pct(price, last_p)
        if cl is not None:
            F.append(f"معامله‌ی قبلی همین نماد {fa_date(last_d)} با نرخ {fa_int(last_p)} {pu} بود؛ "
                     + ("نرخ تغییری نکرد." if abs(cl) < 0.05 else f"نرخ این بار {signed(cl)} {'بالاتر' if cl > 0 else 'پایین‌تر'} است."))
        hi = max(prices, key=lambda x: x[1])
        lo = min(prices, key=lambda x: x[1])
        first = prices[0][0]
        if price > hi[1]:
            F.append(f"این بیشترین نرخ این نماد از {fa_date(first)} (آغاز داده‌ها) است؛ بیشترین قبلی {fa_int(hi[1])} در {fa_date(hi[0])} بود.")
        elif price < lo[1]:
            F.append(f"این کمترین نرخ این نماد از {fa_date(first)} (آغاز داده‌ها) است؛ کمترین قبلی {fa_int(lo[1])} در {fa_date(lo[0])} بود.")
        F.append(f"این نماد از {fa_date(first)} تا امروز {fa_int(len(past))} بار معامله شده است.")
        rows = [[fa_date(d), fa_int(p)] for d, p in prices[-5:]] + [[fa_date(date_fa), fa_int(price)]]
        tables["T1"] = {"title": f"نرخ {goods} در شش معامله‌ی اخیر ({pu})", "columns": ["تاریخ", "نرخ"], "rows": rows}
    tables["T2"] = {"title": f"معامله‌ی {goods} شرکت {prod}", "columns": ["شاخص", "مقدار"],
                    "rows": [["عرضه (تن)", vol(off)], ["تقاضا (تن)", vol(dem)], ["فروش (تن)", vol(trd)],
                             [f"قیمت پایه ({pu})", fa_int(base)], [f"نرخ معامله ({pu})", fa_int(price)], ["شرط تحویل", deliv]]}
    text = "\n".join(f"{i}. {f}" for i, f in enumerate(F, 1))
    return text, tables
