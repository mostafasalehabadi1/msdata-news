"""facts_report_kish.py - whole-market facts and candidate tables of the Kish export-market daily report.
Same shape and rules as facts_report.py (physical market): numbered ready sentences + ready tables, all computed here.
Dollar rows only in the totals and comparisons; «ریال صادراتی» rows get their own sentence (never summed with dollars).
build(items, series, prev_day, date_fa, date) -> (facts_text, symbol_map_text, {id: table})
"""
import datetime as dt

from facts_kala import WEEKDAYS
from facts_kish import clean_name, delivery
from factsheet import FA, YEAR, fa_date, fa_int, fa_num


def usd(v):
    v = v or 0
    if v >= 1e6:
        return f"{fa_num(v / 1e6, 2)} میلیون دلار"
    if v >= 1e3:
        return f"{fa_num(v / 1e3, 0)} هزار دلار"
    return f"{fa_int(round(v))} دلار"


def val(r):
    return r.get("total_value_usd") or 0


def comp(r):
    return (r["weighted_price"] / r["base_price"] - 1) * 100 if r.get("weighted_price") and r.get("base_price") else 0


def name(r):
    return f"{clean_name(r.get('goods_name'))} شرکت {r.get('producer')}"


def sgn(x):
    return ("+" if x > 0 else "") + fa_num(x) + "٪"


def country(r):
    return delivery(r.get("delivery_place"))[1] or "بدون مرز مشخص (بندر یا درِ کارخانه)"


def last_change(r, series, date_fa):
    past = [h for h in series.get(r["symbol"], []) if (h.get("trade_date") or "") < date_fa and (h.get("trade_volume") or 0) > 0
            and h.get("is_usd")]
    if not past or not past[-1].get("weighted_price"):
        return None, past
    return (r["weighted_price"] / past[-1]["weighted_price"] - 1) * 100, past


