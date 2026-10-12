"""fx_repair.py - the daily «ارز و طلا» draft of a paid model is repaired sentence by sentence (owner 2026-10-12).

Gemma's four full drafts of 1405-07-20 each failed on something small and different (28 numbers, «امروز», «نرخ», a
question, 192 words). Here the draft stays and only what is wrong is touched: the code fixes the words it can, cuts or
rewrites single sentences, adds one paragraph when the text is short and brings the paragraphs to 3 or 4.
repair(call, d, check, data_json, weekday, day) -> the dict check() returned
"""
import copy
import json
import re

import markers
import report_repair as rr
import write_free as w

CAP = 20
ADD = ("تو خبرنگار بازار ارز و طلای ام‌اس‌دیتا هستی. برای خبری که متنش را می‌بینی فقط یک پاراگراف تازه‌ی ۶۰ تا ۸۰ کلمه‌ای بنویس که چیزی "
       "را بگوید که در متن نیامده. عددها فقط از «داده». حداکثر ۳ عدد و هر جمله حداکثر ۲ عدد. «قیمت» بنویس نه «نرخ». «امروز» ننویس. "
       "جمله‌ی پرسشی ننویس. جمله‌ی کلی و بی‌خبر برای پر کردن متن ننویس و نام ام‌اس‌دیتا را نیاور. فقط خودِ پاراگراف را برگردان؛ بدون تیتر، بدون JSON.")


def repair(call, d, check, data_json, weekday, day, banned=(), note=print, tries=25):
    calls = [0]
    day_line = f"روز: {weekday} {day}."

    def ask(system, user):
        if calls[0] >= tries:
            return ""
        calls[0] += 1
        try:
            raw = w.retry(lambda p: call(p, system), user)
        except w.QuotaError:
            raise
        except ValueError:
            return ""
        return rr.plain_fixes(" ".join((raw or "").split()).strip("«»\""))

    def bad_word(s):
        return next((x for x in banned if x in s), None) or next((rx.search(s).group(0).strip() for rx in markers.RES if rx.search(s)), None)

    # content agent 2026-10-13: the model wrote «۲۰ تیر» three times in the news of 20 Mehr -> the month comes from the data
    jd, mon = day.split()
    other = re.compile(r"([۰-۹0-9]{1,2})\s+(" + "|".join(m for m in markers.MONTHS.split("|") if m != mon) + r")(?![؀-ۿ])")
    month = lambda t: other.sub(lambda m: f"{m.group(1)} {mon}" if int(m.group(1)) in (int(jd), int(jd) - 1) else m.group(0), t)  # noqa: E731
    seen = [False]
    for k in ("title", "subtitle", "lead", "text"):
        if isinstance(d.get(k), str):
            t = rr.plain_fixes(d[k].replace("\r", "")).replace("نرخ", "قیمت")  # owner 1405-07-16: «قیمت», never «نرخ»
            t = "\n".join(w.readable(x) for x in w.yesterday_words(month(t), day_line).split("\n"))
            d[k] = w.today_words(t, day_line, seen if k in ("lead", "text") else None)
    if isinstance(d.get("table"), list):  # readable Persian numbers in the table too
        d["table"] = [{w.readable(str(a)): w.readable(str(b)) for a, b in r.items()} for r in d["table"] if isinstance(r, dict)]
    state = {"nums": 0}

    def clean(text):
        out = []
        for para in re.split(r"\n\s*\n", text.strip()):
            keep = []
            for s in re.split(r"(?<=[.!؟?])\s+", " ".join(para.split())):
                if not s or "؟" in s or "?" in s:
                    continue
                n = len(markers.numbers(s))
                hit = bad_word(s)
                if hit or n > 2:
                    why = (f"عبارت «{hit}» ممنوع است؛ بدون آن بنویس." if hit else "این جمله بیش از ۲ عدد دارد؛ آن را به شکلی بنویس که فقط ۲ عدد مهم‌تر بماند.")
                    new = ask(rr.SENT, f"جمله: {s}\n\nایراد: {why}")
                    if new and new[-1] in ".!" and not bad_word(new) and len(markers.numbers(new)) <= 2 and not set(w.NUM.findall(new)) - set(w.NUM.findall(s)):
                        s, n = new, len(markers.numbers(new))
                    else:
                        continue
                if state["nums"] + n > CAP:
                    continue
                state["nums"] += n
                keep.append(s)
            if keep:
                out.append(" ".join(keep))
        return out

    paras = clean(d["text"])
    for _ in range(3):  # short: one more paragraph from the data
        if sum(len(p.split()) for p in paras) >= 205 or calls[0] >= tries:
            break
        extra = clean(ask(ADD, "داده:\n" + data_json + "\n\nمتن فعلی:\n" + "\n\n".join(paras)))
        paras += [p for p in extra if p not in paras]
    while len(paras) > 4:  # 3 or 4 paragraphs
        i = min(range(len(paras) - 1), key=lambda j: len(paras[j].split()) + len(paras[j + 1].split()))
        paras[i:i + 2] = [paras[i] + " " + paras[i + 1]]
    while 0 < len(paras) < 3:
        i = max(range(len(paras)), key=lambda j: len(paras[j].split()))
        ss = re.split(r"(?<=[.!])\s+", paras[i])
        if len(ss) < 2:
            break
        paras[i:i + 1] = [" ".join(ss[:len(ss) // 2]), " ".join(ss[len(ss) // 2:])]
    d["text"] = "\n\n".join(paras)
    for k in ("title", "subtitle", "lead"):
        if bad_word(d[k]) or "؟" in d[k]:
            new = ask(rr.SENT, f"جمله: {d[k]}\n\nایراد: عبارت ممنوع یا پرسش دارد؛ ساده و خبری بنویس.")
            if new and not bad_word(new) and "؟" not in new:
                d[k] = new.rstrip(".") if k == "title" else new
    for k in ("title", "subtitle", "lead", "text"):  # last guard: «امروز» that came back in a rewritten sentence or the added paragraph (also «امروزه»)
        d[k] = re.sub(r"(?<![؀-ۿ])امروز[هی]?(?![؀-ۿ])", weekday, w.today_words(d[k], day_line, seen))
    note(f"  fx repair: {calls[0]} calls, {len(d['text'].split())} words, {state['nums']} numbers, {len(paras)} paragraphs")
    return check(copy.deepcopy(json.loads(json.dumps(d, ensure_ascii=False))))
