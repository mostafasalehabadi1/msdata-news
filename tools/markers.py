"""markers.py - the automatic checks of style.md section 6 (writing framework v2), run on every text after the model writes it.

check(text, ...) returns a list of problems (empty = pass). A non-empty list sends the text back to the model with the problems.
usage: python tools/markers.py file.json   (prints the problems of title/lead/text of each item)
"""
import json
import re
import sys

PATTERNS = [
    r"به گزارش (?:گروه )?\S* ?\S* ?\S* ?ام[‌ ]?اس[‌ ]?دیتا",
    r"بررسی (?:داده|آمار|روند)\S* نشان می[‌ ]?دهد",
    r"نشان[‌ ]?دهنده|نشانگر|حاکی از|بیانگر",
    r"(?m)^(?:در مجموع|به طور کلی|در نهایت|در پایان)",
    r"به خود اختصاص داد|به ثبت رسید|بالغ (?:شد|می)|رقم (?:خورد|زد)|به شمار می[‌ ]?رود|محسوب می[‌ ]?شود",
    r"روی دست \S+ (?:نماند|ماند)|پرونده(?:‌ی)? \S+ (?:را )?(?:به تمامی )?بست|ایستاد|بایستد|سفارش خورد|پیشی گرفت",
    r"نه[‌ ]?تنها",
    r"(?<![\d۰-۹])[1۱] (?:هزار|میلیون|میلیارد)",
    r"\(\d+\*\d+\)|[A-Z]{2,}[-\d.]+[A-Z\d.-]*",
    r"(?<![؀-ۿ])(?:بزرگترین|بیشترین|کمترین|بالاترین|میشود|میکند)(?![؀-ۿ])|(?<=\S) (?:ها|های|تر|ترین)(?![؀-ۿ])",
    r"(?:برداشتند|خریدند|فروخته شد|رساند|خریداری کردند) تا (?:نرخ|رکورد|قیمت|بخشی)",
    r"رکورد (?:تاریخی|بی‌سابقه)|سقف تاریخی|بالاترین سطح در کل تاریخچه",
    r"به ارزیابی ام[‌ ]?اس[‌ ]?دیتا|جمع‌بندی روز ساده است|در مجموع، الگوی امروز",
    r"سفارشی برای خرید یا فروش نیست|پیشنهادی برای معامله نیست|توصیه(?:‌ی)? خرید یا فروش",
    r"[가-힯぀-ヿ一-鿿Ѐ-ӿ]",
    r"[ادذرزژو]‌(?!گو|جو)",
    r"[؀-ۿ]—[؀-ۿ]",
]
RES = [re.compile(p) for p in PATTERNS]
OFFER_VERBS = ("عرضه کرد", "روی میز گذاشت", "روانه‌ی بازار کرد", "روانه بازار کرد", "به بازار آورد", "به فروش گذاشت")
MONTHS = "فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند"
DATE = re.compile(r"[\d۰-۹]{2,4}/[\d۰-۹]{1,2}/[\d۰-۹]{1,2}|[\d۰-۹]{1,2} (?:%s)(?: [\d۰-۹]{4})?" % MONTHS)
NUM = re.compile(r"[\d۰-۹]+(?:[٫.,،][\d۰-۹]+)*")


def numbers(t):
    """numbers in the text; dates (۱۲ مهر ۱۴۰۵, ۱۴۰۵/۰۷/۱۲) are not counted."""
    return NUM.findall(DATE.sub(" ", t))


def has_subheads(t):
    return any(0 < len(p.split()) <= 8 and not re.search(r"[.!؟?:]$", p.strip())
               for p in re.split(r"\n\s*\n", t.strip())[1:])


def check(text, title="", lead="", dated=True, table=False, full=False):
    probs = []
    allt = "\n".join(x for x in (title, lead, text) if x)
    for rx, p in zip(RES, PATTERNS):
        m = rx.search(allt)
        if m:
            probs.append(f"عبارت ممنوع «{m.group(0).strip()}» (الگو: {p[:40]})")
    n = len(numbers(text))
    cap = 3 if table else 8
    if n > cap:
        probs.append(f"{n} عدد در متن؛ حداکثر {cap}" + (" (بقیه در جدول)" if table else ""))
    for s in re.split(r"[.!؟?\n]", text):
        if len(numbers(s)) > 2:
            probs.append(f"بیش از ۲ عدد در یک جمله: «{s.strip()[:60]}»")
            break
    if dated and re.search(r"(?<![؀-ۿ])امروز", allt):
        probs.append("«امروز» ننویس؛ نام روز و تاریخ را بنویس")
    if text.count("در حالی که") > 1:
        probs.append("«در حالی که» بیش از ۱ بار")
    if len(text.split()) > 400 and not has_subheads(text):
        probs.append("متن بیش از ۴۰۰ کلمه بدون میان‌تیتر")
    if text.count("؛ رقمی که") > 1:
        probs.append("«؛ رقمی که» بیش از ۱ بار")
    if sum(1 for v in OFFER_VERBS if v in text) > 2:
        probs.append("بیش از ۲ فعل مختلف برای «عرضه کرد»")
    if re.search(r"[\d۰-۹]٫[\d۰-۹]", text) and re.search(r"[\d۰-۹]\.[\d۰-۹]", text):
        probs.append("اعشار با دو جداکننده‌ی مختلف («٫» و «.»)")
    if re.search(r"[0-9]", text) and re.search(r"[۰-۹]", text):
        probs.append("رقم لاتین و فارسی قاطی شده؛ همه فارسی")
    return probs


if __name__ == "__main__":
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    for it in d.get("items", [d]):
        p = check(it.get("text", ""), it.get("title", ""), it.get("lead", ""), table=bool(it.get("table")))
        print(it.get("symbol", it.get("slug", "")), len(p), p[:5])
