"""facts_energy.py - facts for one Iran Energy Exchange trade (owner 1405-07-16), same idea as facts_kish.py:
every number and sentence is computed here; the model only picks and phrases.
build(row, past_rows, date_fa, date) -> (facts_text, {table_id: table})
past_rows: earlier traded rows of the same commodity key (fetch_energy.key), oldest first.
"""
import datetime as dt
import re

from factsheet import fa_date, fa_int, fa_num, YEAR
from facts_kala import WEEKDAYS, pct, signed


def clean(s):
    return re.sub(r"\s+", " ", (s or "").replace("ي", "ی").replace("ك", "ک")).strip()


def kind(row):
    """'rial' | 'usd' | 'premium_usd' | 'premium_rial' | 'premium_pct'."""
    pu = row.get("price_unit") or ""
    if "درصد" in pu:
        return "premium_pct"
    if row.get("is_premium") or "پریمیوم" in pu:
        return "premium_usd" if "دلار" in pu else "premium_rial"
    return "usd" if row.get("is_usd") or "دلار" in pu else "rial"


def unit_fa(row):
    """price unit in words. Domestic ton prices are per kilogram (value = price x tons x 1000), checked on the row itself."""
    vu = row.get("volume_unit") or "تن"
    k = kind(row)
    if k == "premium_pct":
        return "درصد"
    cur = "دلار" if k in ("usd", "premium_usd") else "ریال"
    p, v, val = row.get("weighted_price") or 0, row.get("trade_volume") or 0, row.get("total_value_rial") or 0
    if k == "rial" and vu == "تن" and p and v and val and abs(val / (p * v) - 1000) < 50:
        return f"{cur} در هر کیلوگرم"
    return f"{cur} در هر {vu}"


def market_fa(row):
    return "رینگ بین‌الملل" if "بین" in (row.get("target_market") or "") else "رینگ داخلی"


def side(x, pu):
    """premium in plain words (designer 1405-07-16): -282 -> «۲۸۲ دلار زیر قیمت مرجع جهانی»."""
    if abs(x) < 1e-9:
        return "برابر قیمت مرجع جهانی"
    return f"{fa_num(abs(x), 2 if abs(x) < 10 else 0)} {pu.split(' در ')[0]} {'زیر' if x < 0 else 'بالای'} قیمت مرجع جهانی"


def price_fact(price, base, pu, k):
    if k.startswith("premium") and k != "premium_pct":
        return (f"این معامله با پریمیوم انجام شد: نرخ نهایی {side(price, pu)} است (در هر {pu.split(' در هر ')[-1]}). "
                f"پریمیوم پایه {side(base, pu)} بود. پریمیوم اختلاف با قیمت مرجع است، نه قیمت مطلق؛ قیمت نهایی را عدد مطلق ننویس.")
    if k == "premium_pct":
        return f"پریمیوم معامله {fa_num(price, 2)} درصد نسبت به قیمت مرجع بود و پریمیوم پایه {fa_num(base, 2)} درصد."
    return f"نرخ میانگین معامله {fa_int(price)} {pu} بود و قیمت پایه {fa_int(base)} {pu}."


