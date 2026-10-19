"""report_staged.py - the daily report written in small pieces by the paid model (owner 2026-10-12).

A small model cannot keep the whole report rule sheet: asked for the report in one go it wrote 350 words with the banned
phrases. Here it only writes one short section at a time from a few facts; everything that can be decided without a
model is done by the code: sentences with a number that is not in the facts are cut, a sentence with a banned phrase is
rewritten alone (or cut), digits are made Persian, the number budget is kept, sub-headings, tables and links are put
together here. The result still has to pass write_report.check() like every other report.
write(call, fx, symmap, tables, market, check, known, note) -> dict for check()
"""
import json
import re

import markers
import write_free as w

SECTION = ("تو سردبیر بورس کالای ام‌اس‌دیتا هستی و یک بخش از «{market}» را می‌نویسی. فقط از حقیقت‌های داده‌شده بنویس؛ "
           "هیچ عدد، درصد، مقایسه، علت یا پیش‌بینی از خودت نساز.\n"
           "- خط اول فقط یک میان‌تیتر ۲ تا ۶ کلمه‌ای، بدون عدد و بدون نقطه. بعد یک خط خالی. بعد متن.\n"
           "- متن: ۱۱۰ تا ۱۴۰ کلمه در ۲ پاراگراف که با یک خط خالی جدا شده‌اند.\n"
           "- جمله‌های کوتاه با فعل ساده (فروخت، خرید، رسید، بود، است). کل بخش حداکثر ۵ عدد داشته باشد و بیشتر جمله‌ها بدون عدد باشند.\n"
           "- به‌جای عدد، نام شرکت‌ها، کالاها و مقصدها را بگو: چه چیزی فروش رفت، چه چیزی خریدار نداشت، قیمت بالا رفت یا پایین آمد.\n"
           "- نام کالا و شرکت را دقیقاً همان‌طور که در حقیقت‌ها آمده بنویس.\n"
           "- «امروز» و «دیروز» ننویس. این‌ها ممنوع است: نشان‌دهنده، نشان می‌دهد، حاکی از، بیشترین، کمترین، بزرگ‌ترین، "
           "به خود اختصاص داد، رقم زد، رقم خورد، به ثبت رسید، در مجموع، در نهایت، در حالی که.\n"
           "هیچ چیز دیگری ننویس.")
HEAD = ("تو سردبیر بورس کالای ام‌اس‌دیتا هستی. برای «{market}» که متنش را می‌بینی این سه را بنویس و فقط یک JSON با کلیدهای "
        "title, subtitle, lead برگردان:\n"
        "- title: ۸ تا ۱۲ کلمه، یک جمله‌ی خبری درباره‌ی مهم‌ترین اتفاق روز، با حداکثر یک عدد.\n"
        "- subtitle: یک جمله‌ی کوتاه با نکته‌ای غیر از تیتر.\n"
        "- lead: یک جمله‌ی ۲۵ تا ۳۵ کلمه‌ای با حداکثر یک عدد که کل گزارش را می‌گوید.\n"
        "عددها فقط از حقیقت‌ها. «امروز» ننویس؛ روز و تاریخ را بنویس. واژه‌های بیشترین، کمترین، بزرگ‌ترین و نشان‌دهنده ممنوع است.")
SENT = w.STAGE_SENT
CAP = 30  # numbers in the whole report text (owner 1405-07-21; check() uses the same limit)
SPARE_HEADS = ["نگاه کلی بازار", "رقابت خریداران", "معامله‌های پرارزش", "تغییر قیمت‌ها", "کالاهای دیگر", "جزئیات بیشتر"]
FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
DOT = re.compile(r"(?<=[0-9۰-۹])[.](?=[0-9۰-۹])")
TODAY = re.compile(r"(?<![؀-ۿ])(?:امروز|دیروز)")


def norm(t):
    return DOT.sub("٫", t).translate(FA)


def dirty(s):
    return bool(TODAY.search(s)) or any(rx.search(s) for rx in markers.RES)


