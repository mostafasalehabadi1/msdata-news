"""facts_report.py - the whole-market facts and candidate tables of the daily report, computed in code (owner, 2026-10-05).

The model only writes: it gets numbered ready sentences (more than it needs) and 4-7 ready tables, and picks 1-3 tables
that are published with the report. build(rows, date_fa, date, sym_dir) -> (facts_text, symbol_map_text, {id: table})
"""
import datetime as dt
import json
import os

from facts_kala import WEEKDAYS
from factsheet import FA, YEAR, fa_date, fa_int, fa_num, toman_billion


def val(r):
    return r.get("trade_value") or 0


def comp(r):
    return (r["weighted_price"] / r["weighted_base_price"] - 1) * 100 if r.get("weighted_price") and r.get("weighted_base_price") else 0


def name(r):
    return f"{r.get('goods_name')} شرکت {r.get('producer_name')}"


def sgn(x):
    return ("+" if x > 0 else "") + fa_num(x) + "٪"


def records(rows, sym_dir, date_fa):
    """symbols whose price today is above / below every earlier trade in the data."""
    hi, lo = [], []
    for r in rows:
        p = os.path.join(sym_dir, f"{r['symbol']}.json")
        if not r.get("traded_qty") or not os.path.exists(p):
            continue
        past = [h["weighted_price"] for h in json.load(open(p, encoding="utf-8")).get("history", [])
                if h.get("trade_date") != date_fa and h.get("weighted_price") and (h.get("traded_qty") or 0) > 0]
        if len(past) >= 5:
            if r["weighted_price"] > max(past):
                hi.append(r)
            elif r["weighted_price"] < min(past):
                lo.append(r)
    return hi, lo


