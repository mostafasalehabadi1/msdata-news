"""factsheet.py - turns one symbol's raw numbers into ready-to-quote Persian facts for the news writer.

The model only writes prose; every number it may use is pre-computed, rounded and spelled here.
"""
FA = str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫")
MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def fa_int(n):
    """1571234 -> «۱ میلیون و ۵۷۱ هزار و ۲۳۴»; small numbers stay plain."""
    n = int(round(n))
    if n < 1000:
        return str(n).translate(FA)
    parts = []
    for size, name in ((10**12, "هزار میلیارد"), (10**9, "میلیارد"), (10**6, "میلیون"), (10**3, "هزار")):
        if n >= size:
            parts.append(f"{str(n // size).translate(FA)} {name}")
            n %= size
    if n:
        parts.append(str(n).translate(FA))
    return " و ".join(parts)


def fa_num(x, digits=1):
    """rounded decimal: 4.2 -> «۴٫۲», 15000.0 -> «۱۵ هزار»."""
    if x is None:
        return "نامشخص"
    if abs(x - round(x)) < 1e-9 or abs(x) >= 100:
        return fa_int(x)
    return f"{x:.{digits}f}".rstrip("0").rstrip(".").translate(FA)


YEAR = [None]  # current Jalali year; older dates get their year written out


def fa_date(d):
    """1405/06/22 -> «۲۲ شهریور» (or «۱۷ آذر ۱۴۰۴» when not in the current year)."""
    try:
        y, m, day = d.split("/")
        yr = "" if y == YEAR[0] else " " + y.translate(FA)
        return f"{str(int(day)).translate(FA)} {MONTHS[int(m) - 1]}{yr}"
    except (ValueError, IndexError):
        return d


def toman_billion(thousand_rial):
    """trade values are in thousand rial -> «X میلیارد تومان»."""
    v = (thousand_rial or 0) / 1e7
    return f"{fa_num(v, 1)} میلیارد تومان" if v >= 1 else f"{fa_int(v * 1000)} میلیون تومان"


def build(row, history, peers, date_fa):
    YEAR[0] = date_fa[:4]
    unit = (history[-1].get("unit") if history else None) or "تن"
    price, base = row.get("weighted_price") or 0, row.get("weighted_base_price") or 0
    offered, demand, traded = row.get("offered_qty") or 0, row.get("demand_qty") or 0, row.get("traded_qty") or 0
    L = [f"کالا: {row.get('goods_name')} | تولیدکننده: {row.get('producer_name')} | {row.get('talar')}",
         f"تاریخ امروز: {fa_date(date_fa)} {date_fa[:4].translate(FA)}"]
    L.append(f"عرضه: {fa_num(offered)} {unit} | سفارش خریداران: {fa_num(demand)} {unit} | معامله‌شده: {fa_num(traded)} {unit}")
    if traded > 0:
        L.append(f"نرخ میانگین معامله: {fa_int(price)} ریال هر کیلوگرم | قیمت پایه: {fa_int(base)} ریال")
        if base:
            L.append(f"فاصله‌ی نرخ معامله از قیمت پایه (درصد رقابت): {fa_num((price / base - 1) * 100)} درصد")
        L.append(f"ارزش معامله: {toman_billion(row.get('trade_value'))}")
    else:
        L.append("امروز خریداری برای این عرضه نبود (معامله انجام نشد).")
    if offered:
        L.append(f"سفارش خریداران نسبت به عرضه: {fa_num(demand / offered * 100, 0)} درصد")
    past = [h for h in history if h.get("trade_date") != date_fa and (h.get("traded_qty") or 0) > 0]
    if past:
        last = past[-1]
        lp = last.get("weighted_price") or 0
        chg = f"؛ تغییر نرخ امروز نسبت به آن: {fa_num((price / lp - 1) * 100)} درصد" if lp and traded else ""
        L.append(f"آخرین معامله‌ی قبلی: {fa_date(last['trade_date'])}، نرخ {fa_int(lp)} ریال، {fa_num(last.get('traded_qty'))} {unit}{chg}")
        prices = [h["weighted_price"] for h in past if h.get("weighted_price")]
        if len(prices) >= 3:
            hi = max(past, key=lambda h: h.get("weighted_price") or 0)
            lo = min(past, key=lambda h: h.get("weighted_price") or 1e18)
            L.append(f"در {fa_int(len(past))} معامله‌ی قبلی موجود (از {fa_date(past[0]['trade_date'])}): بیشترین نرخ {fa_int(hi['weighted_price'])} ریال در {fa_date(hi['trade_date'])}، کمترین {fa_int(lo['weighted_price'])} ریال در {fa_date(lo['trade_date'])}")
            if traded and price >= max(prices):
                L.append("نرخ امروز بالاترین نرخ در کل تاریخچه‌ی موجود است.")
            elif traded and price <= min(prices):
                L.append("نرخ امروز پایین‌ترین نرخ در کل تاریخچه‌ی موجود است.")
        L.append("روند نرخ چند معامله‌ی اخیر: " + "، ".join(f"{fa_date(h['trade_date'])}: {fa_int(h.get('weighted_price') or 0)}" for h in past[-5:]))
    else:
        L.append("این نخستین معامله‌ی ثبت‌شده‌ی این نماد در داده‌ی موجود است؛ مقایسه با قبل ممکن نیست.")
    others = [p for p in peers if p is not row and (p.get("traded_qty") or 0) > 0][:5]
    if others:
        L.append("تولیدکننده‌های دیگر همین کالا امروز: " + "؛ ".join(
            f"{p.get('producer_name')}: {fa_int(p.get('weighted_price') or 0)} ریال، {fa_num(p.get('traded_qty'))} {unit}" for p in others))
    return "\n".join(L)
