"""facts_kala.py - everything the symbol-news writer may say, computed in code (owner, 2026-10-05).

The model does no arithmetic: it gets a numbered list of ready Persian sentences (more than it needs, it picks some)
and 2-4 ready tables; it chooses one table id and the news is published with that table.
build(row, history, peers, date_fa, date, ytd_value) -> (facts_text, {table_id: table})
table = {"title": str, "columns": [str], "rows": [[str]]}
"""
import datetime as dt
import re

from factsheet import FA, fa_date, fa_int, fa_num, toman_billion, YEAR

WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]  # datetime.weekday() order


def pct(a, b):
    return (a / b - 1) * 100 if a and b else None


def signed(p):
    """+4.23 -> «۴٫۲ درصد بیشتر/گران‌تر» is done by callers; here: «۴٫۲ درصد»."""
    return f"{fa_num(abs(p))} درصد"


def days_between(d1, d2):
    """Jalali yyyy/mm/dd strings -> rough day gap (months 1-6 = 31 days, 7-11 = 30)."""
    def n(d):
        y, m, day = (int(x) for x in d.split("/"))
        return y * 365 + sum(31 if i < 6 else 30 for i in range(m - 1)) + day
    return n(d2) - n(d1)


def build(row, history, peers, date_fa, date, ytd_value=None):
    YEAR[0] = date_fa[:4]
    unit = (history[-1].get("unit") if history else None) or "تن"
    goods, prod, hall = row.get("goods_name") or "", row.get("producer_name") or "", row.get("talar") or ""
    price, base = row.get("weighted_price") or 0, row.get("weighted_base_price") or 0
    off, dem, trd = row.get("offered_qty") or 0, row.get("demand_qty") or 0, row.get("traded_qty") or 0
    wd = WEEKDAYS[dt.date.fromisoformat(date).weekday()]
    F = [f"نام کالا: {goods}؛ تولیدکننده: شرکت {prod}؛ {hall}.",
         f"روز معامله: {wd} {fa_date(date_fa)} {date_fa[:4].translate(FA)}."]
    # today
    F.append(f"شرکت {prod} امروز {fa_num(off)} {unit} {goods} عرضه کرد و خریداران {fa_num(dem)} {unit} سفارش دادند.")
    if off:
        r = dem / off * 100
        F.append(f"سفارش خریداران {fa_num(r, 0)} درصد عرضه بود" + (f"؛ یعنی تقاضا {fa_num(dem / off)} برابر عرضه بود." if r >= 150 else "."))
    if trd <= 0:
        F.append("امروز هیچ معامله‌ای انجام نشد و کالا بی‌خریدار ماند (از نبود معامله نتیجه‌ی قیمتی نگیر).")
    elif trd >= off - 1e-9:
        F.append("تمام عرضه به فروش رفت.")
    else:
        F.append(f"فقط {fa_num(trd)} {unit} از {fa_num(off)} {unit} عرضه فروش رفت ({fa_num(trd / off * 100, 0)} درصد) و "
                 f"{fa_num(off - trd)} {unit} بی‌خریدار ماند.")
    if trd > 0:
        F.append(f"نرخ میانگین معامله {fa_int(price)} ریال هر کیلوگرم بود و قیمت پایه‌ی عرضه {fa_int(base)} ریال.")
        c = pct(price, base)
        if c is not None:
            F.append("نرخ معامله با قیمت پایه برابر بود و رقابتی بالاتر از پایه شکل نگرفت." if abs(c) < 0.05 else
                     f"خریداران با رقابت، نرخ را {signed(c)} بالاتر از قیمت پایه بردند." if c > 0 else
                     f"نرخ معامله {signed(c)} پایین‌تر از قیمت پایه بود.")
        F.append(f"ارزش معامله‌ی امروز {toman_billion(row.get('trade_value'))} بود.")
    # history
    past = [h for h in history if h.get("trade_date") != date_fa and (h.get("traded_qty") or 0) > 0 and h.get("weighted_price")]
    if not past:
        F.append("این نخستین معامله‌ی ثبت‌شده‌ی این نماد در داده‌های موجود است؛ مقایسه با قبل ممکن نیست.")
    else:
        last = past[-1]
        lp, gap = last["weighted_price"], days_between(last["trade_date"], date_fa)
        F.append(f"آخرین معامله‌ی قبلی در {fa_date(last['trade_date'])} بود ({"دیروز" if gap == 1 else fa_int(gap) + " روز پیش"}) با نرخ {fa_int(lp)} ریال و "
                 f"{fa_num(last.get('traded_qty'))} {unit} معامله.")
        c = pct(price, lp) if trd else None
        if c is not None:
            F.append(f"نرخ امروز با آخرین معامله ({fa_date(last['trade_date'])}) برابر بود." if abs(c) < 0.05 else
                     f"نرخ امروز {signed(c)} {'گران‌تر' if c > 0 else 'ارزان‌تر'} از آخرین معامله ({fa_date(last['trade_date'])}) بود.")
        q = pct(trd, last.get("traded_qty"))
        if trd and q is not None and abs(q) >= 20:
            F.append(f"حجم معامله‌ی امروز {fa_num(q / 100 + 1)} برابر آخرین معامله بود." if q >= 100 else
                     f"حجم معامله‌ی امروز {signed(q)} {'بیشتر' if q > 0 else 'کمتر'} از آخرین معامله بود.")
        lb = last.get("weighted_base_price")
        cb = pct(base, lb)
        if cb is not None and abs(cb) >= 0.05:
            F.append(f"قیمت پایه‌ی این عرضه {signed(cb)} {'بالاتر' if cb > 0 else 'پایین‌تر'} از قیمت پایه‌ی عرضه‌ی {fa_date(last['trade_date'])} بود.")
        if gap >= 30:
            F.append(f"این نماد پس از {fa_int(gap)} روز غیبت دوباره معامله شد.")
        prices = [h["weighted_price"] for h in past]
        if len(past) >= 3:
            hi, lo = max(past, key=lambda h: h["weighted_price"]), min(past, key=lambda h: h["weighted_price"])
            F.append(f"در {fa_int(len(past))} معامله‌ی ثبت‌شده از {fa_date(past[0]['trade_date'])}، بیشترین نرخ {fa_int(hi['weighted_price'])} ریال "
                     f"({fa_date(hi['trade_date'])}) و کمترین {fa_int(lo['weighted_price'])} ریال ({fa_date(lo['trade_date'])}) بود.")
            if trd and price > max(prices):
                F.append(f"نرخ امروز بیشترین نرخ این نماد از {fa_date(past[0]['trade_date'])} (نخستین معامله‌ی موجود) است.")
            elif trd and price < min(prices):
                F.append(f"نرخ امروز کمترین نرخ این نماد از {fa_date(past[0]['trade_date'])} (نخستین معامله‌ی موجود) است.")
            k = past[-5:]
            avg = sum(h["weighted_price"] for h in k) / len(k)
            c = pct(price, avg) if trd else None
            if c is not None and abs(c) >= 0.5:
                F.append(f"نرخ امروز {signed(c)} {'بالاتر' if c > 0 else 'پایین‌تر'} از میانگین {fa_int(len(k))} معامله‌ی اخیر ({fa_int(avg)} ریال) بود.")
        seq = prices + ([price] if trd else [])
        n, d0 = 0, None
        for a, b in zip(reversed(seq[:-1]), reversed(seq[1:])):
            d = (b > a) - (b < a)
            if d0 is None:
                d0 = d
            if d != d0 or d == 0:
                break
            n += 1
        if n >= 2:
            F.append(f"نرخ این نماد {fa_int(n)} معامله‌ی پیاپی {'گران‌تر' if d0 > 0 else 'ارزان‌تر'} شده است.")
        flat = 0
        for a, b in zip(reversed(seq[:-1]), reversed(seq[1:])):
            if a != b:
                break
            flat += 1
        if flat >= 2:
            F.append(f"نرخ این نماد {fa_int(flat + 1)} معامله‌ی پیاپی ثابت مانده است.")
        yr = [h for h in past if h["trade_date"][:4] == date_fa[:4]]
        if len(yr) >= 2:
            F.append(f"این نماد امسال (از فروردین {date_fa[:4].translate(FA)}) {fa_int(len(yr) + (1 if trd else 0))} بار معامله شده است.")
    if ytd_value:
        F.append(f"ارزش کل معاملات این نماد از ابتدای امسال {toman_billion(ytd_value)} است.")
    # same goods, other producers today
    same = [p for p in peers if (p.get("traded_qty") or 0) > 0 and p.get("weighted_price")]
    others = [p for p in same if p is not row and p.get("producer_name") != prod]
    if others:
        F.append(f"امروز {fa_int(len(others))} تولیدکننده‌ی دیگر هم {goods} فروختند: " + "؛ ".join(
            f"شرکت {p.get('producer_name')} با نرخ {fa_int(p['weighted_price'])} ریال" for p in others[:4]) + ".")
        if trd:
            ps = sorted(same, key=lambda p: p["weighted_price"])
            # only a strict min/max (content agent 1405-07-14: a tie was called «ارزان‌ترین»)
            if ps[0] is row and ps[0]["weighted_price"] < ps[1]["weighted_price"]:
                F.append(f"شرکت {prod} ارزان‌ترین فروشنده‌ی {goods} در معاملات امروز بود.")
            elif ps[-1] is row and ps[-1]["weighted_price"] > ps[-2]["weighted_price"]:
                F.append(f"شرکت {prod} گران‌ترین فروشنده‌ی {goods} در معاملات امروز بود.")
            tot = sum(p["traded_qty"] for p in same)
            if tot:
                F.append(f"سهم شرکت {prod} از کل {fa_num(tot)} {unit} {goods} معامله‌شده‌ی امروز {fa_num(trd / tot * 100, 0)} درصد بود.")
    # style guide v2: no «امروز» in the facts (the model copied it 3-6 times per news); the date is in F1
    F = [F[0]] + [re.sub(r"\bامروز\b", "این جلسه", x) for x in F[1:]]
    facts = "\n".join(f"F{i + 1}. {s}" for i, s in enumerate(F))
    return facts, tables(row, past, same if others else [], unit, goods, trd)