def build(rows, date_fa, date, sym_dir):
    YEAR[0] = date_fa[:4]
    traded = [r for r in rows if (r.get("traded_qty") or 0) > 0]
    total = sum(val(r) for r in rows)
    sup_v, dem_v = sum(r.get("supply_value") or 0 for r in rows), sum(r.get("demand_value") or 0 for r in rows)
    F = [f"روز: {WEEKDAYS[dt.date.fromisoformat(date).weekday()]} {fa_date(date_fa)} {date_fa[:4].translate(FA)}.",
         f"امروز {fa_int(len(rows))} نماد از {fa_int(len({r.get('goods_name') for r in rows}))} کالا و "
         f"{fa_int(len({r.get('producer_name') for r in rows}))} عرضه‌کننده در بازار فیزیکی بورس کالا عرضه شد.",
         f"ارزش کل معاملات امروز {toman_billion(total)} بود."]
    if sup_v:
        F.append(f"ارزش سفارش خریداران {fa_num(dem_v / sup_v * 100, 0)} درصد ارزش عرضه‌ها بود.")
    nt = [r for r in rows if not r.get("traded_qty")]
    if nt:
        F.append(f"{fa_int(len(nt))} نماد بی‌خریدار ماند و هیچ معامله‌ای نداشت: " + "، ".join(name(r) for r in nt[:5]) + ".")
    part = [r for r in traded if r["traded_qty"] < (r.get("offered_qty") or 0) - 1e-9]
    if part:
        F.append(f"در {fa_int(len(part))} نماد فقط بخشی از عرضه فروش رفت؛ روی هم {fa_num(sum(r['offered_qty'] - r['traded_qty'] for r in part))} واحد بی‌خریدار ماند.")
    full = [r for r in traded if r["traded_qty"] >= (r.get("offered_qty") or 0) - 1e-9]
    F.append(f"در {fa_int(len(full))} نماد تمام عرضه به فروش رفت.")
    at_base = sum(1 for r in traded if abs(comp(r)) < 0.05)
    hot = sorted([r for r in traded if comp(r) > 0.05], key=comp, reverse=True)
    F.append(f"{fa_int(at_base)} نماد روی قیمت پایه و {fa_int(len(hot))} نماد با رقابت بالاتر از قیمت پایه معامله شد." if hot else
             f"امروز هیچ نمادی با رقابت بالاتر از قیمت پایه معامله نشد؛ {fa_int(at_base)} نماد روی قیمت پایه فروخته شد.")
    more = [r for r in rows if (r.get("demand_qty") or 0) > (r.get("offered_qty") or 0) > 0]
    if more:
        top = max(more, key=lambda r: r["demand_qty"] / r["offered_qty"])
        F.append(f"در {fa_int(len(more))} نماد سفارش خریداران از عرضه بیشتر بود؛ بیشترین نسبت مال {name(top)} بود: "
                 f"تقاضا {fa_num(top['demand_qty'] / top['offered_qty'])} برابر عرضه.")
    halls = {}
    for r in rows:
        halls.setdefault(r.get("talar") or "نامشخص", []).append(r)
    hall_rows = sorted(halls.items(), key=lambda x: -sum(val(r) for r in x[1]))
    for h, rs in hall_rows:
        v = sum(val(r) for r in rs)
        if v:
            F.append(f"{h}: {fa_int(len(rs))} نماد و ارزش {toman_billion(v)} ({fa_num(v / total * 100)} درصد کل بازار).")
    big = sorted(traded, key=val, reverse=True)
    for r in big[:5]:
        F.append(f"معامله‌ی بزرگ: {name(r)} با ارزش {toman_billion(val(r))}؛ " +
                 ("روی قیمت پایه." if abs(comp(r)) < 0.05 else f"{fa_num(comp(r))} درصد بالاتر از قیمت پایه."))
    for r in hot[:4]:
        F.append(f"رقابت: نرخ {name(r)} {fa_num(comp(r))} درصد بالاتر از قیمت پایه بسته شد.")
    ch = [r for r in traded if r.get("price_change_pct") is not None and abs(r["price_change_pct"]) >= 0.05]
    up = sorted([r for r in ch if r["price_change_pct"] > 0], key=lambda r: -r["price_change_pct"])
    down = sorted([r for r in ch if r["price_change_pct"] < 0], key=lambda r: r["price_change_pct"])
    F.append(f"نسبت به آخرین معامله‌ی همان نماد، {fa_int(len(up))} نماد گران‌تر و {fa_int(len(down))} نماد ارزان‌تر فروخته شد.")
    for r in up[:3]:
        F.append(f"افزایش نرخ: {name(r)} {fa_num(r['price_change_pct'])} درصد گران‌تر از آخرین معامله‌ی خود.")
    for r in down[:3]:
        F.append(f"کاهش نرخ: {name(r)} {fa_num(abs(r['price_change_pct']))} درصد ارزان‌تر از آخرین معامله‌ی خود.")
    hi, lo = records(traded, sym_dir, date_fa)
    if hi:
        F.append(f"{fa_int(len(hi))} نماد امروز به بیشترین نرخ خود در داده‌ی msdata رسید: " + "، ".join(name(r) for r in hi[:5]) + ".")
    if lo:
        F.append(f"{fa_int(len(lo))} نماد امروز کمترین نرخ خود در داده‌ی msdata را ثبت کرد: " + "، ".join(name(r) for r in lo[:5]) + ".")
    goods = {}
    for r in traded:
        goods.setdefault(r.get("goods_name"), []).append(r)
    gtop = sorted(goods.items(), key=lambda x: -sum(val(r) for r in x[1]))
    for g, rs in gtop[:4]:
        if len(rs) >= 2:
            ps = sorted(rs, key=lambda r: r["weighted_price"])
            head = f"{g}: {fa_int(len(rs))} تولیدکننده فروختند، ارزش روی هم {toman_billion(sum(val(r) for r in rs))}؛ "
            F.append(head + (f"همه با نرخ یکسان {fa_int(ps[0]['weighted_price'])} ریال." if ps[0]["weighted_price"] == ps[-1]["weighted_price"] else
                             f"ارزان‌ترین شرکت {ps[0].get('producer_name')} ({fa_int(ps[0]['weighted_price'])} ریال) و گران‌ترین شرکت "
                             f"{ps[-1].get('producer_name')} ({fa_int(ps[-1]['weighted_price'])} ریال)."))
    facts = "\n".join(f"F{i + 1}. {s}" for i, s in enumerate(F))
    named = big[:8] + hot[:4] + up[:3] + down[:3] + hi[:5] + lo[:5]
    symmap = "؛ ".join(f"{name(r)} = {r['symbol']}" for r in {r["symbol"]: r for r in named}.values())

    T = {"T1": {"title": "تالارهای بورس کالا امروز", "columns": ["تالار", "نماد", "ارزش معامله", "سهم از بازار"],
                "rows": [[h, fa_int(len(rs)), toman_billion(sum(val(r) for r in rs)), fa_num(sum(val(r) for r in rs) / total * 100) + "٪" if total else "—"]
                         for h, rs in hall_rows]},
         "T2": {"title": "بزرگ‌ترین معامله‌های امروز", "columns": ["کالا", "تولیدکننده", "ارزش معامله", "رقابت از پایه"],
                "rows": [[r.get("goods_name"), r.get("producer_name"), toman_billion(val(r)), sgn(comp(r))] for r in big[:8]]}}
    if len(hot) >= 3:
        T["T3"] = {"title": "بیشترین رقابت خریداران", "columns": ["کالا", "تولیدکننده", "نرخ بالاتر از پایه", "تقاضا به عرضه"],
                   "rows": [[r.get("goods_name"), r.get("producer_name"), sgn(comp(r)),
                             fa_num(r["demand_qty"] / r["offered_qty"] * 100, 0) + "٪" if r.get("offered_qty") else "—"] for r in hot[:8]]}
    if len(up) + len(down) >= 3:
        T["T4"] = {"title": "بیشترین تغییر نرخ نسبت به آخرین معامله", "columns": ["کالا", "تولیدکننده", "تغییر نرخ"],
                   "rows": [[r.get("goods_name"), r.get("producer_name"), sgn(r["price_change_pct"])] for r in (up[:4] + down[:4])]}
    if len(gtop) >= 3:
        T["T5"] = {"title": "کالاهای پرمعامله‌ی امروز", "columns": ["کالا", "تعداد تولیدکننده", "ارزش معامله"],
                   "rows": [[g, fa_int(len(rs)), toman_billion(sum(val(r) for r in rs))] for g, rs in gtop[:8]]}
    if len(hi) + len(lo) >= 2:
        T["T6"] = {"title": "رکوردهای نرخ امروز (در داده‌ی msdata)", "columns": ["کالا", "تولیدکننده", "رکورد"],
                   "rows": [[r.get("goods_name"), r.get("producer_name"), "بیشترین نرخ"] for r in hi[:6]] +
                           [[r.get("goods_name"), r.get("producer_name"), "کمترین نرخ"] for r in lo[:4]]}
    return facts, symmap, T