def build(row, past, date_fa, date):
    YEAR[0] = date_fa[:4]
    goods, prod = clean(row.get("goods_name")), clean(row.get("producer") or row.get("supplier"))
    k, pu, vu = kind(row), unit_fa(row), row.get("volume_unit") or "تن"
    price, base = row.get("weighted_price") or 0, row.get("base_price") or 0
    off, trd = row.get("offer_volume") or 0, row.get("trade_volume") or 0
    wd = WEEKDAYS[dt.date.fromisoformat(date).weekday()]
    mk = market_fa(row)
    F = [f"نام کالا: {goods}؛ عرضه‌کننده: {prod}.",
         f"بازار: {mk} بورس انرژی ایران؛ نوع قرارداد: {row.get('contract_type') or 'نقدی'}.",
         f"روز معامله: {wd} {fa_date(date_fa)}.",
         f"{prod} {fa_num(off)} {vu} {goods} عرضه کرد و {fa_num(trd)} {vu} آن فروش رفت."]
    dem = row.get("demand_volume")  # kala_energy fce103a: «حجم تقاضا» of the IEE stats page
    if dem and off:
        r = dem / off
        F.append(f"خریداران {fa_num(dem)} {vu} تقاضا ثبت کردند" + (f"؛ تقاضا {fa_num(r)} برابر عرضه بود." if r >= 1.5 else
                 f"؛ تقاضا فقط {fa_num(r * 100, 0)} درصد عرضه بود." if r < 1 else "."))
    if trd and off and trd < off - 1e-9:
        F.append(f"{fa_num(trd / off * 100, 0)} درصد عرضه فروش رفت.")
    elif trd and off:
        F.append("همه‌ی عرضه فروش رفت.")
    F.append(price_fact(price, base, pu, k))
    if not k.startswith("premium"):
        c = pct(price, base)
        if c is not None:
            F.append("نرخ با قیمت پایه برابر بود." if abs(c) < 0.05 else
                     f"رقابت نرخ را {signed(c)} بالاتر از قیمت پایه برد." if c > 0 else f"نرخ {signed(c)} پایین‌تر از قیمت پایه بود.")
    if (row.get("trade_count") or 0) > 1:
        F.append(f"این حجم در {fa_int(row['trade_count'])} معامله فروخته شد.")
    if k == "usd" and row.get("total_value_usd"):
        F.append(f"ارزش این معامله {fa_int(round(row['total_value_usd']))} دلار بود.")
    elif row.get("total_value_rial"):
        F.append(f"ارزش ریالی این معامله {fa_num(row['total_value_rial'] / 1e10, 0)} میلیارد تومان بود.")
    # owner 1405-07-15: no share-of-day figures and no same-day comparisons (the day may still change)
    same = [h for h in (past or []) if (h.get("trade_date") or "") < date_fa and kind(h) == k and (h.get("weighted_price") or 0)]
    tables = {}
    if same:
        last = same[-1]
        lp = last["weighted_price"]
        if k.startswith("premium"):
            F.append(f"معامله‌ی قبلی همین کالا از همین عرضه‌کننده {fa_date(last['trade_date'])} با پریمیوم {side(lp, pu) if k != 'premium_pct' else fa_num(lp, 2) + ' درصد'} بود.")
        else:
            cl = pct(price, lp)
            if cl is not None:
                F.append(f"معامله‌ی قبلی همین کالا از همین عرضه‌کننده {fa_date(last['trade_date'])} با نرخ {fa_int(lp)} {pu} بود؛ "
                         + ("نرخ تغییری نکرد." if abs(cl) < 0.05 else f"نرخ این بار {signed(cl)} {'بالاتر' if cl > 0 else 'پایین‌تر'} است."))
        if len(same) >= 4 and not k.startswith("premium"):  # content rule: «بیشترین/کمترین» only with enough history
            prices = [(h["trade_date"], h["weighted_price"]) for h in same]
            hi, lo, first = max(prices, key=lambda x: x[1]), min(prices, key=lambda x: x[1]), prices[0][0]
            if price > hi[1]:
                F.append(f"این بالاترین نرخ این کالا از {fa_date(first)} (آغاز داده‌ها) است؛ بالاترین قبلی {fa_num(hi[1])} در {fa_date(hi[0])} بود.")
            elif price < lo[1]:
                F.append(f"این پایین‌ترین نرخ این کالا از {fa_date(first)} (آغاز داده‌ها) است؛ پایین‌ترین قبلی {fa_num(lo[1])} در {fa_date(lo[0])} بود.")
            F.append(f"این کالا از {fa_date(first)} تا امروز {fa_int(len(same))} بار از همین عرضه‌کننده معامله شده است.")
            tables["T1"] = {"title": f"نرخ {goods} در معامله‌های اخیر ({pu})", "columns": ["تاریخ", "نرخ"],
                            "rows": [[fa_date(d), fa_num(p, 2 if abs(p) < 10 else 0)] for d, p in prices[-5:]] + [[fa_date(date_fa), fa_num(price, 2 if abs(price) < 10 else 0)]]}
    tables["T2"] = {"title": f"معامله‌ی {goods} {prod}", "columns": ["شاخص", "مقدار"],
                    "rows": [[f"عرضه ({vu})", fa_num(off)], [f"فروش ({vu})", fa_num(trd)],
                             [f"قیمت پایه ({pu})", fa_num(base, 2 if abs(base) < 10 else 0)],
                             [f"نرخ معامله ({pu})", fa_num(price, 2 if abs(price) < 10 else 0)], ["بازار", mk]]}
    text = "\n".join(f"{i}. {f}" for i, f in enumerate(F, 1))
    return text, tables