def build(items, series, prev_day, date_fa, date):
    YEAR[0] = date_fa[:4]
    rows = [r for r in items if r.get("is_usd")]
    rial = [r for r in items if not r.get("is_usd")]
    traded = [r for r in rows if (r.get("trade_volume") or 0) > 0]
    total = sum(val(r) for r in rows)
    F = [f"روز: {WEEKDAYS[dt.date.fromisoformat(date).weekday()]} {fa_date(date_fa)}.",
         f"در بازار صادراتی بورس کالا {fa_int(len(items))} کالا از {fa_int(len({r.get('producer') for r in items}))} عرضه‌کننده روی تابلو رفت "
         f"({fa_int(len(rows))} دلاری و {fa_int(len(rial))} ریال صادراتی).",
         f"ارزش کل معامله‌های دلاری {usd(total)} بود."]
    if prev_day and prev_day.get("total_value_usd"):
        p = prev_day["total_value_usd"]
        F.append(f"روز معاملاتی قبل ارزش معامله‌های دلاری {usd(p)} بود؛ یعنی " +
                 ("تقریباً همان." if abs(total / p - 1) < 0.005 else f"{fa_num(abs(total / p - 1) * 100)} درصد {'بیشتر' if total > p else 'کمتر'}."))
    nt = [r for r in items if not (r.get("trade_volume") or 0)]
    if nt:
        F.append(f"{fa_int(len(nt))} کالا بی‌خریدار ماند: " + "، ".join(name(r) for r in nt[:5]) + ".")
    part = [r for r in traded if r["trade_volume"] < (r.get("offer_volume") or 0) - 1e-9]
    if part:
        F.append(f"در {fa_int(len(part))} کالا فقط بخشی از عرضه فروش رفت.")
    full = [r for r in traded if r["trade_volume"] >= (r.get("offer_volume") or 0) - 1e-9]
    F.append(f"در {fa_int(len(full))} کالای دلاری تمام عرضه فروش رفت.")
    at_base = sum(1 for r in traded if abs(comp(r)) < 0.05)
    hot = sorted([r for r in traded if comp(r) > 0.05], key=comp, reverse=True)
    F.append(f"{fa_int(at_base)} کالا روی قیمت پایه و {fa_int(len(hot))} کالا با رقابت بالاتر از قیمت پایه معامله شد." if hot else
             f"هیچ کالای دلاری با رقابت بالاتر از قیمت پایه معامله نشد؛ {fa_int(at_base)} کالا روی قیمت پایه فروخته شد.")
    more = [r for r in rows if (r.get("demand_volume") or 0) > (r.get("offer_volume") or 0) > 0]
    if more:
        top = max(more, key=lambda r: r["demand_volume"] / r["offer_volume"])
        F.append(f"در {fa_int(len(more))} کالا تقاضا از عرضه بیشتر بود؛ بیشترین نسبت مال {name(top)} بود: "
                 f"تقاضا {fa_num(top['demand_volume'] / top['offer_volume'])} برابر عرضه.")
    dest = {}
    for r in traded:
        dest.setdefault(country(r), []).append(r)
    dest_rows = sorted(dest.items(), key=lambda x: -sum(val(r) for r in x[1]))
    for c, rs in dest_rows:
        v = sum(val(r) for r in rs)
        if v and total:
            F.append(f"مقصد {c}: {fa_int(len(rs))} کالا و ارزش {usd(v)} ({fa_num(v / total * 100)} درصد فروش دلاری).")
    big = sorted(traded, key=val, reverse=True)
    for r in big[:5]:
        F.append(f"معامله‌ی بزرگ: {name(r)} با ارزش {usd(val(r))}، {delivery(r.get('delivery_place'))[0]}؛ " +
                 ("روی قیمت پایه." if abs(comp(r)) < 0.05 else f"{fa_num(comp(r))} درصد بالاتر از قیمت پایه."))
    for r in hot[:4]:
        F.append(f"رقابت: نرخ {name(r)} {fa_num(comp(r))} درصد بالاتر از قیمت پایه بسته شد.")
    ch = []
    hi, lo = [], []
    for r in traded:
        c, past = last_change(r, series, date_fa)
        if c is not None and abs(c) >= 0.05:
            ch.append((r, c))
        prices = [h["weighted_price"] for h in past if h.get("weighted_price")]
        if len(prices) >= 5:
            if r["weighted_price"] > max(prices):
                hi.append(r)
            elif r["weighted_price"] < min(prices):
                lo.append(r)
    up = sorted([x for x in ch if x[1] > 0], key=lambda x: -x[1])
    down = sorted([x for x in ch if x[1] < 0], key=lambda x: x[1])
    F.append(f"نسبت به آخرین معامله‌ی همان کالا، {fa_int(len(up))} کالا گران‌تر و {fa_int(len(down))} کالا ارزان‌تر فروخته شد.")
    for r, c in up[:3]:
        F.append(f"افزایش نرخ: {name(r)} {fa_num(c)} درصد گران‌تر از آخرین معامله‌ی خود.")
    for r, c in down[:3]:
        F.append(f"کاهش نرخ: {name(r)} {fa_num(abs(c))} درصد ارزان‌تر از آخرین معامله‌ی خود.")
    if hi:
        F.append(f"{fa_int(len(hi))} کالا به بیشترین نرخ خود از مهر ۱۴۰۴ (آغاز داده‌ها) رسید: " + "، ".join(name(r) for r in hi[:5]) + ".")
    if lo:
        F.append(f"{fa_int(len(lo))} کالا کمترین نرخ خود از مهر ۱۴۰۴ (آغاز داده‌ها) را ثبت کرد: " + "، ".join(name(r) for r in lo[:5]) + ".")
    goods = {}
    for r in traded:
        goods.setdefault(clean_name(r.get("goods_name")), []).append(r)
    gtop = sorted(goods.items(), key=lambda x: -sum(val(r) for r in x[1]))
    for g, rs in gtop[:4]:
        F.append(f"{g}: {fa_int(len(rs))} معامله، ارزش روی هم {usd(sum(val(r) for r in rs))} "
                 f"({fa_num(sum(val(r) for r in rs) / total * 100) if total else '۰'} درصد فروش دلاری).")
    rt = [r for r in rial if (r.get("trade_volume") or 0) > 0]
    if rial:
        F.append(f"قراردادهای ریال صادراتی (جدا از دلاری؛ با دلاری جمع یا مقایسه نکن): {fa_int(len(rial))} کالا روی تابلو، {fa_int(len(rt))} معامله: "
                 + "، ".join(f"{name(r)} {fa_int(r.get('weighted_price') or 0)} ریال صادراتی در هر تن" for r in rt[:4]) + ".")
    facts = "\n".join(f"F{i + 1}. {s}" for i, s in enumerate(F))
    named = big[:8] + hot[:4] + [x[0] for x in up[:3] + down[:3]] + hi[:5] + lo[:5] + rt[:4]
    symmap = "؛ ".join(f"{name(r)} = {r['symbol']}" for r in {r["symbol"]: r for r in named}.values())
    T = {"T1": {"title": "فروش دلاری بر اساس مقصد", "columns": ["مقصد", "کالا", "ارزش معامله", "سهم"],
                "rows": [[c, fa_int(len(rs)), usd(sum(val(r) for r in rs)), fa_num(sum(val(r) for r in rs) / total * 100) + "٪" if total else "—"]
                         for c, rs in dest_rows]},
         "T2": {"title": "بزرگ‌ترین معامله‌های بازار صادراتی", "columns": ["کالا", "تولیدکننده", "ارزش معامله", "رقابت از پایه"],
                "rows": [[clean_name(r.get("goods_name")), r.get("producer"), usd(val(r)), sgn(comp(r))] for r in big[:8]]}}
    if len(hot) >= 3:
        T["T3"] = {"title": "بیشترین رقابت خریداران", "columns": ["کالا", "تولیدکننده", "نرخ بالاتر از پایه", "تقاضا به عرضه"],
                   "rows": [[clean_name(r.get("goods_name")), r.get("producer"), sgn(comp(r)),
                             fa_num(r["demand_volume"] / r["offer_volume"] * 100, 0) + "٪" if r.get("offer_volume") else "—"] for r in hot[:8]]}
    if len(up) + len(down) >= 3:
        T["T4"] = {"title": "بیشترین تغییر نرخ نسبت به آخرین معامله", "columns": ["کالا", "تولیدکننده", "تغییر نرخ"],
                   "rows": [[clean_name(r.get("goods_name")), r.get("producer"), sgn(c)] for r, c in (up[:4] + down[:4])]}
    if len(gtop) >= 3:
        T["T5"] = {"title": "کالاهای پرمعامله‌ی بازار صادراتی", "columns": ["کالا", "تعداد معامله", "ارزش معامله"],
                   "rows": [[g, fa_int(len(rs)), usd(sum(val(r) for r in rs))] for g, rs in gtop[:8]]}
    return facts, symmap, T
