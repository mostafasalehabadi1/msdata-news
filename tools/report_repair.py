"""report_repair.py - a full report draft is repaired sentence by sentence instead of being written again (owner 2026-10-12).

Before this, one «امروز» or one banned word sent the whole 600-word report back to the model, and the new text failed on
something else. Now the draft stays: the code fixes what needs no model (the word «امروز», «۱ هزار», a stray half-space,
Latin digits), cuts a sentence whose number is not in the facts, and asks the model to rewrite only the single sentence
that still breaks a rule. A sentence that cannot be repaired is cut. The result must still pass write_report.check().
today_words(text, day_line) is also used by the symbol news (write_free.parse).
repair(call, d, check, known, fx, symmap, cap) -> the dict check() returned
"""
import copy
import json
import re

import markers
import write_free as w

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
DOT = re.compile(r"(?<=[0-9۰-۹])[.](?=[0-9۰-۹])")
LATIN = markers.RES[8] if len(markers.RES) > 8 else re.compile(r"[A-Z]{2,}[-\d.]+[A-Z\d.-]*")
ZWNJ_BAD = re.compile(r"([ادذرزژو])‌(?!گو|جو)")
SENT = ("تو ویراستار هستی. یک جمله از گزارش بازار و ایراد آن را می‌گیری. همان جمله را با همان معنا، همان نام‌ها و همان عددها دوباره بنویس تا "
        "ایراد رفع شود. فعل ساده به کار ببر (فروخت، خرید، رسید، بود، است). فقط خودِ جمله‌ی تازه را برگردان؛ بدون توضیح، بدون گیومه و بدون JSON.")


day_parts, today_words = w.day_parts, w.today_words


def plain_fixes(t):
    t = DOT.sub("٫", t).translate(FA)
    t = ZWNJ_BAD.sub(r"\1", t)
    t = re.sub(r"(?<![0-9۰-۹٫])۱ (هزار|میلیون|میلیارد)", lambda m: ("یک " if m.group(1) != "هزار" else "") + m.group(1), t)
    return t.replace("می‌شود".replace("‌", ""), "می‌شود").replace("می‌کند".replace("‌", ""), "می‌کند")


def dirty(s):
    return any(rx.search(s) for rx in markers.RES)