def tables(row, past, same, unit, goods, trd):
    T = {}
    price, base = row.get("weighted_price") or 0, row.get("weighted_base_price") or 0
    off, dem = row.get("offered_qty") or 0, row.get("demand_qty") or 0
    T["T1"] = {"title": "معامله‌ی امروز", "columns": ["شاخص", "مقدار"], "rows": [
        ["عرضه", f"{fa_num(off)} {unit}"], ["سفارش خریداران", f"{fa_num(dem)} {unit}"], ["معامله‌شده", f"{fa_num(trd)} {unit}"],
        ["قیمت پایه (ریال/کیلوگرم)", fa_int(base)], ["نرخ میانگین (ریال/کیلوگرم)", fa_int(price) if trd else "—"],
        ["ارزش معامله", toman_billion(row.get("trade_value")) if trd else "—"]]}
    if len(past) >= 2:
        seq = past[-5:] + ([{"trade_date": row.get("trade_date") or "امروز", "weighted_price": price, "traded_qty": trd}] if trd else [])
        rows, prev = [], None
        for h in seq:
            c = pct(h["weighted_price"], prev)
            rows.append([fa_date(h["trade_date"]) if "/" in str(h["trade_date"]) else "امروز", fa_int(h["weighted_price"]),
                         f"{fa_num(h.get('traded_qty'))} {unit}", "—" if c is None else ("+" if c > 0 else "") + fa_num(c) + "٪"])
            prev = h["weighted_price"]
        T["T2"] = {"title": "روند نرخ در معامله‌های اخیر", "columns": ["تاریخ", "نرخ (ریال/کیلوگرم)", "حجم معامله", "تغییر"], "rows": rows}
    if len(same) >= 2:
        T["T3"] = {"title": f"تولیدکننده‌های {goods} در معاملات امروز", "columns": ["تولیدکننده", "نرخ (ریال/کیلوگرم)", "معامله‌شده", "تقاضا به عرضه"],
                   "rows": [[p.get("producer_name"), fa_int(p["weighted_price"]), f"{fa_num(p['traded_qty'])} {unit}",
                             f"{fa_num((p.get('demand_qty') or 0) / p['offered_qty'] * 100, 0)}٪" if p.get("offered_qty") else "—"]
                            for p in sorted(same, key=lambda p: p["weighted_price"])[:8]]}
    if len(past) >= 3 and trd:
        k = past[-5:]
        hi, lo = max(past, key=lambda h: h["weighted_price"]), min(past, key=lambda h: h["weighted_price"])
        T["T4"] = {"title": "نرخ امروز در برابر گذشته", "columns": ["مبنا", "نرخ (ریال/کیلوگرم)", "تفاوت نرخ امروز"], "rows": [
            [f"آخرین معامله ({fa_date(past[-1]['trade_date'])})", fa_int(past[-1]["weighted_price"]), fa_num(pct(price, past[-1]["weighted_price"])) + "٪"],
            [f"میانگین {fa_int(len(k))} معامله‌ی اخیر", fa_int(sum(h['weighted_price'] for h in k) / len(k)),
             fa_num(pct(price, sum(h['weighted_price'] for h in k) / len(k))) + "٪"],
            [f"بیشترین ({fa_date(hi['trade_date'])})", fa_int(hi["weighted_price"]), fa_num(pct(price, hi["weighted_price"])) + "٪"],
            [f"کمترین ({fa_date(lo['trade_date'])})", fa_int(lo["weighted_price"]), fa_num(pct(price, lo["weighted_price"])) + "٪"]]}
    return T