def write(call, fx, symmap, tables, market, check, known, note=print, tries=90):
    calls = [0]
    facts = [x for x in fx.splitlines() if x.strip()]
    day = facts[0] if facts else ""
    known = set(known)  # as check() sees them: a Latin-digit name of the facts (6070) written in Persian digits is not «known», so that sentence is cut

    def ask(system, user):
        calls[0] += 1
        try:
            raw = w.retry(lambda p: call(p, system), user)
        except w.QuotaError:
            raise
        except ValueError as e:
            note(f"  staged report: call {calls[0]} failed: {str(e)[:120]}")
            return ""
        return raw if isinstance(raw, str) else ""

    state = {"nums": 0, "while": 0}

    def clean(body, budget=6):
        """keeps only sentences that pass every rule a sentence can break; returns the paragraphs"""
        paras, used = [], 0
        for para in re.split(r"\n\s*\n", norm(body).strip()):
            keep = []
            for s in re.split(r"(?<=[.!؟])\s+", " ".join(para.split())):
                if not s or s[-1] not in ".!؟":
                    continue
                if dirty(s) and calls[0] < tries:
                    new = norm(ask(SENT, f"جمله: {s}\n\nایراد: این جمله عبارت ممنوع یا «امروز» دارد؛ بدون آن بنویس. اگر تاریخ لازم شد: {day}").strip().strip("«»\""))
                    s = new if new and "\n" not in new and new[-1] in ".!؟" else s
                nums = w.NUM.findall(s)
                n = len(markers.numbers(s))
                # the whole report may hold CAP numbers: a correct sentence that is over the budget is said again without numbers
                if n and not dirty(s) and all(x in known for x in nums) and (n > 2 or used + n > budget or state["nums"] + n > CAP) and calls[0] < tries:
                    new = norm(ask(SENT, f"جمله: {s}\n\nایراد: همین را بدون هیچ عدد و رقمی بنویس؛ فقط بگو چه شد (مثلاً «کامل فروش رفت»، «خریدار نداشت»، «گران‌تر شد»).").strip().strip("«»\""))
                    if new and "\n" not in new and new[-1] in ".!؟" and not w.NUM.search(new) and not dirty(new):
                        s, nums, n = new, [], 0
                if dirty(s) or any(x not in known for x in nums) or n > 2 or used + n > budget or state["nums"] + n > CAP:
                    continue
                if "در حالی که" in s:
                    if state["while"]:
                        continue
                    state["while"] = 1
                if re.search(r"[A-Za-z]", s) and not all(tok.strip("«»()،.؛:") in fx for tok in re.findall(r"\S*[A-Za-z]\S*", s)):
                    continue  # Latin text that is not a name from the facts
                for v in markers.OFFER_VERBS:
                    if v != "عرضه کرد":
                        s = s.replace(v, "عرضه کرد")
                used += n
                state["nums"] += n
                keep.append(s)
            paras += keep
        half = -(-len(paras) // 2)  # all kept sentences of a section become two paragraphs (the model's own breaks made 24 tiny ones)
        return [" ".join(x) for x in (paras[:half], paras[half:]) if x] if len(paras) > 3 else ([" ".join(paras)] if paras else [])

    def section(chunk, k):
        raw = ask(SECTION.format(market=market), day + "\n\nحقیقت‌های این بخش:\n" + "\n".join(chunk))
        lines = [x for x in raw.replace("\r", "").strip().split("\n")]
        head = re.sub(r"[#*«»:.،؛!؟]", "", lines[0]).strip() if lines else ""
        body = "\n".join(lines[1:]) if 0 < len(head.split()) <= 8 else raw
        if not 2 <= len(head.split()) <= 7 or dirty(head) or w.NUM.search(head) or re.search(r"[A-Za-z]", head):
            head = SPARE_HEADS[k % len(SPARE_HEADS)]
        return head, clean(body)

    n = max(1, len(facts) - 1)
    size = -(-n // 4)
    chunks = [facts[1 + i:1 + i + size] for i in range(0, n, size)]
    parts = []
    for k, ch in enumerate(chunks):
        before, best, best_state = dict(state), ("", []), dict(state)
        for _ in range(3):  # a section that came out too thin is asked again; the fullest try is kept
            if calls[0] >= tries:
                break
            state.update(before)
            head, paras = section(ch, k)
            if sum(len(p.split()) for p in paras) > sum(len(p.split()) for p in best[1]):
                best, best_state = (head, paras), dict(state)
            if sum(len(p.split()) for p in best[1]) >= 60:
                break
        state.update(best_state)
        if best[1]:
            parts.append(best)
    k = len(chunks)
    while sum(len(p.split()) for _, ps in parts for p in ps) < 430 and calls[0] < tries:  # short: one more section from the main facts
        head, paras = section(facts[1:1 + 2 * size] if k % 2 == 0 else facts[1 + size:], k)
        if paras and all(p not in [q for _, ps in parts for q in ps] for p in paras):
            parts.append((SPARE_HEADS[k % len(SPARE_HEADS)], paras))
        k += 1
        if len(parts) >= 9:
            break
    if not parts:
        raise ValueError("staged report: no section survived")
    if sum(len(ps) for _, ps in parts) + len(parts) - 1 > 17:  # check() allows 18 paragraphs, sub-headings included
        parts = [(h, [" ".join(ps)]) for h, ps in parts]
    text = "\n\n".join(parts[0][1])
    seen = set()
    for head, paras in parts[1:]:
        while head in seen:
            head = SPARE_HEADS[(SPARE_HEADS.index(head) + 1) % len(SPARE_HEADS)] if head in SPARE_HEADS else SPARE_HEADS[len(seen) % len(SPARE_HEADS)]
        seen.add(head)
        text += "\n\n" + head + "\n\n" + "\n\n".join(paras)
    # links: every name of the symbol map that the text really has; one plain sentence is added when none is there
    pairs = [x.rsplit(" = ", 1) for x in symmap.split("؛ ") if " = " in x]
    links = [{"title": nm.strip(), "symbol": sym.strip()} for nm, sym in pairs if nm.strip() in text]
    plain = [x for x in pairs if not re.search(r"[A-Za-z0-9]", x[0])] or pairs
    if not links and pairs:
        nm, sym = plain[0]
        text += f"\n\n{nm.strip()} از معامله‌های پرارزش این روز بود."
        links = [{"title": nm.strip(), "symbol": sym.strip()}]
    head3 = None
    for _ in range(5):
        if calls[0] >= tries + 5:
            break
        raw = ask(HEAD.format(market=market), day + "\n\nحقیقت‌های اصلی:\n" + "\n".join(facts[1:8]) + "\n\nمتن گزارش:\n" + text[:1500])
        m = re.search(r"\{.*\}", raw, re.S)
        d = w.loads_lenient(m.group(0)) if m else None
        if isinstance(d, dict) and all(isinstance(d.get(x), str) and d[x].strip() for x in ("title", "subtitle", "lead")):
            d = {x: norm(" ".join(d[x].split())) for x in ("title", "subtitle", "lead")}
            if not any(dirty(v) or any(y not in known for y in w.NUM.findall(v)) for v in d.values()) and len(w.NUM.findall(d["lead"])) <= 2:
                head3 = d
                break
    if not head3:  # plain and safe: built from the first facts
        strip = lambda s: re.sub(r"^F\d+\.\s*", "", s).strip()  # noqa: E731
        head3 = {"title": f"{market}، {strip(day).replace('روز:', '').strip(' .')}", "subtitle": strip(facts[1]) if len(facts) > 1 else market,
                 "lead": " ".join(strip(x) for x in facts[1:3])}
    # last guard: «امروز» that came in through a fact copied word for word (the fallback headline) is said as the day itself
    head3 = {x: re.sub(r"(?<![؀-ۿ])امروز", "این روز", v) for x, v in head3.items()}
    text = re.sub(r"(?<![؀-ۿ])امروز", "این روز", text)
    if calls[0] < tries + 8:  # stage 6 (owner 1405-07-27): the title alone with the 40 headline rules; a rejected one keeps this title
        w.TODAY_TITLES[:] = []
        h6 = w.headline6({**head3, "text": text}, "\n".join(facts), ask, note, "report")
        head3 = {x: h6[x] for x in ("title", "subtitle", "lead")}
    out = {**head3, "slug": head3["title"], "text": text, "table": list(tables)[:2], "scenarios": [], "links": links, "sources": []}
    note(f"  staged report: {calls[0]} calls, {len(text.split())} words, {state['nums']} numbers, {len(links)} links")
    return check(json.loads(json.dumps(out, ensure_ascii=False)))