def repair(call, d, check, known, fx, symmap, cap=30, note=print, tries=40):
    facts = [x for x in fx.splitlines() if x.strip()]
    day = facts[0] if facts else ""
    calls = [0]

    def ask(s, problem):
        if calls[0] >= tries:
            return ""
        calls[0] += 1
        try:
            raw = w.retry(lambda p: call(p, SENT), f"جمله: {s}\n\nایراد: {problem}")
        except w.QuotaError:
            raise
        except ValueError:
            return ""
        new = plain_fixes(" ".join((raw or "").split()).strip("«»\""))
        return new if new and new[-1] in ".!؟" else ""

    seen = [False]
    for k in ("title", "subtitle", "lead", "text"):
        if isinstance(d.get(k), str):
            d[k] = today_words(plain_fixes(d[k].replace("\r", "")), day, seen if k == "text" else None)
    known = set(known)
    stats = {"cut": 0, "fixed": 0, "nums": 0, "whiles": 0}

    def sentence_ok(s, budget_left):
        nums = w.NUM.findall(s)
        return not dirty(s) and all(n in known for n in nums) and len(markers.numbers(s)) <= 2 and len(markers.numbers(s)) <= budget_left

    def fix_text(text):
        out = []
        for para in re.split(r"\n\s*\n", text.strip()):
            if "\n" not in para.strip() and len(para.split()) <= 8 and not re.search(r"[.!؟?:،]$", para.strip()):
                out.append(para.strip() if not dirty(para) else "جزئیات معامله‌ها")  # a sub-heading
                continue
            keep = []
            for s in re.split(r"(?<=[.!؟])\s+", " ".join(para.split())):
                if not s:
                    continue
                for attempt in range(2):
                    if sentence_ok(s, cap - stats["nums"]):
                        break
                    hit = next((rx.search(s) for rx in markers.RES if rx.search(s)), None)
                    bad = [n for n in w.NUM.findall(s) if n not in known]
                    if bad:
                        s = ""  # an invented number is never «corrected»: the sentence goes
                        break
                    why = (f"عبارت «{hit.group(0).strip()}» ممنوع است؛ بدون آن بنویس (کد لاتین کالا را حذف کن و فقط نام فارسی را بگو)." if hit
                           else "این جمله بیش از ۲ عدد دارد؛ فقط ۲ عدد مهم‌تر را نگه دار.")
                    new = ask(s, why)
                    if not new or set(w.NUM.findall(new)) - set(w.NUM.findall(s)):
                        s = ""
                        break
                    s = new
                    stats["fixed"] += 1
                if not s or not sentence_ok(s, cap - stats["nums"]) or ("در حالی که" in s and stats["whiles"]):
                    stats["cut"] += 1
                    continue
                stats["whiles"] += "در حالی که" in s
                stats["nums"] += len(markers.numbers(s))
                keep.append(s)
            if keep:
                out.append(" ".join(keep))
        return "\n\n".join(out)

    d["text"] = fix_text(d["text"])
    # a draft that came out short (or lost sentences above) grows by whole new sections from facts it has not used yet
    import report_staged
    for k in range(5):
        if len(d["text"].split()) >= 440 or calls[0] >= tries:
            break
        unused = [f for f in facts[1:] if not any(n in d["text"] for n in w.NUM.findall(f)[:3] if len(n) > 1)] or facts[1:]
        calls[0] += 1
        try:
            raw = w.retry(lambda p: call(p, report_staged.SECTION.format(market="گزارش پایان روز بورس کالا")),
                          day + "\n\nحقیقت‌های این بخش:\n" + "\n".join(unused[k % 2::2][:9] or unused[:9]))
        except w.QuotaError:
            raise
        except ValueError:
            continue
        lines = (raw or "").replace("\r", "").strip().split("\n")
        head = re.sub(r"[#*«»:.،؛!؟]", "", lines[0]).strip() if lines else ""
        if not 2 <= len(head.split()) <= 7 or dirty(head) or w.NUM.search(head) or head in d["text"]:
            head = report_staged.SPARE_HEADS[k % len(report_staged.SPARE_HEADS)]
        body = fix_text(today_words(plain_fixes("\n".join(lines[1:])), day, seen))
        body = "\n\n".join(x for x in body.split("\n\n") if x not in d["text"])
        if len(body.split()) >= 30:
            d["text"] += "\n\n" + head + "\n\n" + body
    paras = d["text"].split("\n\n")
    if len(paras) > 17:  # check() allows 18 paragraphs, sub-headings included: body paragraphs under one heading are joined
        merged = []
        for x in paras:
            is_head = len(x.split()) <= 8 and not re.search(r"[.!؟?:،]$", x.strip())
            if merged and not is_head and not merged[-1][0]:
                merged[-1] = (False, merged[-1][1] + " " + x)
            else:
                merged.append((is_head, x))
        d["text"] = "\n\n".join(x for _, x in merged)
    for k in ("title", "subtitle", "lead"):
        if dirty(d[k]) or any(n not in known for n in w.NUM.findall(d[k])):
            new = ask(d[k], "این جمله عبارت ممنوع یا عدد نادرست دارد؛ ساده و بدون آن بنویس.")
            if new and not dirty(new) and all(n in known for n in w.NUM.findall(new)):
                d[k] = new.rstrip(".") if k == "title" else new
    # links: kept when the model's own are valid, otherwise built from the names of the symbol map found in the text
    pairs = [x.rsplit(" = ", 1) for x in symmap.split("؛ ") if " = " in x]
    links = [x for x in d.get("links") or [] if isinstance(x, dict) and x.get("title") and x["title"] in d["text"]]
    links += [{"title": nm.strip(), "symbol": sym.strip()} for nm, sym in pairs if nm.strip() in d["text"] and nm.strip() not in [x["title"] for x in links]]
    if not links and pairs:
        nm, sym = ([x for x in pairs if not re.search(r"[A-Za-z0-9]", x[0])] or pairs)[0]
        d["text"] += f"\n\n{nm.strip()} از معامله‌های پرارزش این روز بود."
        links = [{"title": nm.strip(), "symbol": sym.strip()}]
    d["links"] = links
    note(f"  report repair: {calls[0]} sentence calls, {stats['fixed']} rewritten, {stats['cut']} cut, {len(d['text'].split())} words")
    return check(copy.deepcopy(json.loads(json.dumps(d, ensure_ascii=False))))
