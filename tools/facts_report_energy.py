"""facts_report_energy.py - whole-market facts and candidate tables of the Iran Energy Exchange daily report (owner 1405-07-27).
Same shape and rules as facts_report_kish.py: numbered ready sentences + ready tables, all computed here.
Four parts (owner 1405-07-19): physical (domestic and export rings), electricity, derivatives, securities and funds.
build(phys, power, contracts, securities, date_fa, date) -> (facts_text, symbol_map_text, {id: table})
"""
import datetime as dt

from facts_energy import clean
from facts_kala import WEEKDAYS
from factsheet import YEAR, fa_date, fa_int, fa_num

MARKET_FA = {"power_derivative": "مشتقه‌ی برق", "power_derivative_green": "مشتقه‌ی برق سبز", "power_derivative_free": "مشتقه‌ی برق آزاد",
             "capacity_cert": "گواهی ظرفیت", "parallel_salaf": "سلف موازی", "project_fund": "صندوق پروژه", "fund": "صندوق",
             "deposit_cert": "گواهی سپرده", "energy_saving_cert": "گواهی صرفه‌جویی انرژی", "green_cert": "گواهی برق سبز",
             "futures": "آتی", "option": "اختیار معامله"}


def rial(v):
    v = v or 0
    if v >= 1e12:
        return f"{fa_num(v / 1e12)} هزار میلیارد ریال"
    if v >= 1e9:
        return f"{fa_num(v / 1e9, 0)} میلیارد ریال"
    return f"{fa_int(v / 1e6)} میلیون ریال"


def comp(r):
    return (r["weighted_price"] / r["base_price"] - 1) * 100 if r.get("weighted_price") and r.get("base_price") else 0


def name(r):
    p = clean(r.get("producer") or r.get("supplier"))
    return f"{clean(r.get('goods_name'))} {p if p.startswith('شرکت') else 'شرکت ' + p}"


def sgn(x):
    return ("+" if x > 0 else "") + fa_num(x) + "٪"


def mk(x):
    return MARKET_FA.get(x.get("market"), "سایر")


def build(phys, power, contracts, securities, date_fa, date):
    YEAR[0] = date_fa[:4]
    items = phys.get("items") or []
    traded = [r for r in items if (r.get("trade_volume") or 0) > 0]
    tot = (phys.get("total_value_rial") or 0) + (power.get("total_value_rial") or 0) + (contracts.get("total_value_rial") or 0) + (securities.get("total_value_rial") or 0)
    F = [f"روز: {WEEKDAYS[dt.date.fromisoformat(date).weekday()]} {fa_date(date_fa)}.",
         f"ارزش کل معامله‌های بورس انرژی در چهار بازار فیزیکی، برق، مشتقه و اوراق {rial(tot)} بود."]
    # 1) physical
    pv = phys.get("total_value_rial") or 0
    F.append(f"بازار فیزیکی: {fa_int(len(items))} کالا روی تابلو رفت و {fa_int(len(traded))} کالا معامله شد؛ ارزش معامله‌ها {rial(pv)}.")
    dom, exp = phys.get("domestic_value_rial") or 0, phys.get("international_value_rial") or 0
    F.append(f"رینگ داخلی {rial(dom)} و رینگ صادراتی {rial(exp)} معامله داشت." if exp else
             "رینگ صادراتی این روز معامله‌ای نداشت؛ همه‌ی معامله‌های فیزیکی در رینگ داخلی بود.")
    nt = [r for r in items if not (r.get("trade_volume") or 0)]
    if nt:
        F.append(f"{fa_int(len(nt))} کالا بی‌خریدار ماند: " + "، ".join(name(r) for r in nt[:5]) + ".")
    big = sorted(traded, key=lambda r: r.get("total_value_rial") or 0, reverse=True)
    for r in big[:5]:
        F.append(f"معامله‌ی بزرگ فیزیکی: {name(r)}، {fa_int(r['trade_volume'])} {r.get('volume_unit') or ''} به ارزش {rial(r.get('total_value_rial'))}؛ " +
                 ("روی قیمت پایه." if abs(comp(r)) < 0.05 else f"{fa_num(comp(r))} درصد {'بالاتر' if comp(r) > 0 else 'پایین‌تر'} از قیمت پایه."))
    hot = sorted([r for r in traded if comp(r) > 0.05], key=comp, reverse=True)
    for r in hot[:3]:
        F.append(f"رقابت: قیمت {name(r)} {fa_num(comp(r))} درصد بالاتر از قیمت پایه بسته شد.")
    more = [r for r in traded if (r.get("demand_volume") or 0) > (r.get("offer_volume") or 0) > 0]
    if more:
        top = max(more, key=lambda r: r["demand_volume"] / r["offer_volume"])
        F.append(f"در {fa_int(len(more))} کالا تقاضا از عرضه بیشتر بود؛ بیشترین نسبت مال {name(top)} بود: تقاضا {fa_num(top['demand_volume'] / top['offer_volume'])} برابر عرضه.")
    # 2) electricity
    pw = [r for r in power.get("items") or [] if (r.get("trade_volume") or 0) > 0]
    if pw:
        F.append(f"بازار برق فیزیکی: {fa_int(len(pw))} معامله به ارزش {rial(power.get('total_value_rial'))}؛ " +
                 "، ".join(f"{clean(r.get('producer') or r.get('supplier'))} {fa_int(r['trade_volume'])} کیلووات به قیمت {fa_int(r.get('weighted_price') or 0)} ریال" for r in pw[:3]) + ".")
    else:
        F.append("بازار برق فیزیکی این روز معامله‌ای نداشت.")
    # 3) derivatives
    dv = sorted([x for x in contracts.get("items") or [] if (x.get("trade_value_rial") or 0) > 0], key=lambda x: -x["trade_value_rial"])
    if dv:
        F.append(f"بازار مشتقه: {fa_int(len(dv))} قرارداد معامله شد، به ارزش {rial(contracts.get('total_value_rial'))}.")
        for x in dv[:3]:
            F.append(f"قرارداد {clean(x.get('name'))} ({mk(x)}): {fa_int(x.get('trade_count') or 0)} معامله، ارزش {rial(x['trade_value_rial'])}، قیمت پایانی {fa_int(x.get('close_price') or 0)} ریال.")
    else:
        F.append("بازار مشتقه این روز معامله‌ای نداشت.")
    # 4) securities and funds
    sv = sorted([x for x in securities.get("items") or [] if (x.get("trade_value_rial") or 0) > 0], key=lambda x: -x["trade_value_rial"])
    groups = {}
    for x in sv:
        groups.setdefault(mk(x), []).append(x)
    if sv:
        F.append(f"اوراق و صندوق‌ها: {fa_int(len(sv))} نماد معامله شد، به ارزش {rial(securities.get('total_value_rial'))}؛ " +
                 "، ".join(f"{g} {rial(sum(x['trade_value_rial'] for x in xs))}" for g, xs in sorted(groups.items(), key=lambda g: -sum(x["trade_value_rial"] for x in g[1]))) + ".")
        for x in sv[:3]:
            c = x.get("close_change_pct")
            F.append(f"نماد {clean(x.get('name') or x.get('symbol'))} ({mk(x)}): ارزش {rial(x['trade_value_rial'])}" +
                     (f"، قیمت پایانی {sgn(c)} نسبت به روز قبل." if c else "، قیمت پایانی بی‌تغییر."))
    else:
        F.append("در اوراق و صندوق‌های بورس انرژی این روز معامله‌ای ثبت نشد.")
    facts = "\n".join(f"F{i + 1}. {s}" for i, s in enumerate(F))
    named = big[:8] + hot[:3] + pw[:3]
    symmap = "؛ ".join(f"{name(r)} = {r['symbol']}" for r in {r["symbol"]: r for r in named}.values())
    T = {"T1": {"title": "ارزش معامله‌های بورس انرژی به تفکیک بازار", "columns": ["بازار", "ارزش معامله", "سهم"],
                "rows": [[m, rial(v), fa_num(v / tot * 100) + "٪" if tot else "—"] for m, v in
                         (("فیزیکی", pv), ("برق فیزیکی", power.get("total_value_rial") or 0), ("مشتقه", contracts.get("total_value_rial") or 0),
                          ("اوراق و صندوق‌ها", securities.get("total_value_rial") or 0))]},
         "T2": {"title": "بزرگ‌ترین معامله‌های بازار فیزیکی", "columns": ["کالا", "عرضه‌کننده", "ارزش معامله", "رقابت از پایه"],
                "rows": [[clean(r.get("goods_name")), clean(r.get("producer") or r.get("supplier")), rial(r.get("total_value_rial")), sgn(comp(r))] for r in big[:8]]}}
    if len(dv) >= 2:
        T["T3"] = {"title": "قراردادهای پرمعامله‌ی بازار مشتقه", "columns": ["قرارداد", "معامله", "ارزش", "قیمت پایانی"],
                   "rows": [[clean(x.get("name")), fa_int(x.get("trade_count") or 0), rial(x["trade_value_rial"]), fa_int(x.get("close_price") or 0)] for x in dv[:8]]}
    if len(sv) >= 3:
        T["T4"] = {"title": "اوراق و صندوق‌های پرمعامله", "columns": ["نماد", "بازار", "ارزش", "تغییر قیمت پایانی"],
                   "rows": [[clean(x.get("name") or x.get("symbol")), mk(x), rial(x["trade_value_rial"]), sgn(x.get("close_change_pct") or 0)] for x in sv[:8]]}
    return facts, symmap, T
