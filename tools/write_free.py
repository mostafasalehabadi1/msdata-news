"""write_free.py - writes symbol news with free LLMs (Gemini free tier + OpenRouter :free models), no Claude.

For each symbol in data/<date>/today.json that has no news yet, ask the providers in order; the first answer
that passes the same checks as tools/validate.py is kept, otherwise the next model is tried. Symbols that fail
on every model stay in the queue for the next run.

env (any subset): GITHUB_TOKEN (GitHub Models, free in Actions), GEMINI_API_KEY, OPENROUTER_API_KEY,
GROQ_API_KEY, MISTRAL_API_KEY.
usage: python tools/write_free.py [--limit N] [--out news]
"""
import signal
import random
import argparse
import datetime as dt
import inspect
import json
import os
import re
import zlib
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import FORBIDDEN, SLUG_BAD, paragraphs, words  # noqa: E402
import facts_kala  # noqa: E402
import markers  # noqa: E402
import importance  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
GH_URL = "https://models.github.ai/inference/chat/completions"
GH_AZURE_URL = "https://models.inference.ai.azure.com/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
GROQ_PREFER = ("kimi-k2", "gpt-oss-120b", "llama-4-maverick", "qwen3", "llama-3.3-70b", "llama-4-scout", "gpt-oss-20b")
OR_PREFER = ("nemotron-3-ultra", "gemma-4-31b", "nemotron-3-ultra", "deepseek", "qwen", "gemma", "llama")  # better Persian first

# content agent 1405-07-27 (owner 1405-07-19): the 40 headline principles go with every writer, beside style.md
HEADLINES = "\n\n" + open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "headline_rules_40.md"), encoding="utf-8").read()
STYLE_FULL = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.md"), encoding="utf-8").read()


def style_for(*templates):
    """style.md without what the model does not need (section 6 is for the programmer, other templates are unused),
    so the prompt stays under the free tiers' tokens-per-minute limit (Gemma free tier answered 429 on the full file)."""
    parts = re.split(r"(?m)^(?=##+ )", STYLE_FULL)
    keep = []
    for p in parts:
        head = p.split("\n", 1)[0]
        if head.startswith("## بخش ۶") or head.startswith("## بخش ۴"):
            continue
        if head.startswith("### قالب ") and not any(head.startswith("### قالب " + t) for t in templates):
            continue
        keep.append(p)
    return "".join(keep)


STYLE = style_for("الف")
# Gemma's free tier caps input tokens per minute; it gets the same rules in short form (style_compact.md) + the templates
STYLE_COMPACT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "style_compact.md"), encoding="utf-8").read()


def compact_for(*templates):
    return STYLE_COMPACT + "\n" + "".join(p for p in re.split(r"(?m)^(?=##+ )", STYLE_FULL)
                                          if any(p.startswith("### قالب " + t) for t in templates))


RULES_GEMMA = None  # set by a writer: the system prompt for gemma-* models (short style)
# content agent 1405-08-01 (owner approved): item news (kala, kish, energy) get only style_kala.md v3 + the 40 headline rules
STYLE_KALA = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "style_kala.md"), encoding="utf-8").read()
# symbol news (owner 2026-10-05): the old template without clichés; every fact and table is built in code (facts_kala.py)
STABLE_MIN = 20  # Kish: the hall is «closed» once no row changed for 20 minutes (a session lasts ~12 min, e.g. 11:30-11:42) (owner 1405-07-15: write only after all trades)
# «در یک نگاه» box of the news page (designer + content agent, owner 1405-07-21): optional, never a reason to reject a news
KEY_POINTS = ("key_points آرایه‌ای از دقیقاً ۳ جمله‌ی کوتاه است (هر کدام کمتر از ۱۵ کلمه) که مهم‌ترین نکته‌های همین خبر را "
              "فقط با عددهای خود خبر می‌گوید. ")
MIN_WORDS = [120]  # owner 1405-07-15: 120 words minimum for all physical and Kish news (same as validate.py)
RULES = ("تو خبرنگار بورس کالای msdata.ir هستی و برای یک نماد بازار فیزیکی بورس کالا یک خبر فارسی می‌نویسی.\n\n" + STYLE_KALA + HEADLINES +
         "\n\nفقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text, table, key_points و هیچ متن دیگری. " + KEY_POINTS +
         "slug فارسی با خط تیره، بدون فاصله و علامت. table فقط شناسه‌ی جدول انتخابی (مثلاً \"T2\").")
STYLE_KISH = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "style_kish.md"), encoding="utf-8").read()
RULES_KISH = ("تو خبرنگار بورس کالای msdata.ir هستی و برای یک معامله‌ی تالار صادراتی بورس کالا یک خبر فارسی می‌نویسی.\n\n" + STYLE_KALA + HEADLINES +
              "\n\nفقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text, table, key_points و هیچ متن دیگری. " + KEY_POINTS +
              "slug فارسی با خط تیره، بدون فاصله و علامت. table فقط شناسه‌ی جدول انتخابی (مثلاً \"T2\").")
STYLE_ENERGY = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "style_energy.md"), encoding="utf-8").read()
RULES_ENERGY = ("تو خبرنگار بورس انرژی msdata.ir هستی و برای یک معامله‌ی بورس انرژی ایران یک خبر فارسی می‌نویسی.\n\n" + STYLE_KALA + HEADLINES +
                "\n\nفقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text, table, key_points و هیچ متن دیگری. " + KEY_POINTS +
                "slug فارسی با خط تیره، بدون فاصله و علامت. table فقط شناسه‌ی جدول انتخابی (مثلاً \"T2\").")


# content agent 1405-07-15 (252 news of 1405-07-14): the one main flaw of each model, appended to its prompt
MODEL_HINTS = [(r"gapgpt:gemma", "۱) همه‌ی رقم‌ها و اعشارها فارسی و با «٫» (۷٫۲ نه 7.2)؛ در تیتر، متن و key_points. ۲) «امروز» و «دیروز» را هیچ‌جا ننویس، در key_points هم نه؛ تاریخ فقط یک بار (مثل دوشنبه ۲۰ مهر)، و لید با روز یا تاریخ شروع نشود. ۳) هر عدد را فقط یک بار و دقیقاً از حقیقت‌ها بنویس؛ عدد صفر یا عددی که در حقیقت‌ها نیست ننویس و ارزش معامله را خودت حساب نکن. ۴) text دست‌کم ۱۴۰ کلمه در ۲ یا ۳ پاراگراف باشد و پاراگراف‌ها با یک خط خالی جدا شوند؛ حداکثر ۶ عدد در متن داشته باشد. ۵) هیچ حرف لاتین یا کد کالا (مثل نام گرید) در تیتر و متن نیاور؛ نام فارسی کالا کافی است. ۶) در تیتر «؛» نگذار و «نماد» ننویس. ۷) تیتر جمله‌ی کامل با فعل است، ۸ تا ۱۳ کلمه. ۸) نسبت تقاضا به عرضه را فقط به شکل «X برابر عرضه» بنویس؛ «X درصد بیشتر از عرضه» ننویس. ۹) صفت عالی (بالاترین، پایین‌ترین، بیشترین، کمترین، گران‌ترین، ارزان‌ترین) ممنوع است، مگر همان واژه در حقیقت‌ها آمده باشد."),
               (r"^zai:", "text حتماً دست‌کم ۱۳۰ کلمه باشد (کمتر از ۱۲۰ کلمه رد می‌شود)، در ۲ یا ۳ پاراگراف با یک خط خالی بین آن‌ها؛ پیش از برگرداندن کلمه‌ها را بشمار."),  # content agent 1405-08-04: 15 zai drafts were 76-91 words
               (r"nemotron-3-super", "کلمه‌ی انگلیسی ننویس. «عرض» ننویس؛ «عرضه» بنویس."),
               (r"dots", "هیچ کلمه‌ی انگلیسی مثل Display یا faced در متن نیاور."),
               (r"command-a.*reason", "«امروز» ننویس؛ روز هفته و تاریخ را بنویس. «در حالی که» و «با این حال» ننویس."),
               (r"command-a", "«امروز» ننویس؛ روز هفته و تاریخ را بنویس."),
               (r"step-3\.7", "لید حداکثر ۳۰ کلمه."),
               (r"glm-4\.5-flash|apodex|gpt-oss|ling|command-r", "متن کمتر از ۱۴۰ کلمه نباشد."),
               (r"nemotron-3-ultra", "«نشان‌دهنده» و «منجر شد» ننویس.")]


def model_hint(name):
    for pat, h in MODEL_HINTS:
        if re.search(pat, name, re.I):
            return "\n\nنکته‌ی مخصوص تو: " + h
    return ""


# one opening/structure pattern per news, drawn at random (content agent 1405-07-15). 7 (half-sold) and 10 (bag price)
# need data checks the code does not make yet, so they are left out
PATTERNS = ["تضاد: اول نکته‌ی متناقض (مثلاً «تقاضا چند برابر شد، اما قیمت پایین آمد»)، بعد عددها.",
            "روایت زمانی: از گذشته شروع کن («سه هفته پیش …») و به روز معامله برس.",
            "جمله‌ی خیلی کوتاه اول متن (مثلاً «رقابت سخت بود.») و بعد توضیح.",
            "پرسش در لید یا تیتر و پاسخ در همان پاراگراف اول.",
            "مقایسه‌ی دو تولیدکننده‌ی همان کالا، اگر در حقیقت‌ها هست؛ وگرنه مقایسه با معامله‌ی قبلی همین کالا.",
            "از خریدار شروع کن: چقدر خواستند و چقدر گیرشان آمد.",
            "فاصله‌ی قیمت از قیمت پایه را در جمله‌ی اول بگو.",
            "اگر در حقیقت‌ها بیشترین یا کمترین قیمت از یک تاریخ هست، همان را در جمله‌ی اول بگو؛ وگرنه از رقابت شروع کن."]


# the source sentence (owner 1405-07-15): «ام‌اس‌دیتا» exactly once, mid or end of the text, never title/lead/first sentence.
# content agent 1405-07-22: 20 complete sentences (no blanks for the model to fill with a made-up number), one per news,
# chosen by symbol+day so a rerun gives the same one; the code puts it in when the model leaves it out.
SOURCE_LINES = ["این ارقام از داده‌های روزانه‌ی ام‌اس‌دیتا از معاملات بورس کالا استخراج شده است.",
                "ام‌اس‌دیتا معاملات این کالا را روز به روز ثبت و با جلسه‌های قبل مقایسه می‌کند.",
                "مقایسه‌ی قیمت با معامله‌های قبلی بر پایه‌ی داده‌های ام‌اس‌دیتا است.",
                "سابقه‌ی قیمت این کالا در پایگاه داده‌ی ام‌اس‌دیتا در دسترس است.",
                "ام‌اس‌دیتا روند قیمت و حجم این کالا را از نخستین معامله‌ی ثبت‌شده دنبال می‌کند.",
                "محاسبه‌ی تغییر قیمت و نسبت تقاضا به عرضه را ام‌اس‌دیتا انجام داده است.",
                "داده‌های این معامله را ام‌اس‌دیتا از گزارش رسمی معاملات بورس کالا گردآوری کرده است.",
                "جزئیات معامله‌های قبلی این کالا در صفحه‌ی آن در ام‌اس‌دیتا آمده است.",
                "طبق داده‌های ام‌اس‌دیتا، این مقایسه با آخرین معامله‌ی همین تولیدکننده انجام شده است.",
                "ام‌اس‌دیتا قیمت این کالا را در کنار دیگر فروشندگان همین جلسه مقایسه کرده است.",
                "نمودار روند قیمت این کالا در ام‌اس‌دیتا هر روز به‌روز می‌شود.",
                "بالاترین و پایین‌ترین قیمت‌های این کالا از داده‌های ثبت‌شده‌ی ام‌اس‌دیتا آمده است.",
                "این گزارش بر پایه‌ی آمار معاملات روزانه‌ای است که ام‌اس‌دیتا منتشر می‌کند.",
                "ارزش معامله‌های امسال این کالا را ام‌اس‌دیتا از جمع معاملات ثبت‌شده حساب کرده است.",
                "ام‌اس‌دیتا همه‌ی عرضه‌های این تولیدکننده را در بورس کالا ثبت می‌کند.",
                "داده‌ی عرضه، تقاضا و قیمت این معامله از آرشیو ام‌اس‌دیتا گرفته شده است.",
                "به استناد داده‌های ام‌اس‌دیتا، این معامله با جلسه‌های پیشین همین کالا سنجیده شده است.",
                "در آرشیو ام‌اس‌دیتا، معامله‌های این کالا به تفکیک تولیدکننده ثبت شده است.",
                "ام‌اس‌دیتا داده‌های این تالار را هر روز پس از پایان معاملات جمع‌آوری می‌کند.",
                "خوانندگان می‌توانند سابقه‌ی کامل معاملات این کالا را در ام‌اس‌دیتا ببینند."]
MARKET = ["kala"]  # set by main(): sentences 1, 7 and 13 name the energy exchange / the export hall instead


FIRST_TRADE = [False]  # set by staged(): the facts say this is the first trade on record
NOT_FIRST = {2, 8, 16}  # content agent 1405-07-23: they talk about earlier sessions
HAS_YTD = [True]  # set by staged(): sentence 13 names this year's value, only when the facts have it (content agent 1405-07-25)


def source_pick(key):
    pool = [x for i, x in enumerate(SOURCE_LINES) if not (FIRST_TRADE[0] and i in NOT_FIRST) and not (i == 13 and not HAS_YTD[0])]
    line = pool[zlib.crc32(key.encode("utf-8")) % len(pool)]
    if MARKET[0] == "energy":
        return line.replace("بورس کالا", "بورس انرژی")
    if MARKET[0] == "kish":
        return line.replace("معاملات بورس کالا", "معاملات تالار صادراتی بورس کالا").replace("گزارش رسمی معاملات بورس کالا", "گزارش رسمی تالار صادراتی بورس کالا")
    return line


def source_line(key=""):
    return ("\n\nمنبع: این جمله را عیناً و فقط یک بار در وسط یا آخر text بیاور (نه در title، نه در lead، نه در جمله‌ی اول text): «"
            + (source_pick(key) if key else random.choice(SOURCE_LINES)) + "» جمله‌ی دیگری با «ام‌اس‌دیتا» ننویس.")


def pattern_line():
    return ("\n\nالگوی این خبر: " + random.choice(PATTERNS) +
            " پاراگراف آخر یک واقعیت باشد، نه پیش‌بینی؛ و دست‌کم یکی از این‌ها در متن باشد: «اما»، یک جمله‌ی زیر هشت کلمه، یا پاراگراف تک‌جمله‌ای.")


def post(url, key, model, prompt, extra=None, system=None):
    body = {"model": model, "temperature": 0.7,
            "messages": [{"role": "system", "content": system or (RULES_GEMMA if RULES_GEMMA and model.startswith("gemma") else RULES)},
                         {"role": "user", "content": prompt}]}
    if "z.ai" in url:
        body["thinking"] = {"type": "disabled"}  # GLM thinks by default: slow (timeouts) and it spends the answer budget on reasoning
    if "gapgpt.app" in url and "deepseek" in model:
        body["reasoning"] = {"enabled": False}  # 2026-10-12: with thinking it spent 1400-4900 tokens and 25-140s per news and still wrote short; off = 3s
    if "gapgpt.app" in url and model.startswith("gemma"):
        body["max_tokens"] = 4096  # content agent 1405-07-27 (owner delegated): back to 4096, no cap showed no gain
    if "cloudflare.com" in url:
        body["max_tokens"] = 2048  # Cloudflare cuts answers at a 256-token default; a cap elsewhere starves reasoning models
    auth = {"Authorization": f"Bearer {key}"} if key else {}  # OVH AI Endpoints works anonymously (2 req/min per model)
    req = urllib.request.Request(url, json.dumps(body).encode(), {
        **auth, "Content-Type": "application/json", "User-Agent": "msdata-news/1.0", **(extra or {})})
    try:
        with urllib.request.urlopen(req, timeout=600 if model.startswith("gemma") else 180) as r:  # gemma writes a long report slowly
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise ValueError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:1500]}") from None
    try:
        return json.loads(raw)["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        raise ValueError(f"bad response: {raw[:200]!r}") from None


STRONG_GEMINI = ("gemma-4-31b-it",)
# gemma-4-31b-it is kept for the daily report only (write_report.py) and writes no symbol news
REPORT_ONLY = {"gemini:gemma-4-31b-it"}
STRONG = {"llm7:deepseek-v4-flash:0731", "cohere:command-a-03-2025", "kilo:dots-studio/dots-3-note-preview:free",
          "hf:deepseek-ai/DeepSeek-V3.1"}  # kilo:stepfun/step-3.7-flash:free removed 2026-10-10: Kilo answers 404 (model no longer exists)
# the next three by rating: write the important half too, but only when every strong model is out or a symbol waited 2h
# ovh:Qwen3.5 added by the owner on 2026-10-10: every strong/backup model was out of quota and 30 important symbols stayed unwritten
BACKUP = {"zai:glm-4.7-flash", "zai:glm-4.5-flash", "kilo:nvidia/nemotron-3-ultra-550b-a55b:free", "cf:@cf/meta/llama-3.3-70b-instruct-fp8-fast",
          "ovh:Qwen3.5-397B-A17B", "arad:deepseek-v4-flash-0423"}
BACKUP_WAIT = 2 * 3600
# owner 2026-10-12: a paid model writes a symbol (any tier) only after the free models had 10 minutes for it and it is still unwritten
PAID = {"gapgpt:gemma-3-27b-it", "gapgpt:deepseek-v4-flash"}
PAID_WAIT = 600
PAID_STOP = 10  # owner 1405-08-03: 10 symbols in a row with no accepted news stop the paid model for the run («هزینه بالا برای هیچ کار»)
GIVE_UP = 2  # failed runs before a symbol waits for new data (backend 1405-07-30)
DEEPSEEK_ON = False  # owner 2026-10-12: GapGPT deepseek-v4-flash stays ready but writes nothing until this is True


def providers():
    """Ordered fallback chain: best free model first; when one fails or runs out, the next one is used."""
    out = []
    if os.environ.get("GEMINI_API_KEY"):
        # every Gemini model has its own daily free quota, so use all text models the key can see
        gem = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
        try:
            url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=" + os.environ["GEMINI_API_KEY"]
            with urllib.request.urlopen(url, timeout=60) as r:
                seen = [m["name"].split("/", 1)[1] for m in json.load(r).get("models", [])
                        if "generateContent" in m.get("supportedGenerationMethods", [])]
            seen = [m for m in seen if re.match(r"^gem(ini|ma)-", m) and not re.search(r"embed|image|tts|audio|live|vision|exp|preview|-2\.5-", m)]
            gem = list(dict.fromkeys(gem + seen))
        except Exception as e:  # noqa: BLE001 - listing is optional; the two known models still work
            note(f"gemini model list failed: {e}")

        def quality(m):  # best Persian prose first: pro > flash > lite, newer version first
            v = re.search(r"(\d+(?:\.\d+)?)", m)
            tier = 3 if "lite" in m else 0 if "pro" in m else 1 if "flash" in m else 2
            return (tier, -(float(v.group(1)) if v else 99.0))
        gem.sort(key=quality)
        for m in list(dict.fromkeys(gem[:10] + [x for x in gem if x in STRONG_GEMINI])):
            out.append(("gemini:" + m, lambda p, m=m: post(GEMINI_URL, os.environ["GEMINI_API_KEY"], m, p)))
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=60) as r:
            free = [m["id"] for m in json.load(r)["data"] if m["id"].endswith(":free") and "qwen3.8" not in m["id"]]  # qwen3.8 wrote nonsense Persian
        free.sort(key=lambda i: next((n for n, w in enumerate(OR_PREFER) if w in i), 99))
        for m in free[:6]:
            out.append(("openrouter:" + m, lambda p, m=m, key=key: post(OR_URL, key, m, p, {"X-Title": "msdata-news"})))
    key = os.environ.get("GROQ_API_KEY")
    if key:  # Groq retires model ids often (old ones gave 404), so take the live list: text models only, best Persian first
        try:
            req = urllib.request.Request("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}", "User-Agent": "msdata-news/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                ids = [m["id"] for m in json.load(r)["data"] if m.get("active", True)]
            ids = [i for i in ids if not re.search(r"whisper|guard|tts|orpheus|playai|compound|safeguard|distil|qwen3.8|allam", i, re.I)]
            ids.sort(key=lambda i: next((n for n, w in enumerate(GROQ_PREFER) if w in i), 99))
            note("groq models: " + ", ".join(ids))
            for m in ids[:4]:
                out.append(("groq:" + m, lambda p, m=m, key=key: post(GROQ_URL, key, m, p)))
        except Exception as e:  # noqa: BLE001
            note(f"groq model list failed: {e}")
    for env, url, models in (
                             ("MISTRAL_API_KEY", MISTRAL_URL, ("mistral-large-latest", "mistral-medium-latest")),
                             ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1/chat/completions", ("qwen-3-235b-a22b-instruct-2507", "llama-3.3-70b")),
                             ("ZAI_API_KEY", "https://api.z.ai/api/paas/v4/chat/completions", ("glm-4.7-flash", "glm-4.5-flash")),
                             ("HF_TOKEN", "https://router.huggingface.co/v1/chat/completions", ("deepseek-ai/DeepSeek-V3.1", "Qwen/Qwen3-235B-A22B-Instruct-2507")),
                             ("CF_API_TOKEN", f"https://api.cloudflare.com/client/v4/accounts/{os.environ.get('CF_ACCOUNT_ID', '')}/ai/v1/chat/completions",
                              ("@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/qwen/qwen2.5-coder-32b-instruct")),
                             ("NVIDIA_API_KEY","https://integrate.api.nvidia.com/v1/chat/completions", ("deepseek-ai/deepseek-v3.1", "qwen/qwen3-235b-a22b", "meta/llama-3.3-70b-instruct")),
                             ("LLM7_API_KEY", "https://api.llm7.io/v1/chat/completions", ("deepseek-v4-flash:0731", "minimax-m2.7", "mistral-Nemo-Instruct-2407")),
                             ("COHERE_API_KEY", "https://api.cohere.ai/compatibility/v1/chat/completions", ("command-a-03-2025", "command-a-reasoning-08-2025", "command-r-plus-08-2024"))):
        if os.environ.get(env):
            for m in models:
                out.append((env.split("_")[0].lower() + ":" + m, lambda p, m=m, u=url, e=env: post(u, os.environ[e], m, p)))
    if os.environ.get("GAPGPT_API_KEY"):  # GapGPT gateway (paid, owner 2026-10-12): gemma wrote 2 of 3 test news in ~12s; deepseek-v4-flash wrote 0 of 3 and is off (owner: gemma only for now)
        # deepseek is wired but off: set DEEPSEEK_ON = True below to let it write
        for m in ("gemma-3-27b-it",) + (("deepseek-v4-flash",) if DEEPSEEK_ON else ()):
            out.append(("gapgpt:" + m, lambda p, s=None, m=m: post("https://api.gapgpt.app/v1/chat/completions", os.environ["GAPGPT_API_KEY"], m, p, system=s)))
    if os.environ.get("ARAD_API_KEY"):  # Arad AI gateway (paid, owner 2026-10-12); the key is tied to DeepSeek V4 Flash 0423
        out.append(("arad:deepseek-v4-flash-0423", lambda p: post("https://llm.aradai.ir/v1/chat/completions", os.environ["ARAD_API_KEY"],
                                                                  "arad/model-1624c3b5a0d4c57211c492845a9f7bb1", p)))
    for m in ("Qwen3.5-397B-A17B", "gpt-oss-120b", "Meta-Llama-3_3-70B-Instruct"):  # OVH: no signup, no key
        out.append(("ovh:" + m, lambda p, m=m: post("https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions", "", m, p)))
    for m in ("dots-studio/dots-3-note-preview:free", "nvidia/nemotron-3-ultra-550b-a55b:free"):  # Kilo gateway: no signup, 200 req/h per IP
        out.append(("kilo:" + m, lambda p, m=m: post("https://api.kilo.ai/api/gateway/chat/completions", "", m, p)))
    # added 2026-10-06 after the 20-model bench (passed or only rate-limited); LLM7 big models were paid-only (402) and dropped
    # 1405-08-04: ling-3.1-flash and inkling-small are gone from the Kilo model list (404 all night)
    for m in ("kilo-auto/free", "nvidia/nemotron-3-super-120b-a12b:free", "poolside/laguna-s-2.1:free", "cohere/north-mini-code:free"):
        out.append(("kilo:" + m, lambda p, m=m: post("https://api.kilo.ai/api/gateway/chat/completions", "", m, p)))
    for m in ("Qwen3.6-27B", "Mistral-Small-3.2-24B-Instruct-2506", "Qwen3-Coder-30B-A3B-Instruct"):
        out.append(("ovh:" + m, lambda p, m=m: post("https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions", "", m, p)))
    if os.environ.get("REQUESTY_API_KEY"):  # Requesty: free models, 200 req/day, no card (signed up 2026-10-06); 1 concurrent request, the per-provider worker keeps it serial
        for m in ("mistral/leanstral-1-5", "novita/inclusionai/ling-3.0-tiny", "poolside/laguna-m.1",
              "nvidia/nemotron-3-nano-30b-a3b", "nvidia/muse-glimmer-30b"):
            out.append(("requesty:" + m, lambda p, m=m: post("https://router.requesty.ai/v1/chat/completions", os.environ["REQUESTY_API_KEY"], m, p)))
    if os.environ.get("BENCH_NEW"):  # 3rd batch of candidates (+ batch-2 models that hit quota), model_bench.py only
        if os.environ.get("LLM7_API_KEY"):
            for m in ("grok-4.5", "grok-4.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra", "gpt-6-sol", "gpt-6.1-sol",
                      "chroma-v.46-flash", "jev-latest", "Voxtral-Small-24B-2507", "codestral-latest"):
                out.append(("llm7:" + m, lambda p, m=m: post("https://api.llm7.io/v1/chat/completions", os.environ["LLM7_API_KEY"], m, p)))
        if os.environ.get("CF_API_TOKEN"):
            acc = os.environ.get("CF_ACCOUNT_ID", "")
            cfu = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/v1/chat/completions"
            want = ("glm-5.2", "kimi-k2.7-code", "clef", "clef-flash", "apertus-v1.5-8b", "eurollm-9b-it", "gemma-sea-lion-v4-27b-it",
                    "granite-4.0-h-micro", "llama-3.1-8b-instruct-fp8", "llama-3.2-3b-instruct",
                    "llama-4-scout-17b-16e-instruct", "mistral-small-3.1-24b-instruct", "qwq-32b", "qwen3-30b-a3b-fp8", "deepseek-r1-distill-qwen-32b")
            try:  # full ids (@cf/<org>/<name>) from the live catalog
                req = urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/models/search?per_page=500",
                                             headers={"Authorization": "Bearer " + os.environ["CF_API_TOKEN"]})
                with urllib.request.urlopen(req, timeout=60) as r:
                    names = [m["name"] for m in json.load(r)["result"]]
                for w in want:
                    hit = next((n for n in names if n.split("/")[-1] == w), None)
                    note(f"cf candidate {w}: {hit}")
                    if hit:
                        out.append(("cf:" + hit, lambda p, m=hit: post(cfu, os.environ["CF_API_TOKEN"], m, p)))
            except Exception as e:  # noqa: BLE001
                note(f"cf catalog failed: {e}")
        for m in ("Qwen3-Coder-30B-A3B-Instruct", "Qwen3.5-9B", "Mistral-7B-Instruct-v0.3"):
            out.append(("ovh:" + m, lambda p, m=m: post("https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions", "", m, p)))
        out.append(("kilo:poolside/laguna-xs-2.1:free", lambda p: post("https://api.kilo.ai/api/gateway/chat/completions", "", "poolside/laguna-xs-2.1:free", p)))
        # 4th batch: models never tested before (checked against memory reference_tested_news_models)
        if os.environ.get("GEMINI_API_KEY"):  # every Gemini/Gemma model has its own quota; only ids not tested yet
            done = {"gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-flash-latest",
                    "gemma-4-26b-a4b-it", "gemma-4-31b-it"}
            try:
                url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=" + os.environ["GEMINI_API_KEY"]
                with urllib.request.urlopen(url, timeout=60) as r:
                    seen = [m["name"].split("/", 1)[1] for m in json.load(r).get("models", [])
                            if "generateContent" in m.get("supportedGenerationMethods", [])]
                seen = [m for m in seen if re.match(r"^gem(ini|ma)-", m) and m not in done
                        and not re.search(r"embed|image|tts|audio|live|robotics|computer", m)]
                note("gemini new candidates: " + ", ".join(seen))
                for m in seen[:12]:
                    out.append(("gemini4:" + m, lambda p, m=m: post(GEMINI_URL, os.environ["GEMINI_API_KEY"], m, p)))
            except Exception as e:  # noqa: BLE001
                note(f"gemini list failed: {e}")
        out.append(("ovh:Qwen2.5-VL-72B-Instruct", lambda p: post("https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions", "", "Qwen2.5-VL-72B-Instruct", p)))
        if os.environ.get("COHERE_API_KEY"):
            for m in ("command-r-08-2024", "command-a-vision-07-2025"):
                out.append(("cohere:" + m, lambda p, m=m: post("https://api.cohere.ai/compatibility/v1/chat/completions", os.environ["COHERE_API_KEY"], m, p)))
        if os.environ.get("ZAI_API_KEY"):
            out.append(("zai:glm-4.6v-flash", lambda p: post("https://api.z.ai/api/paas/v4/chat/completions", os.environ["ZAI_API_KEY"], "glm-4.6v-flash", p)))
    # content agent 1405-08-04: hf (402, credit gone) and cohere (429, trial key 1000/month used) failed every call all night;
    # out of the rotation until their accounts are fixed (model_bench still sees them with BENCH_NEW)
    if not os.environ.get("BENCH_NEW"):
        out = [x for x in out if not x[0].startswith(("hf:", "cohere:"))]
    return out


LOG = []


# backend 1405-08-03: kala and energy both wrote news-test/live-log.txt; from 20:32 every kala push hit a rebase conflict on it,
# was aborted, and 2 hours of gemma news never left the runner. Each market now has its own file.
LIVE_LOG = "live-log-" + next((sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == "--market"), "kala") + ".txt"


def note(line):
    print(line, flush=True)
    LOG.append(line)
    if os.environ.get("PUSH_EACH"):
        os.makedirs(os.path.join(ROOT, "news-test"), exist_ok=True)
        open(os.path.join(ROOT, "news-test", LIVE_LOG), "w", encoding="utf-8").write("\n".join(LOG[-200:]))


class QuotaError(ValueError):
    pass


QUOTA_RE = re.compile(r"quota|exceeded|per day|daily limit|daily free allocation|RESOURCE_EXHAUSTED", re.I)
DAILY_RE = re.compile(r"daily free allocation|per day|daily limit|free-models-per-day", re.I)  # content agent 1405-08-03: cf 429 «daily free allocation» -> out until tomorrow
DEAD_RE = re.compile(r"HTTP 40[0134].*(Unknown Model|model_not_found|sign in to use|Trial key|provider or policy you attempted)", re.I | re.S)
EXHAUSTED_FILE = os.path.join(tempfile.gettempdir(), f"exhausted-{time.strftime('%Y-%m-%d', time.gmtime())}.txt")


QUOTA_RETRY = 3600  # content agent 1405-08-02: a used-up quota (e.g. gapgpt credit, recharged later) is tried again after an hour


def exhausted():
    """models whose quota ran out less than QUOTA_RETRY seconds ago; lines are «name epoch» (a bare name = the whole day)."""
    if not os.path.exists(EXHAUSTED_FILE):
        return set()
    out = set()
    for line in open(EXHAUSTED_FILE, encoding="utf-8").read().splitlines():
        parts = line.split()
        if parts and (len(parts) < 2 or time.time() - float(parts[1]) < QUOTA_RETRY):
            out.add(parts[0])
    return out


def retry(call, prompt):
    """busy / rate-limited models get 3 more tries with growing pauses before we move to the next model;
    a used-up quota is not busy - it fails at once so the model is dropped for the rest of the day."""
    for wait in (0, 15, 30):
        time.sleep(wait)
        try:
            return call(prompt)
        except ValueError as e:
            if re.search(r"HTTP 40[23]", str(e)) and QUOTA_RE.search(str(e)):  # backend 1405-07-30: gapgpt «pre-consume quota failed» (credit gone) was retried 20 times per news
                raise QuotaError(str(e)) from e
            if re.search(r"HTTP 429", str(e)) and QUOTA_RE.search(str(e)) and not re.search(r"PerMinute|per minute|PerSecond", str(e), re.I):
                raise QuotaError(str(e)) from e
            if not re.search(r"HTTP (429|500|502|503)", str(e)) or wait == 30:
                raise


# clichés the code fixes by itself (no rewrite needed); the rest of BANNED sends the text back to the model
FIXES = [(re.compile(r"^به گزارش [^،,]{0,40}(?:ام[‌ ]?اس[‌ ]?دیتا|msdata)[^،,]{0,10}[،,]\s*"), ""),
         (re.compile(r"به شمار می[‌ ]?رود|محسوب می[‌ ]?شود"), "است"), (re.compile(r"به ثبت رسید|رقم خورد"), "رسید"),
         (re.compile(r"بالغ شد"), "رسید"), (re.compile(r"شایان ذکر است(?: که)?\s*"), ""), (re.compile(r"در این میان،?\s*"), ""),
         # content agent 1405-07-14: «۱ هزار و» -> «هزار و»; a lone «عرض/العرض» is always a typo of «عرضه» in commodity news
         (re.compile(r"(?<![0-9۰-۹٫.])[1۱] هزار"), "هزار"), (re.compile(r"(?<![0-9۰-۹٫.])[1۱] میلیون"), "یک میلیون"),
         # content agent 1405-07-22: «این نماد/این سهم» -> «این کالا», a lone «نماد» -> «کالا», «۲۲ مهر ۲۲ مهر» -> once
         (re.compile(r"این (?:نماد|سهم)(?![\w‌])"), "این کالا"), (re.compile(r"(?<![\w‌])نماد(?![\w‌])"), "کالا"),
         (re.compile(r"([0-9۰-۹]{1,2} (?:فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند))(?: \1)+"), r"\1"), (re.compile(r"(?<![\w‌])(?:ال)?عرض(?![\w‌])"), "عرضه")]
BANNED = re.compile(r"به گزارش|بررسی (?:داده|آمار)\S* نشان|نشان[‌ ]?دهنده|حاکی از|بیانگر|؛ رقمی که|این در حالی است|به خود اختصاص|"
                    r"در مجموع|روی میز|تاریخی|فصل ساخت|منتظر|انتظار می‌رود|احتمالاً|شاید|[?؟]\s*$|ms ?d\w*ata", re.I)
# owner 1405-07-14: «رکورد» is allowed; the source is named once, in Persian only («ام‌اس‌دیتا», owner 1405-07-15 via content agent)
MSD = re.compile(r"ام[‌ ]?اس[‌ ]?دیتا")
# style guide v2 items that every model still breaks often (1405-07-14: 0 of 131 news passed them) -> logged, not rejected yet;
# promoted to BANNED/probs once the new facts and prompt bring them down
SOFT = re.compile(r"ثبت شد|به ثبت رساند|در حالی که")
NUM = re.compile(r"[0-9۰-۹]+(?:[٫.][0-9۰-۹]+)?")


SOURCE_END = "این خبر بر پایه‌ی داده‌های ام‌اس‌دیتا از بورس کالا نوشته شده است."


def ensure_source(text, key=""):
    """content agent 1405-07-15: a bare «ام‌اس‌دیتا» / «منبع: ام‌اس‌دیتا» tail, or no mention at all -> the picked source
    sentence at the end of paragraph 2 (content agent 1405-07-22), or of the last paragraph when there is only one."""
    t = re.sub(r"\s*(?:منبع\s*[:：]\s*)?ام[‌ ]?اس[‌ ]?دیتا\.?\s*$", "", text.rstrip())
    if not MSD.search(t):
        paras = t.split("\n\n")
        k = 1 if len(paras) > 1 else 0
        paras[k] = paras[k].rstrip() + " " + (source_pick(key) if key else SOURCE_END)
        t = "\n\n".join(paras)
    return t


def clean(t):
    for rx, rep in FIXES:
        t = rx.sub(rep, t)
    return t


def loads_lenient(t):
    """JSON from a model, repaired instead of thrown away (1405-07-16: ~1/4 of failed tries were only broken JSON):
    raw newlines/tabs inside strings, trailing commas, ```json fences, smart quotes around keys."""
    try:
        return json.loads(t, strict=False)
    except ValueError:
        pass
    t2 = re.sub(r"^```(?:json)?|```$", "", t.strip(), flags=re.M)
    t2 = re.sub(r",\s*([}\]])", r"\1", t2)
    t2 = re.sub(r"[\u201c\u201d](\w+)[\u201c\u201d]\s*:", r'"\1":', t2)
    return json.loads(t2, strict=False)


def parse(raw, facts="", tables=None):
    m = re.search(r"\{.*\}", raw, re.S)
    d = loads_lenient(m.group(0)) if m else None
    if not isinstance(d, dict):
        raise ValueError("no JSON: " + raw[-80:].replace("\n", " "))  # the tail shows a cut answer vs. text outside the JSON
    if isinstance(d.get("title"), str) and d["title"].strip() and not (isinstance(d.get("slug"), str) and d["slug"].strip()):
        d["slug"] = d["title"]  # a missing slug is not worth a lost news: the title becomes the slug (cleaned below by SLUG_BAD)
    for k in ("title", "slug", "subtitle", "lead", "text"):
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise ValueError(f"empty {k}")
        d[k] = clean(d[k].replace("\r", "").strip())
    if facts:  # owner 2026-10-12: «امروز» -> weekday and date the first time, only the weekday after that
        seen = [False]
        d["title"] = today_words(d["title"], day_of(facts))
        for k in ("subtitle", "lead", "text"):
            d[k] = today_words(d[k], day_of(facts), seen)
    d["text"] = ensure_source(d["text"], (facts.splitlines()[0] + day_of(facts)) if facts else "")
    w, p = words(d["text"]), paragraphs(d["text"])
    if not MIN_WORDS[0] <= w <= 220 or not 2 <= p <= 4:  # = validate.py limits; stricter limits only threw away good news
        raise ValueError(f"{w} words / {p} paragraphs")
    if any(FORBIDDEN.search(d[k]) for k in ("title", "subtitle", "lead", "text")):
        raise ValueError("forbidden content")
    probs = []
    for k in ("title", "subtitle", "lead", "text"):
        probs += [f"«{x.group(0)}» در {k}" for x in BANNED.finditer(d[k])]
    if tables is not None:
        tid = str(d.get("table") or "").strip().upper()
        if tid not in tables:
            probs.append(f"table باید یکی از {', '.join(tables)} باشد")
        else:
            d["table"] = tables[tid]
        # every number in the news must come from the facts or the tables (the model does no arithmetic)
        # Latin or Persian digits are the same number (a model that writes «1.7» for «۱٫۷» is not inventing it)
        fa = lambda n: n.translate(str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫"))  # noqa: E731
        known = {fa(n) for n in NUM.findall(facts + json.dumps(tables, ensure_ascii=False))} | set("۰۱۲۳۴۵۶۷۸۹")
        bad = sorted({n for k in ("title", "subtitle", "lead", "text") for n in NUM.findall(d[k]) if fa(n) not in known})
        if bad:
            probs.append("عدد بیرون از حقیقت‌ها: " + "، ".join(bad))
    # wrong hall (content agent: cement written as «تالار صنعتی و معدنی"; the data hall was right)
    for h in ("صنعتی و معدنی", "پتروشیمی", "سیمان", "کشاورزی", "فرعی", "صادراتی"):
        if facts and re.search("تالار[‌ ]+" + h, " ".join(d[k] for k in ("title", "lead", "text"))) and h not in facts.splitlines()[0]:
            probs.append(f"تالار اشتباه: «{h}»")
    first = re.split(r"(?<=[.!؟])\s", d["text"].strip(), 1)[0]
    if d["lead"] in d["text"] or d["lead"][:40] == first[:40]:
        probs.append("لید عیناً در متن تکرار شده")
    # content agent checks (style guide v2, 1405-07-14)
    soft = [x.group(0) for k in ("title", "lead", "text") for x in SOFT.finditer(d[k])]
    if len(MSD.findall(d["text"])) != 1 or MSD.search(d["title"] + d["lead"]):  # logged for now; reject once models follow it
        soft.append("ام‌اس‌دیتا!=1")
    if len(re.findall(r"امروز", d["title"] + d["lead"] + d["text"])) > 2:
        soft.append("امروز>2")
    if re.search(r"[A-Za-z]{2,}[-0-9]|[A-Za-z][0-9]|[0-9][A-Za-z]|[؀-ۿ][A-Za-z]|[A-Za-z][؀-ۿ]", d["title"] + " " + d["lead"] + " " + d["text"]):
        probs.append("کد کالا یا حروف لاتین وسط متن")
    # content agent 1405-07-15: Chinese/Cyrillic/other scripts slipped through (e.g. «同样»); only Persian, digits, punctuation and Latin
    if re.search(r"[^\u0600-\u06FF\u200c\u200d\uFB50-\uFDFF\uFE70-\uFEFF\x00-\x7F\u00A0-\u00FF\u2000-\u206F«»×÷٪]", " ".join(d[k] for k in ("title", "subtitle", "lead", "text"))):
        probs.append("نویسه‌ی غیرفارسی (چینی/سیریلیک/…)")
    if re.search(r"(?<![0-9۰-۹])صفر ریال|بدون قیمت", " ".join(d[k] for k in ("title", "subtitle", "lead", "text"))):
        probs.append("نرخ «صفر ریال»/«بدون قیمت»")
    if len(d["lead"].split()) > 35 or len(NUM.findall(d["lead"])) > 1:
        soft.append("lead>35w/1num")
    if len(NUM.findall(d["text"])) > 8:
        soft.append(f"text {len(NUM.findall(d['text']))} nums")
    if soft:
        note("style-soft: " + ", ".join(soft))
    if probs:
        raise ValueError("markers: " + " | ".join(probs))
    d["slug"] = SLUG_BAD.sub("-", d["slug"].strip())
    # key_points: kept only when it is 3 clean short sentences with numbers of this news; otherwise dropped, the news stays
    kp = d.pop("key_points", None)
    if isinstance(kp, list) and len(kp) == 3 and all(isinstance(x, str) and 0 < len(x.split()) < 15 for x in kp):
        kp = [clean(x.strip()) for x in kp]
        body = d["title"] + " " + d["subtitle"] + " " + d["lead"] + " " + d["text"]
        if not any(BANNED.search(x) or FORBIDDEN.search(x) or re.search(r"[A-Za-z]", x) for x in kp) and all(n in body for x in kp for n in NUM.findall(x)):
            d["key_points"] = kp
    return d


# ---- paid model, staged writing (owner 2026-10-12) ----
# A small model loses half of a long rule sheet. So it never gets all rules at once: it writes a draft with the general
# rules, then corrects the same draft twice (accuracy, then wording), and every result still passes parse() like any other
# news. A rejected text goes back with its errors; after 2 failed corrections a fresh draft starts. Up to PAID_TRIES calls.
PAID_TRIES = 8  # owner 1405-07-30: was 20; the token bill went 10x
FREE_TRIES = 8  # free models (1405-07-25): the 5 steps for every model, fewer calls per news for their daily quota
JSON_KEYS = ("فقط یک JSON با کلیدهای title, slug, subtitle, lead, text, table, key_points برگردان و هیچ متن دیگری ننویس. "
             "slug همان تیتر با خط تیره به‌جای فاصله است. table فقط شناسه‌ی یکی از جدول‌های پیشنهادی است (مثلاً \"T2\"). ")
# step 1 - draft: only the shape of the news; no wording rules (they come in later steps)
# content agent 1405-08-02: the draft gets style_kala.md v3 + the 40 headline rules; only three mechanical rules stay beside them
STAGE1 = ("تو خبرنگار بورس کالای ام‌اس‌دیتا هستی و از روی «حقیقت‌ها» خبر یک نماد را می‌نویسی.\n\n" + STYLE_KALA + HEADLINES +
          "\n\nقاعده‌های ثابت: تاریخ را فقط یک بار و به این شکل بنویس: شنبه ۲۵ مهر. هر عدد و هر مقایسه فقط از حقیقت‌ها؛ "
          "عدد، درصد یا مقایسه‌ای خودت نساز. «ثابت ماند» فقط وقتی که حقیقت‌ها «نسبت به آخرین معامله: قیمت ثابت ماند» بگویند؛ "
          "برابری با قیمت پایه ثابت ماندن نیست. پاراگراف‌های text با یک خط خالی (\\n\\n) جدا شوند.\n" + JSON_KEYS)
# step 2 - check: a closed yes/no question per sentence is easier for a small model than a rewrite (content agent 1405-07-22)
STAGE2 = ("تو راستی‌آزما هستی. «حقیقت‌ها» و جمله‌های شماره‌دار یک خبر را می‌گیری. هر جمله را با حقیقت‌ها بسنج. "
          "جمله غلط است اگر: جهت را برعکس بگوید (بیشتر/کمتر، گران‌تر/ارزان‌تر)، بگوید تمام عرضه فروخته شد در حالی که بخشی ماند، "
          "ارزش این جلسه را ارزش امسال یا ارزش کل بنامد، حجم تقاضا را مقدار خرید بنامد، تعداد تولیدکننده‌ها را غلط بشمارد، "
          "یا تفسیری کند که حقیقت‌ها پشتش نیست یا منطقش وارونه است (مثل «با وجود تقاضای بالا، تمام عرضه فروش رفت» یا «به افزایش رقابت در بازار کمک می‌کند»)، "
          "یا پیش‌بینی، همکاری یا انبار بگوید. تفسیری که از عددهای حقیقت‌ها درمی‌آید درست است (مثل «تقاضا ۲٫۲ برابر بود و رقابت قیمت را ۱۱ درصد بالا برد»). "
          "فقط یک JSON مثل {\"bad\": [2, 5]} برگردان؛ اگر همه درست است {\"bad\": []}.")
# 5-step variant (owner 1405-07-23, off until compared): the check above in two short questions
FIVE_STEPS = [True]  # owner 1405-07-25 after round 3 (content agent: 3.3 issues / 8 errors vs 3.4 / 9)
STAGE2_NUM = ("تو راستی‌آزمای عدد هستی. «حقیقت‌ها» و جمله‌های شماره‌دار یک خبر را می‌گیری. فقط عدد و جهت هر جمله را بسنج. "
              "جمله غلط است اگر عددش با حقیقت‌ها نخواند، جهت را برعکس بگوید (بیشتر/کمتر، گران‌تر/ارزان‌تر)، حجم تقاضا را مقدار خرید بنامد، "
              "ارزش این جلسه را ارزش امسال یا ارزش کل بنامد، یا تعداد تولیدکننده‌ها را غلط بشمارد. "
              "فقط یک JSON مثل {\"bad\": [2, 5]} برگردان؛ اگر همه درست است {\"bad\": []}.")
STAGE2_MEANING = ("تو راستی‌آزمای تفسیر هستی. «حقیقت‌ها» و جمله‌های شماره‌دار یک خبر را می‌گیری. فقط جمله‌هایی را بسنج که تفسیر می‌کنند (علت، نتیجه، حال بازار). "
                  "تفسیری که از عددهای حقیقت‌ها درمی‌آید درست است (مثل «تقاضا ۲٫۲ برابر بود و رقابت قیمت را ۱۱ درصد بالا برد»). "
                  "غلط است اگر حقیقت‌ها پشتش نباشد، منطقش وارونه باشد (مثل «با وجود تقاضای بالا، تمام عرضه فروش رفت»)، یا پیش‌بینی، همکاری یا انبار بگوید. "
                  "فقط یک JSON مثل {\"bad\": [3]} برگردان؛ اگر همه درست است {\"bad\": []}.")
# step 3 - wording only (numbers, names and dates stay)
STAGE3 = ("تو ویراستار نثر هستی. یک خبر را می‌گیری. عددها، نام‌ها، تاریخ‌ها و جهت تغییرها را دست نزن و فقط نثر را درست کن:\n"
          "۱) این عبارت‌ها را با فعل ساده (فروخت، خرید، رسید، بود، است) عوض کن: «به گزارش»، «نشان‌دهنده»، «نشان می‌دهد»، "
          "«حاکی از»، «بیانگر»، «این در حالی است»، «در این میان»، «شایان ذکر است»، «به شمار می‌رود»، «محسوب می‌شود»، «رقم خورد»، "
          "«بالغ شد»، «به ثبت رسید»، «به خود اختصاص داد»، «در مجموع»، «تاریخی»، «انتظار می‌رود»، «احتمالاً»، «شاید».\n"
          "۲) جمله‌ها کوتاه (۱۵ تا ۲۰ کلمه) و فعل در آخر جمله.\n"
          "۳) «قیمت» بنویس، نه «نرخ». «نماد» و «سهم» ننویس؛ «کالا» بنویس. «امروز» و «دیروز» ننویس.\n"
          "۴) «ام‌اس‌دیتا» را ننویس؛ جمله‌ی منبع را خود سامانه می‌گذارد.\n"
          "۵) text همچنان ۱۲۰ تا ۱۸۰ کلمه در ۲ یا ۳ پاراگراف بماند، حداکثر ۶ عدد، بدون ترتیب ثابت پاراگراف‌ها.\n" + JSON_KEYS)
# step 4 - the title alone, with a verb chosen from the data
STAGE_TITLE = ("تو تیترنویس هستی. فقط یک تیتر فارسی بنویس، حدود ۱۰ کلمه: نام کالا و تولیدکننده، فقط یک عدد از حقیقت‌ها، "
               "و در آخر یک فعل که با حقیقت‌ها بخواند (مثل «فروخته شد»، «به … رسید»، «گران‌تر شد»، «ارزان‌تر شد»، «ثابت ماند»، "
               "«بی‌خریدار ماند»، «… برابر عرضه شد»). فقط خود تیتر را برگردان؛ بدون گیومه و بدون توضیح.")
STAGE_FIX = ("تو ویراستار هستی. یک خبر و فهرست ایرادهایش را می‌گیری. فقط همان ایرادها را رفع کن و بقیه‌ی متن را نگه دار. "
             "عددها فقط از حقیقت‌ها. text باید ۱۲۰ تا ۱۸۰ کلمه در ۲ یا ۳ پاراگراف با یک خط خالی (\\n\\n) بین آن‌ها باشد. " + JSON_KEYS)


LATIN_CODE = re.compile(r"[A-Za-z][A-Za-z0-9./+-]*")
DOT = re.compile(r"(?<=[0-9۰-۹])[.](?=[0-9۰-۹])")
STAGE_SENT = ("تو ویراستار هستی. یک جمله از یک خبر و ایراد آن را می‌گیری. همان جمله را با همان معنا و همان عددها دوباره بنویس تا ایراد رفع شود. "
              "فعل ساده به کار ببر (فروخت، خرید، رسید، بود، است). فقط خودِ جمله‌ی تازه را برگردان؛ بدون توضیح، بدون گیومه و بدون JSON.")


NAME_LINE = re.compile(r"نام کالا: ([^؛]+)؛ (?:تولیدکننده|عرضه‌کننده): (?:شرکت )?([^.؛]+)")
NAME_STOP = {"شرکت", "صنایع", "گروه", "تولید", "ایران", "مجتمع", "کارخانه", "انواع", "نوع", "درجه", "گرید"}
TITLE_VERB = re.compile(r"(?:ت|د|است|ند)$")


DATE_WORDS = re.compile(r"[0-9۰-۹]{1,2} (?:فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند)(?: [0-9۰-۹]{4})?")


def clean_title(t):
    """content agent 1405-07-22: no final dot, the slug is made from the cleaned title (no comma, dot or Latin)"""
    return re.sub(r"[.。]+$", "", t.strip().strip("«»\"").strip())


def sale_problem(d, facts):
    """content agent 1405-07-22: «x برابر» only with «تقاضا»; «بی‌خریدار ماند» only when nothing traded; «تمام/کامل» only
    when the whole offer sold; the quantity next to «عرضه کرد» is the offered quantity."""
    probs, t = [], d["title"]
    for m in re.finditer(r"برابر", t):
        if "تقاضا" not in t[max(0, m.start() - 40):m.start()]:
            probs.append("در تیتر «برابر» فقط با «تقاضا» بیاید (تقاضا x برابر عرضه بود)")
            break
    if "بی‌خریدار" in t and "هیچ معامله‌ای انجام نشد" not in facts:
        probs.append("تیتر «بی‌خریدار» نوشته ولی کالا فروش رفته است")
    if re.search(r"تمام|کامل", t) and "تمام عرضه به فروش رفت" not in facts:
        probs.append("تیتر «تمام/کامل» نوشته ولی همه‌ی عرضه فروش نرفت")
    m = re.search(r"(?:امروز|این جلسه) ((?:[0-9۰-۹٫]+ )?(?:هزار|میلیون)(?: و [0-9۰-۹٫]+)?|[0-9۰-۹٫]+) [^.]*? عرضه کرد", facts)
    off = set(NUM.findall(m.group(1))[-1:]) if m else set()  # the last group: «۱ هزار و ۷۲۵» -> ۷۲۵
    if off:
        for x in re.split(r"(?<=[.!؟])\s+", d["lead"] + " " + d["text"]):
            if "عرضه کرد" in x and NUM.findall(DATE_WORDS.sub("", x)) and not off & set(NUM.findall(x)):
                probs.append(f"مقدار عرضه غلط است؛ عرضه {m.group(1)} بود")
                break
    return " | ".join(probs)


def title_problem(t, facts):
    """content agent 1405-07-22: no Latin, at most one comma, 8-13 words, a verb, and the commodity named as in the data
    (its first two words, or the one word) - «کنسانتره مس» for «کنسانتره فلزات گرانبها» is another commodity."""
    probs = []
    if re.search(r"[A-Za-z]", t):
        probs.append("حرف لاتین در تیتر")
    if len(re.findall(r"[،,]", t)) > 1:
        probs.append("بیش از یک ویرگول در تیتر")
    if not 8 <= len(t.split()) <= 13:
        probs.append(f"تیتر {len(t.split())} کلمه است؛ ۸ تا ۱۳ کلمه بنویس")
    m = NAME_LINE.search(facts.splitlines()[0]) if facts else None
    if m:
        w = re.match(r"\s*((?:[\u0600-\u06EF\u06FA-\u06FF\u200c]+ ?){1,2})", m.group(1))  # Persian digits end the name
        w = w.group(1).split() if w else []
        if w and " ".join(w) not in t:
            probs.append(f"نام کالا در تیتر باید «{' '.join(w)}» باشد")
    return " | ".join(probs)



TODAY_TITLES = []  # titles of this market and day so far (stage 6: no repeated title or final verb)
STAGE6 = ("تو تیترنویس ام‌اس‌دیتا هستی. برای خبری که متن نهایی‌اش را می‌بینی تیتر و سوتیتر تازه بنویس و فقط یک JSON "
          'به شکل {"title": "...", "subtitle": "..."} برگردان. تیتر ۸ تا ۱۳ کلمه، با فعل، بدون حرف لاتین، و هر عددش در متن باشد.' + HEADLINES)


def headline6(d, facts, ask, note=print, kind="news"):
    """content agent / owner 1405-07-27: stage 6, the title alone with the 40 headline rules, up to 3 calls.
    A rejected title keeps the stage-5 title; this stage never stops a news from being published."""
    q = sum(t.rstrip().endswith("؟") for t in TODAY_TITLES)
    ends = {t.split()[-1] for t in TODAY_TITLES if t.split()}
    want_q = len(TODAY_TITLES) >= 4 and q * 5 < len(TODAY_TITLES) + 1
    why, n = "", 0
    for n in range(1, 4):
        raw = ask(STAGE6, "حقیقت‌ها:\n" + facts[:2500] + "\n\nمتن نهایی:\n" + d.get("lead", "") + "\n\n" + d["text"]
                  + "\n\nتیترهای امروز همین بازار:\n" + ("\n".join(TODAY_TITLES[-15:]) or "هنوز نیست")
                  + f"\n\nتیترهای پرسشی امروز: {q} از {len(TODAY_TITLES)}" + ("؛ این تیتر پرسشی باشد و جوابش در لید آمده باشد." if want_q else "")
                  + (f"\n\nتیتر قبلی رد شد: {why}" if why else ""))
        m = re.search(r"\{.*\}", raw or "", re.S)
        try:
            j = loads_lenient(m.group(0)) if m else None
        except ValueError:
            j = None
        t = clean_title(j["title"]) if isinstance(j, dict) and isinstance(j.get("title"), str) else ""
        probs = []
        if not t:
            probs.append("JSON درست نبود")
        else:
            if re.search(r"[A-Za-z]", t):
                probs.append("حرف لاتین")
            if not 8 <= len(t.split()) <= 13:
                probs.append(f"{len(t.split())} کلمه؛ ۸ تا ۱۳")
            last = t.rstrip("؟").split()[-1] if t.split() else ""
            if not t.endswith("؟") and not TITLE_VERB.search(last):
                probs.append("فعل ندارد")
            fa = lambda x: x.translate(str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫"))  # noqa: E731
            body = {fa(x) for x in NUM.findall(d.get("lead", "") + " " + d["text"])}
            out = [x for x in NUM.findall(DATE_WORDS.sub("", t)) if fa(x) not in body]
            if out:
                probs.append("عدد بیرون از متن: " + "، ".join(out))
            if last in ends:
                probs.append(f"فعل «{last}» امروز تکرار شده")
            if t in TODAY_TITLES:
                probs.append("تیتر تکراری")
            if kind == "news" and facts:
                probs += [x for x in (sale_problem({"title": t, "lead": d.get("lead", ""), "text": d["text"]}, facts),) if x]
        if not probs:
            d["title"] = t
            if isinstance(j.get("subtitle"), str) and j["subtitle"].strip() and not re.search(r"[A-Za-z]", j["subtitle"]):
                d["subtitle"] = j["subtitle"].strip()
            note(f"  stage6 {kind}: title in {n} calls")
            return d
        why = " | ".join(probs)
    note(f"  stage6 {kind}: kept stage-5 title after {n} calls ({why[:80]})")
    return d

def number_problem(text):
    """content agent 1405-07-22: at most 2 numbers in a sentence and 6 in the text (content agent 1405-08-02: was 15) (dates are not counted)"""
    t = DATE_WORDS.sub("", text)
    probs = []
    if len(NUM.findall(t)) > 6:
        probs.append(f"متن {len(NUM.findall(t))} عدد دارد؛ حداکثر ۶ عدد بماند")
    many = [x for x in re.split(r"(?<=[.!؟])\s+", t) if len(NUM.findall(x)) > 2]
    if many:
        probs.append(f"{len(many)} جمله بیش از ۲ عدد دارد؛ هر جمله حداکثر ۲ عدد")
    return " | ".join(probs)


AUTO_FIX = [(re.compile(r"لازم به ذکر است(?: که)?\s*"), ""), (re.compile(r"(?<![\w‌])ثبت شد"), "بود"),
            (re.compile(r"رقم زد"), "رسید"), (re.compile(r"(?<![\w‌])نشان[‌ ]?دهنده‌ی"), "یعنی"),
            (re.compile(r"(?<![\w‌])نشان[‌ ]?دهنده"), "نشانه‌ی"),
            # content agent 1405-07-22 (second review)
            (re.compile(r"\s*تعیین[‌ ]?شده توسط (?:شرکت )?[^،.]{0,40}?(?=[،.]| بود| است| به| در| را)"), ""),
            (re.compile(r"(?<![0-9۰-۹٫.])(?:یک|[1۱]) هزار"), "هزار"), (re.compile("[\u0660-\u0669]"), lambda m: chr(ord(m.group(0)) + 0x90)),
            # content agent 1405-07-23 (third review): tone words go, stock verbs become plain ones
            (re.compile(r"([0-9۰-۹]{1,2} (?:فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند))(?: [0-9۰-۹]{4})?[،,]? \1(?![0-9۰-۹])"), r"\1"),
            (re.compile(r"خوشبختانه،?\s*"), ""), (re.compile(r"موفق به ([\u0600-\u06FF\u200c]+) (?:شد|شدند|گردید)"), r"\1 داشت"),
            (re.compile(r"در نهایت،?\s*"), ""), (re.compile(r"صورت گرفت"), "انجام شد"), (re.compile(r"به (?:اتمام|پایان) رسید"), "تمام شد"),
            (re.compile(r"به شمار می[‌ ]?رسد"), "است"), (re.compile(r"بوده بود"), "بود"), (re.compile(r"بورس کالای ام[‌ ]?اس[‌ ]?دیتا"), "بورس کالا"),
            (re.compile(r"را به ثبت رساند"), "داشت"), (re.compile(r"به ثبت رساند"), "داشت"), (re.compile(r"این بدان معناست که"), "یعنی"),
            (re.compile(r"این بدان معناست"), "یعنی"), (re.compile(r"عرضه نمود"), "عرضه کرد"), (re.compile(r"(?<![\w‌])نمود(?![\w‌])"), "کرد"),
            (re.compile(r"قابل[‌ ]توجهی"), "زیادی"), (re.compile(r"قابل[‌ ]توجه"), "زیاد"),
            (re.compile(r"(درصد عرضه) (?:شد|گردید)(?=[،.\s]|$)"), r"\1 فروخته شد")]
# sentences cut when the text stays long enough: fillers, a date alone, no verb at the end
FILLER = re.compile(r"مورد توجه فعالان|به تصویر کشید|استقبال مناسب|قابل توجه|به این میزان استقبال کردند|شاهد|نشان می[‌ ]?داد|در حداقل سطح ممکن|با کیفیت|نیاز صنایع|شرایط بازار باعث|استقبال (?:بالا|خوب)|واکنش خوب|در حالی که|نشان از|نشانه‌ی|استقبال کرد|اهمیت این محصول|در محدوده‌ی مشخصی|صبح |عصر |به ثبت برسد")
ABOVE_BASE = re.compile(r"([0-9۰-۹]+(?:[٫.][0-9۰-۹]+)?) درصد بالاتر از قیمت پایه")
DATE_ONLY = re.compile(r"^(?:(?:شنبه|یکشنبه|دوشنبه|سه‌شنبه|چهارشنبه|پنجشنبه|جمعه)\s+)?[0-9۰-۹]{1,2} \S+(?: [0-9۰-۹]{4})?[.!]?$")
VERB_END = re.compile(r"(?:ت|د|است|ند|ید|یم)[.!؟]?$")


def autofix(raw, min_words=120, facts=""):
    """what gemma could not fix in 20 calls is fixed here: stock phrases replaced, lone Latin letters removed, and sentences
    with more than 2 numbers (dates not counted) cut while the text keeps min_words; at most 6 numbers in the text."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    d = loads_lenient(m.group(0)) if m else None
    if not isinstance(d, dict) or not all(isinstance(d.get(k), str) for k in ("title", "lead", "text")):
        return raw
    for k in ("title", "subtitle", "lead", "text"):
        if isinstance(d.get(k), str):
            for rx, r in AUTO_FIX:
                d[k] = rx.sub(r, d[k])
            d[k] = re.sub(r"(?<![\w-])[A-Za-z][A-Za-z0-9]{0,3}(?![\w])", "", d[k])
            d[k] = re.sub(r"\s*-+\s*(?=[\s،.؛)]|$)|(?<=\s)-+(?=\s)", " ", d[k])  # «میلگرد ۲۲ تا ۳۲-» -> «میلگرد ۲۲ تا ۳۲»
            d[k] = re.sub(r" {2,}", " ", d[k]).replace(" ،", "،").replace(" .", ".").strip()
    d["title"] = clean_title(d["title"]).replace(":", "،")
    lead = re.split(r"(?<=[.!؟])\s+", d["lead"].strip())
    if len(lead) > 1 and len(lead[0].split()) >= 12:
        d["lead"] = lead[0]
    m = ABOVE_BASE.search(facts or "")
    rival = bool(m) and float(m.group(1).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫", "0123456789."))) > 1
    cnt = lambda x: len(NUM.findall(DATE_WORDS.sub("", x)))  # noqa: E731

    def split(x):  # content agent 1405-07-22: «… بود و قیمت پایه … بود.» -> two sentences
        if cnt(x) <= 2:
            return [x]
        for m in re.finditer(r" و (?=\S)", x):
            a, b = x[:m.start()], x[m.end():]
            if VERB_END.search(a) and cnt(a) <= 2 and len(b.split()) >= 3:
                return [a + "."] + split(b[0].upper() + b[1:] if b else b)
        return [x]
    sents = [(pi, y) for pi, para in enumerate(d["text"].split("\n\n")) for x in re.split(r"(?<=[.!؟])\s+", para.strip()) if x for y in split(x)]
    sents = [(pi, x) for pi, x in sents if not DATE_ONLY.match(x.strip())]
    total0 = sum(len(x.split()) for _, x in sents)
    price_i = next((i for i, (_, x) in enumerate(sents) if "قیمت" in x and "ریال" in x), -1)
    sents = [(pi, x, i == price_i) for i, (pi, x) in enumerate(sents)]
    keep = []
    for pi, x, pin in sents:
        if pin:
            keep.append((pi, x, pin))
            continue
        if (FILLER.search(x) or not VERB_END.search(x.strip()) or (not rival and "رقابت" in x)) and not MSD.search(x) and total0 - len(x.split()) >= min_words:
            total0 -= len(x.split())
            continue
        keep.append((pi, x, pin))
    pinned = {i for i, (_, _, pin) in enumerate(keep) if pin}
    sents = [(pi, x) for pi, x, _ in keep]
    total = sum(len(x.split()) for _, x in sents)
    nums = sum(cnt(x) for _, x in sents)
    drop = set()
    for i in sorted(range(len(sents)), key=lambda i: -cnt(sents[i][1])):
        x = sents[i][1]
        if cnt(x) <= 2 and nums <= 6:
            break
        if MSD.search(x) or i in pinned or total - len(x.split()) < min_words:
            continue
        drop.add(i)
        total -= len(x.split())
        nums -= cnt(x)
    if True:
        paras = {}
        for i, (pi, x) in enumerate(sents):
            if i not in drop:
                paras.setdefault(pi, []).append(x)
        d["text"] = "\n\n".join(" ".join(v) for _, v in sorted(paras.items()))
    return json.dumps(d, ensure_ascii=False)


def off_topic(d, facts):
    """content agent 1405-07-22: gemma once published a car-market story under a rebar symbol. The text must name this commodity
    and this producer (one main word of each), and the title must end with a verb. Returns the problem or ''."""
    m = NAME_LINE.search(facts.splitlines()[0]) if facts else None
    body = " ".join(d[k] for k in ("title", "lead", "text"))
    probs = []
    if m:
        for label, name in (("نام کالا", m.group(1)), ("نام تولیدکننده", m.group(2))):
            keys = [w for w in re.findall(r"[\u0600-\u06FF\u200c]{3,}", name) if w not in NAME_STOP]
            if keys and not any(w in body for w in keys):
                probs.append(f"{label} («{name.strip()}») در خبر نیست؛ خبر باید درباره‌ی همین کالا و همین تولیدکننده باشد")
    last = re.findall(r"[\u0600-\u06FF\u200c]+", d["title"].split("؛")[-1])
    if last and not TITLE_VERB.search(last[-1]):
        probs.append("تیتر فعل ندارد؛ تیتر را با یک فعل تمام کن")
    return " | ".join(probs)


HOURS_URLS = ("https://riz-api.msdata.ir/kala-fizik/raw-data?hours=1", "https://riz-api2.msdata.ir/kala-fizik/raw-data?hours=1")


def row_fp(r):
    """the figures a kala news is written from; when they change, the news is written again."""
    return "|".join(str(r.get(x)) for x in ("offered_qty", "traded_qty", "demand_qty", "weighted_price", "trade_value"))


def closed_halls(date_fa, teh):
    """owner 1405-07-26: a hall's close time changes every day, so it is read from the kala agent's copy of IME GetMarketsInfo
    (08e5951). Rows of a hall that is still open (or closed under 30 minutes ago) are not final. None = hours unknown."""
    for url in HOURS_URLS:
        try:
            d = json.load(urllib.request.urlopen(url, timeout=20))
        except Exception:  # noqa: BLE001 - no hours = the old rule (two unchanged builds)
            continue
        if d.get("trade_date") != date_fa:
            return None
        now = teh.strftime("%H:%M:%S")
        late = (teh - dt.timedelta(minutes=30)).strftime("%H:%M:%S")
        out = {m["talar"] for m in d.get("markets") or [] if m.get("status") == "closed" and (m.get("finish_time") or "99") <= min(now, late)}
        print("kala: closed halls: " + ("، ".join(sorted(out)) or "none"))
        return out
    return None


def keep_rejected(name, facts, raw, err, calls):
    """owner 1405-07-26: every dropped draft is kept in news-test/rejected/<date>.jsonl, to study later why it failed."""
    try:
        d = os.path.join(ROOT, "news-test", "rejected")
        os.makedirs(d, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc)
        with open(os.path.join(d, now.strftime("%Y-%m-%d") + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now.isoformat(timespec="seconds"), "model": name, "calls": calls, "error": str(err),
                                "facts_head": (facts or "").splitlines()[:3], "draft": raw}, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 - a log must never stop the writer
        note(f"  keep_rejected failed: {e}")


def staged(name, call, prompt, facts, tables, market_rules=""):
    """the paid model's own path: draft -> accuracy -> wording -> parse(); up to tries model calls for one news.
    What a small model keeps getting wrong is repaired in small pieces: Latin product codes never reach it, a sentence with
    a number that is not in the facts is cut by the code, and a sentence with a banned phrase is rewritten alone."""
    tries = PAID_TRIES if name in PAID else FREE_TRIES
    calls, err = 0, None
    gw = [0]  # gateway errors of this news
    ban = re.compile(BANNED.pattern + r"|دیروز|نشانگر|نشان داد|نشان می[‌ ]?دهد|را تجربه کرد|امروز", re.I)  # stricter for this model (content agent 2026-10-12/-22)
    day = day_of(facts)
    fa_digits = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    # Latin grades and codes (HP510L, S65 ...) are taken out of what the model reads, so it cannot copy them into the text
    src = LATIN_CODE.sub(lambda m: m.group(0) if re.fullmatch(r"[TF]\d{1,2}|title|columns|rows", m.group(0)) else "", prompt + ("\n\nقواعد این بازار:\n" + market_rules if market_rules else ""))

    def ask(system, user):
        nonlocal calls
        calls += 1
        try:
            # backend 1405-07-28: only wrappers with an `s` parameter take a system prompt; for the others (lambda p, m=m) a second
            # positional argument replaced the MODEL name, so every free model got HTTP 400 «Model 'تو خبرنگار…'» and gemma wrote everything
            raw = retry((lambda p: call(p, s=system)) if "s" in inspect.signature(call).parameters else (lambda p: call(system + "\n\n" + p)), user)
        except QuotaError:
            raise
        except ValueError as e:  # a gateway hiccup (404 no channel, 5xx, timeout) costs one call, not the whole news
            if DEAD_RE.search(str(e)):  # backend 1405-07-28: a model that does not exist / needs sign-in / trial key is dropped for the day, not retried 1000 times
                raise QuotaError(str(e)) from e
            note(f"  staged {name}: call {calls} failed: {str(e)[:120]}")
            gw[0] += 1
            if gw[0] <= 10:  # content agent 1405-07-27: a gateway error is not one of the 20 writing tries (up to 10 per news)
                calls -= 1
            time.sleep(20)
            return ""
        return raw if isinstance(raw, str) else ""

    def repair(raw, problem):
        """sentence-level repair of the two errors the model repeats; returns the new raw text or None when it does not apply"""
        m = re.search(r"\{.*\}", raw, re.S)
        d = loads_lenient(m.group(0)) if m else None
        if not isinstance(d, dict) or not all(isinstance(d.get(k), str) for k in ("title", "lead", "text")):
            return None
        changed = False
        bad_nums = re.search(r"عدد بیرون از حقیقت‌ها: ([^|]*)", problem)
        nums = [n.strip() for n in bad_nums.group(1).split("،")] if bad_nums else []
        # content agent 1405-07-27: a superlative or a wrong hall is one sentence's fault, not the draft's (480 + 330 calls lost)
        terms = re.findall(r"«([^»]+)» در حقیقت‌ها نیست", problem) + ["تالار " + h for h in re.findall(r"تالار اشتباه: «([^»]+)»", problem)]
        paras = []
        for para in d["text"].split("\n\n"):
            keep = []
            for s in re.split(r"(?<=[.!؟])\s+", para.strip()):
                if any(n and n in NUM.findall(s) for n in nums):
                    changed = True  # a sentence with an invented number is cut, never «corrected»
                    continue
                if any(x in s.replace("‌", " ") or x in s for x in terms):
                    changed = True
                    continue
                hit = ban.search(s)
                if hit and calls < tries:
                    new = ask(STAGE_SENT, f"جمله: {s}\n\nایراد: عبارت «{hit.group(0)}» ممنوع است؛ جمله را بدون آن بنویس. اگر تاریخ لازم شد: {day}").strip().strip("«»\"")
                    if new and not ban.search(new) and set(NUM.findall(new)) <= set(NUM.findall(s) + NUM.findall(day)) and "\n" not in new:
                        s, changed = new, True
                keep.append(s)
            if keep:
                paras.append(" ".join(keep))
        d["text"] = "\n\n".join(paras)
        for k in ("title", "subtitle", "lead"):
            hit = ban.search(d.get(k) or "") or next((re.search(re.escape(x), d.get(k) or "") for x in terms if x in (d.get(k) or "")), None)
            if hit and calls < tries:
                new = ask(STAGE_SENT, f"جمله: {d[k]}\n\nایراد: عبارت «{hit.group(0)}» ممنوع است؛ جمله را بدون آن بنویس. اگر تاریخ لازم شد: {day}").strip().strip("«»\"")
                if new and not ban.search(new) and set(NUM.findall(new)) <= set(NUM.findall(d[k]) + NUM.findall(day)) and "\n" not in new:
                    d[k], changed = new, True
        return json.dumps(d, ensure_ascii=False) if changed else None

    def as_dict(raw):
        m = re.search(r"\{.*\}", raw or "", re.S)
        d = loads_lenient(m.group(0)) if m else None
        return d if isinstance(d, dict) and all(isinstance(d.get(k), str) for k in ("title", "lead", "text")) else None

    # every step is told which commodity and producer the news is about (content agent 1405-07-22: one car-market story)
    fl = LATIN_CODE.sub("", facts or "").replace("  ", " ")  # «تختال C» -> «تختال» (content agent 1405-07-22)
    head = (fl.splitlines()[0] + "\n\n") if fl else ""
    # a small model loses a long input: the draft gets the first 10 facts; the tables and the check still see all of them
    lines = [x for x in fl.splitlines() if x.strip()]
    must = [x for x in lines[10:] if re.search(r"از آخرین معامله|ارزش معامله", x)]  # content agent 1405-07-22
    brief = src.replace(LATIN_CODE.sub(lambda m: m.group(0) if re.fullmatch(r"[TF]\d{1,2}|title|columns|rows", m.group(0)) else "", facts), "\n".join(lines[:10] + must)) if facts and len(lines) > 10 else src
    key = (lines[0] + day) if lines else ""
    FIRST_TRADE[0] = "نخستین معامله‌ی ثبت‌شده‌ی این کالا" in (facts or "")
    HAS_YTD[0] = "از ابتدای امسال" in (facts or "")
    while calls < tries:
        raw = ask(STAGE1, brief + model_hint(name))
        d0 = as_dict(raw)
        for check in ((STAGE2_MEANING, STAGE2_NUM) if FIVE_STEPS[0] else (STAGE2,)):  # step 2 (and 3 in the 5-step variant)
          if d0 and calls < tries:  # sentences the check calls wrong are cut by the code
            sents = [(pi, x) for pi, para in enumerate(d0["text"].split("\n\n")) for x in re.split(r"(?<=[.!؟])\s+", para.strip()) if x]
            ans = ask(check, "حقیقت‌ها:\n" + facts + "\n\nجمله‌ها:\n" + "\n".join(f"{n + 1}. {x}" for n, (_, x) in enumerate(sents)))
            m = re.search(r"\{.*\}", ans or "", re.S)
            bad = (loads_lenient(m.group(0)) or {}).get("bad") if m else None
            if isinstance(bad, list) and len(bad) <= len(sents) // 2:  # a check that rejects most of the text is not trusted
                bad = {int(b) for b in bad if str(b).isdigit()}
                paras = {}
                for n, (pi, x) in enumerate(sents):
                    if n + 1 not in bad:
                        paras.setdefault(pi, []).append(x)
                d0["text"] = "\n\n".join(" ".join(v) for _, v in sorted(paras.items()))
                if bad:
                    note(f"  staged {name}: check cut {len(bad)} sentence(s)")
            raw = json.dumps(d0, ensure_ascii=False)
        if raw and calls < tries:  # step 3: wording; a result that changed the numbers is not used
            new = ask(STAGE3, head + "خبر:\n" + raw + "\n\nشناسه‌ی جدول‌های مجاز: " + ", ".join(tables or {}))
            d1, d0 = as_dict(new), as_dict(raw)
            if d1 and d0 and sorted(NUM.findall(d1["text"])) == sorted(NUM.findall(d0["text"])):
                raw = new
        d0 = as_dict(raw)
        if d0 and calls < tries:  # step 4: the title alone
            why = ""
            for _ in range(3):
                if calls >= tries:
                    break
                t = ask(STAGE_TITLE, head + "\n".join(lines[:8]) + "\n\nلید خبر: " + d0["lead"] + (f"\n\nتیتر قبلی رد شد: {why}" if why else "")).strip().splitlines()
                t = clean_title(t[0]) if t else ""
                tt = {"title": t, "lead": d0["lead"], "text": d0["text"]}
                why = (off_topic(tt, facts) + " " + title_problem(t, facts) + " " + sale_problem(tt, facts)).strip() if t else "خالی"
                if not why:
                    d0["title"], d0["slug"] = t, t
                    break
        if d0:  # step 5: the source sentence is the code's job; whatever the model wrote about the source goes
            paras = [" ".join(x for x in re.split(r"(?<=[.!؟])\s+", para.strip()) if not MSD.search(x)) for para in d0["text"].split("\n\n")]
            d0["text"] = ensure_source("\n\n".join(x for x in paras if x), key)
            raw = json.dumps(d0, ensure_ascii=False)
        for fix in range(4):  # the check, then up to 3 corrections of this draft
            try:
                raw = autofix(raw, facts=facts)
                d = parse(yesterday_words(raw, day), facts, tables)
                extra =[f"«{x.group(0)}» در {k}" for k in ("title", "subtitle", "lead", "text") for x in ban.finditer(d[k])]
                if extra:
                    raise ValueError("markers: " + " | ".join(extra))
                d["title"] = clean_title(d["title"])
                off = " | ".join(x for x in (off_topic(d, facts).replace("تیتر فعل ندارد؛ تیتر را با یک فعل تمام کن", "").strip(" |"),
                                             sale_problem(d, facts), claim_problem(d, facts)) if x)
                if off:
                    raise ValueError(off)
                soft = " | ".join(x for x in (title_problem(d["title"], facts), number_problem(d["text"]),
                                              "" if NUM.search(DATE_WORDS.sub("", d["lead"])) else "لید بی‌عدد") if x)
                if soft:
                    note(f"  staged {name}: soft: {soft[:160]}")
                d["slug"] = SLUG_BAD.sub("-", re.sub(r"[،,.A-Za-z]", " ", d["title"]).strip())
                keep = set(re.findall(r"کد گرید: ([^()]+) \(", facts.splitlines()[0])[0].split("، ")) if facts and "کد گرید: " in facts.splitlines()[0] else ()
                for k in ("title", "subtitle", "lead", "text"):  # Persian, readable digits are fixed here, not asked for
                    d[k] = round_words(re.sub(r"(?<![0-9۰-۹٫.])(?:یک|۱) هزار", "هزار", readable(d[k], keep)))
                    d[k] = after_fix(d[k], facts)
                if words(d["text"]) < MIN_WORDS[0]:  # 1405-07-25: the fixes above can cut a 120-word text to 118, and validate.py then blocked every release
                    raise ValueError(f"{words(d['text'])} words after fixes")
                if not d.get("key_points"):  # content agent 1405-07-23: 12 of 20 had none
                    kp = [x for x in re.split(r"(?<=[.!؟])\s+", d["text"]) if NUM.search(DATE_WORDS.sub("", x)) and 5 <= len(x.split()) <= 25 and not MSD.search(x)]
                    if len(kp) >= 3:
                        d["key_points"] = kp[:3]
                if d.get("key_points"):  # content agent 2026-10-13: no «امروز»/«دیروز» in key_points either
                    kp = [re.sub(r"\s*-+\s*(?=[\s،.؛)]|$)", " ", round_words(re.sub(r"(?<![0-9۰-۹٫.])(?:یک|۱) هزار", "هزار", readable(yesterday_words(today_words(x, day, [True]), day))))).replace(" .", ".").strip() for x in d["key_points"]]
                    kp = [x for x in kp if not ban.search(x)]
                    if kp:
                        d["key_points"] = kp
                    else:
                        d.pop("key_points")
                note(f"  staged {name}: accepted after {calls} calls")
                try:
                    old_title = d["title"]
                    d = headline6(d, facts, ask, note)
                    if d["title"] != old_title:
                        d["slug"] = SLUG_BAD.sub("-", re.sub(r"[،,.A-Za-z]", " ", d["title"]).strip())
                except QuotaError:
                    pass
                return d
            except ValueError as e:
                err = str(e)
            if calls >= tries or fix == 3 or not raw:
                break
            fixed = repair(raw, err) if re.search(r"«[^»]+» در |عدد بیرون از حقیقت‌ها", err) else None
            if fixed:
                raw = fixed
                continue
            short = re.match(r"(\d+) words", err)
            d0 = as_dict(raw)
            if short and int(short.group(1)) < MIN_WORDS[0] and d0 and calls < tries:  # content agent 1405-07-27: 900 calls on short texts
                # content agent 1405-08-02: with 6 numbers in the text already, the new sentence comes from a fact without numbers
                full = len(NUM.findall(DATE_WORDS.sub("", d0["text"]))) >= 6
                # content agent 1405-08-03: one sentence (~15 words) rarely lifted a 72-106 word text over 120, so 25 of 37 gemma
                # drafts were dropped after all 8 calls; one call now asks for as many sentences as the text is short of
                n = max(1, -(-(MIN_WORDS[0] + 15 - int(short.group(1))) // 15))
                new = ask(STAGE_SENT, "حقیقت‌ها:\n" + "\n".join(lines[:12]) + "\n\nمتن خبر:\n" + d0["text"] +
                          f"\n\n{n} جمله‌ی تازه از حقیقت‌ها بنویس که در متن نیامده؛ فقط عددهای حقیقت‌ها. " +
                          ("متن به ۶ عدد رسیده است: جمله‌ها را از حقیقت‌هایی بساز که عدد ندارند (مثل «همه‌ی عرضه فروش رفت» یا «پیشنهادی بالاتر از پایه نیامد»)؛ "
                           "اگر چنین حقیقتی نمانده، کم‌عددترین جمله‌ها را بنویس. " if full else "") +
                          "هر جمله در یک خط؛ فقط خود جمله‌ها را برگردان.")
                add = [re.sub(r"^(?:[-•*]|[0-9۰-۹]{1,2}[.)-])\s*", "", x.strip()).strip("«»\"") for x in (new or "").splitlines()]
                add = [x for x in add if len(x.split()) >= 6 and not ban.search(x)]
                if add:
                    d0["text"] = d0["text"].rstrip() + (" " if paragraphs(d0["text"]) >= 4 else "\n\n") + " ".join(add)
                    raw = json.dumps(d0, ensure_ascii=False)
                    continue
            if short and int(short.group(1)) < 140:
                err += f" (متن {short.group(1)} کلمه است؛ دست‌کم {150 - int(short.group(1))} کلمه‌ی دیگر از حقیقت‌ها اضافه کن و ۳ پاراگراف با \\n\\n بساز)"
            elif short and int(short.group(1)) > 220:
                err += f" (متن {short.group(1)} کلمه است؛ کم‌اهمیت‌ترین جمله‌ها را حذف کن تا به ۱۸۰ کلمه برسد)"
            raw = ask(STAGE_FIX, src + "\n\nخبر:\n" + raw + f"\n\nایرادها: {err}") or raw
        if raw:  # backend 1405-07-28: no answer at all (gateway down) is not a rejected draft; 5,974 empty rows on 07-27 hid the real ones
            note(f"  staged {name}: draft dropped after {calls} calls: {str(err)[:160]}")
            keep_rejected(name, facts, raw, err, calls)
    raise ValueError(f"staged: {calls} calls, last: {err}")


TODAY = re.compile(r"(?<![؀-ۿ])امروز(?![؀-ۿ])")


def day_parts(day_line):
    """«F1. روز: دوشنبه ۲۰ مهر ۱۴۰۵.» -> («دوشنبه», «دوشنبه ۲۰ مهر»)"""
    m = re.search(r"((?:یک|دو|سه|چهار|پنج)?[‌ ]?شنبه|جمعه)\s+([۰-۹0-9]+\s+\S+)", day_line or "")
    return (m.group(1), f"{m.group(1)} {m.group(2).rstrip('.،')}") if m else ("", "")


DUP_DATE = re.compile(r"([0-9۰-۹]{1,2} (?:فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند))(?: [0-9۰-۹]{4})?[،,]? \1(?![0-9۰-۹])")
MONTHS = "فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|آذر|دی|بهمن|اسفند"


def claim_problem(d, facts):
    """content agent 1405-07-26: claims the facts do not carry - «ثابت ماند» after a price change, a superlative, «تقاضا برابر عرضه» at 25%."""
    t, f, out = " ".join(d.get(k) or "" for k in ("title", "subtitle", "lead", "text")), facts or "", []
    if re.search(r"ثابت (?:ماند|بود)|تغییری نکرد|بدون تغییر", t) and not re.search(r"ثابت ماند|تغییری نکرد|ثابت مانده", f):
        out.append("«ثابت» نوشته شده ولی قیمت نسبت به آخرین معامله تغییر کرده")
    for m in set(re.findall(r"(?:گران|ارزان|بیش|کم|بالا|پایین)[‌ ]?ترین", t)):
        if m.replace(" ", "‌").replace("‌", "") not in f.replace("‌", ""):
            out.append(f"«{m}» در حقیقت‌ها نیست")
    if re.search(r"تقاضا(?:ی)? (?:برابر|معادل) (?:با )?عرضه", t) and re.search(r"تقاضا فقط [0-9۰-۹٫]+ درصد عرضه", f):
        out.append("تقاضا کمتر از عرضه بود، نه برابر")
    # content agent 1405-07-27: demand written as the sold amount (AAK 4,330 t «فروخته شد», real 900 t)
    sold = re.search(r"مقدار فروخته‌شده (\S+(?: هزار(?: و \S+)?)?) ", f) or re.search(r"تمام عرضه \((\S+(?: هزار(?: و \S+)?)?) ", f)
    if sold:
        ok = fa_value(sold.group(1))
        for m in re.finditer(r"([0-9۰-۹][0-9۰-۹٫.,]*(?: (?:هزار|میلیون))?(?: و [0-9۰-۹][0-9۰-۹٫.,]*)?) تن(?:[^.،؛]{0,30}?)(?:فروخت|فروخته شد|فروش رفت|معامله شد)", t):
            v = fa_value(m.group(1))
            if v is not None and ok is not None and abs(v - ok) > 1e-6:
                out.append(f"«{m.group(1)} تن» با فعل فروش آمده ولی فروش {sold.group(1)} تن است")
    if re.search(r"خواهد (?:شد|گذاشت|داشت|بود|کرد|رسید)|تاثیر خواهد|تأثیر خواهد", t):
        out.append("پیش‌بینی آینده در حقیقت‌ها نیست")
    for m in re.finditer(r"([0-9۰-۹]+) (?:تولیدکننده|عرضه‌کننده|فروشنده|شرکت)(?:‌ی)? دیگر", t):
        if not re.search(rf"(?<![0-9۰-۹]){m.group(1)}(?![0-9۰-۹]) (?:تولیدکننده|عرضه‌کننده|فروشنده|شرکت)", f):
            out.append(f"«{m.group(0)}» در حقیقت‌ها نیست")
    streak = re.search(r"([0-9۰-۹]+) معامله‌ی پیاپی", f)
    for m in re.finditer(r"([0-9۰-۹]+|یک|دو|سه|چهار|پنج|شش) (?:معامله|هفته|جلسه)(?:‌ی)? (?:پیاپی|متوالی)", t):
        if not streak or fa_value(m.group(1)) != fa_value(streak.group(1)):
            out.append(f"«{m.group(0)}» با شمار حقیقت‌ها نمی‌خواند")
    return " | ".join(out)


WORDNUM = {"یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6}


def fa_value(x):
    """«۴ هزار و ۳۳۰» -> 4330.0; «۳۹٫۶» -> 39.6; None when it is not a number."""
    x = (x or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫", "0123456789.")).replace(",", "")
    if x in WORDNUM:
        return float(WORDNUM[x])
    m = re.fullmatch(r"([0-9.]+)(?: (هزار|میلیون))?(?: و ([0-9.]+))?", x)
    if not m:
        return None
    v = float(m.group(1)) * {"هزار": 1e3, "میلیون": 1e6, None: 1}[m.group(2)]
    return v + (float(m.group(3)) if m.group(3) else 0)


def after_fix(x, facts):
    """content agent 1405-07-25, last pass on the final text: «۲۲ مهر ۲۲ مهر» once; the previous trade never gets today's date;
    «تقاضا برابر عرضه» gets the ratio from the facts; a lead does not start with «. روز معامله:»."""
    x = DUP_DATE.sub(r"\1", x)
    # content agent 1405-07-25 (8 of 24 gemma news): «شنبه شنبه ۲۵ مهر», «شنبه ۲۵ مهر شنبه ۲۵ مهر ۱۴۰۵» -> once
    x = re.sub(r"(?<!\S)((?:\S+ ){0,3}\S+)(?:،? \1)+(?!\S)", r"\1", x)
    # «در تاریخ» with no date after it (the model had no previous-trade date): the words go
    x = re.sub(r"\s*(?:در|به) تاریخ(?!\s*(?:[0-9۰-۹]|شنبه|یکشنبه|دوشنبه|سه‌شنبه|چهارشنبه|پنجشنبه|جمعه))", "", x)
    # «۱۲۱ میلیارد. ۶۸۲ میلیون» -> «۱۲۱ میلیارد و ۶۸۲ میلیون»
    x = re.sub(r"(میلیارد|میلیون|هزار)\. (?=[0-9۰-۹])", r"\1 و ", x)
    x = re.sub(r"^[\s.،:؛]+", "", x)
    x = re.sub(r"^روز معامله:\s*", "", x)
    day = re.search(rf"روز معامله: \S+ ([0-9۰-۹]{{1,2}} (?:{MONTHS}))", facts or "")
    last = re.search(rf"آخرین معامله‌ی قبلی در ([0-9۰-۹]{{1,2}} (?:{MONTHS}))", facts or "")
    if day and last:
        x = re.sub(rf"(آخرین معامله(?:‌ی)?(?: قبلی)?(?: این کالا)?(?: در| در تاریخ| روز)?) (?:(?:شنبه|یکشنبه|دوشنبه|سه‌شنبه|چهارشنبه|پنجشنبه|جمعه) )?{day.group(1)}(?: [0-9۰-۹]{{4}})?",
                   lambda m: m.group(1) + " " + last.group(1), x)
    if last:  # «آخرین معامله در چهارشنبه، ۲۲ مهر» and «معامله‌ی قبلی …» too
        x = re.sub(rf"((?:آخرین معامله|معامله‌ی قبلی)[^.]{{0,25}}?) (?:(?:شنبه|یکشنبه|دوشنبه|سه‌شنبه|چهارشنبه|پنجشنبه|جمعه)،? )?{day.group(1) if day else '@@'}(?: [0-9۰-۹]{{4}})?",
                   lambda m: m.group(1) + " " + last.group(1), x)
    x = fix_weekdays(x, facts)
    ratio = re.search(r"تقاضا ([0-9۰-۹٫]+) برابر عرضه بود", facts or "")
    if ratio:
        x = re.sub(r"(تقاضا(?:ی)?) برابر عرضه", lambda m: m.group(1) + " " + ratio.group(1) + " برابر عرضه", x)
    x = re.sub(r"(?<![0-9۰-۹٫])(?:یک|۱|1) برابر عرضه", "برابر عرضه", x)
    sold = re.search(r"عرضه فروش رفت \(([0-9۰-۹]+) درصد\)", facts or "")
    if sold:
        x = re.sub(r"[0-9۰-۹]+(?= درصد عرضه(?: را)? (?:فروخت|فروخته شد|فروش رفت))", sold.group(1), x)
    if YTD_LINE in x and not re.search(r"امسال[^.]{0,60}[0-9۰-۹]", x.replace(YTD_LINE, "")):
        x = x.replace(YTD_LINE, SOURCE_LINES[0])
    x = re.sub(r"(?:در )?روز جاری", "در این روز", x)
    # content agent 1405-07-26 (~30 of 121): «این معامله یعنی تقاضای مناسب…» - a «یعنی» sentence with no number is filler and goes
    x = "\n\n".join(" ".join(t for t in re.split(r"(?<=[.!؟])\s+", para) if not (re.match(r"(?:این(?: \S+){0,2} )?یعنی", t) and not NUM.search(t)))
                    for para in x.split("\n\n")).strip()
    # content agent 1405-07-27: «یعنی» at most once (writing guide 2, part 1) - a later sentence-initial «یعنی» is dropped from the sentence
    seen = [0]

    def once(m):
        seen[0] += 1
        return m.group(0) if seen[0] == 1 else m.group(1)
    x = re.sub(r"(^|(?<=[.!؟] )|(?<=\n))(?:این(?: \S+){0,2} )?یعنی ", once, x)
    return x


YTD_LINE = SOURCE_LINES[13]
WEEK = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
FA_D = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _doy(d, mon):  # day of the Jalali year; months 1-6 have 31 days, 7-11 have 30
    m = MONTHS.split("|").index(mon)
    return (m * 31 if m < 6 else 186 + (m - 6) * 30) + d


def fix_weekdays(x, facts):
    """content agent 1405-07-25: «چهارشنبه ۲۱ مهر» for a Tuesday; the code names the day from today's date in the facts."""
    m = re.search(rf"روز معامله: (\S+) ([0-9۰-۹]{{1,2}}) ({MONTHS})", facts or "")
    if not m or m.group(1) not in WEEK:
        return x
    base, w0 = _doy(int(m.group(2).translate(FA_D)), m.group(3)), WEEK.index(m.group(1))

    def name(t):
        diff = _doy(int(t.group(3).translate(FA_D)), t.group(4)) - base
        return t.group(0) if abs(diff) > 60 else WEEK[(w0 + diff) % 7] + t.group(2) + t.group(3) + " " + t.group(4)
    return re.sub(rf"(شنبه|یکشنبه|دوشنبه|سه‌شنبه|چهارشنبه|پنجشنبه|جمعه)(،? )([0-9۰-۹]{{1,2}}) ({MONTHS})", name, x)


def day_of(facts):
    """the fact line that names the day. It is not the first line in the kala and energy facts (content agent 1405-07-22:
    «امروز» stayed in 79 of 93 news because the first line, the product name, was read as the day)."""
    lines = (facts or "").splitlines()
    return next((x for x in lines[:8] if "روز" in x and day_parts(x)[0]), lines[0] if lines else "")


def today_words(text, day_line, seen=None):
    """owner 2026-10-12: «امروز» becomes the weekday and date the first time and only the weekday after that."""
    week, full = (x.strip() for x in day_parts(day_line))  # 1405-08-02: day_parts keeps a leading space, so the check below never matched
    if not week or not text:
        return text
    seen = seen if seen is not None else [full in text]

    def one(m):
        rest = text[m.end():m.end() + len(full) + 2].strip()
        if rest.startswith(week):  # «امروز دوشنبه ۲۰ مهر» already names the day
            return ""
        out = week if seen[0] else full
        seen[0] = True
        return out
    out = re.sub(r"  +", " ", TODAY.sub(one, text)).strip()
    # content agent 1405-08-02: «شنبه ۲ آبان شنبه ۲ آبان» in 3 news (the model's date next to the replaced «امروز»)
    return re.sub(rf"({re.escape(full)})(?:[\s،,(]*{re.escape(full)}\)?)+", r"\1", out) if full else out


WEEK = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
BIG = re.compile(r"(?<![0-9۰-۹٫.,٬/])[0-9۰-۹]{5,}(?![0-9۰-۹٫.,٬/])")
NEG = re.compile(r"(?<![0-9۰-۹A-Za-z؀-ۿ])[-−]([0-9۰-۹]+(?:[.٫][0-9۰-۹]+)?)")


def yesterday_words(text, day_line):
    """content agent 2026-10-13: «دیروز» becomes the name of the day before (not when that is a Friday: left to the ban)."""
    week = day_parts(day_line)[0].replace("‌", "").replace(" ", "")
    i = next((k for k, x in enumerate(WEEK) if x.replace("‌", "") == week), None)
    if i is None or not text or i == 0:
        return text
    return re.sub(r"(?<![؀-ۿ])دیروز(?![؀-ۿ])", WEEK[i - 1], text)


def readable(t, keep=()):
    """content agent 2026-10-13: «۲۶۰ هزار و ۶۰۰» not 260600, «۱٫۲۹-» not -1.29, «۷٫۲» not 7.2"""
    def big(m):
        if m.group(0).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")) in keep:  # a grade code stays as it is
            return m.group(0)
        v, parts = int(m.group(0)), []
        for u, name in ((10 ** 9, "میلیارد"), (10 ** 6, "میلیون"), (1000, "هزار")):
            q, v = divmod(v, u)
            if q:
                parts.append(f"{q} {name}")
        return " و ".join(parts + ([str(v)] if v else []))
    t = BIG.sub(big, NEG.sub(r"\1-", t))
    return re.sub(r"(?<=[0-9۰-۹])[.](?=[0-9۰-۹])", "٫", t).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
ROUND = re.compile(r"(?<![0-9۰-۹٫])(حدود )?((?:یک|[0-9۰-۹]+) (?:میلیارد|میلیون)) و ([0-9۰-۹]+) (میلیون|هزار) و ([0-9۰-۹]+)(?: (هزار))?(?![0-9۰-۹٫]| هزار| میلیون)")


def round_words(t):
    """content agent 1405-07-23: «یک میلیون و ۱۷۰ هزار و ۹۵۷ ریال» -> «حدود یک میلیون و ۱۷۱ هزار ریال» in the text;
    the exact number stays in the table."""
    def one(m):
        mid, tail = int(m.group(3).translate(FA)), int(m.group(5).translate(FA))
        frac = tail / 1000 if m.group(4) == "هزار" or m.group(6) else tail / 10 ** 6
        if frac >= 0.5 and mid < 999:
            mid += 1
        return f"حدود {m.group(2)} و {mid} {m.group(4)}".translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    return ROUND.sub(one, t)


def save_scenarios(items, subject, date_fa):
    """style.md 2-15: every scenario is also kept as one JSON line in data/scenarios.jsonl for the monthly judging."""
    if items:
        with open(os.path.join(ROOT, "data", "scenarios.jsonl"), "a", encoding="utf-8") as f:
            for x in items:
                f.write(json.dumps({"تاریخ_ثبت": date_fa, "موضوع_نماد": subject, "وضعیت": "باز", **x}, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="news")
    ap.add_argument("--market", default="kala", choices=("kala", "kish", "energy"))  # kish = export market (owner 1405-07-15), energy = Iran Energy Exchange (owner 1405-07-16)
    a = ap.parse_args()
    energy = a.market == "energy"
    MARKET[0] = a.market
    kish = a.market == "kish" or energy  # energy runs the Kish path (one news per settled traded row), with its own facts
    if kish:
        MIN_WORDS[0] = 80 if a.market == "kish" else 120  # content agent 1405-07-27: a kish news needs ~100 words
        global RULES
        import facts_kish
        import facts_energy
        import fetch_energy
        RULES = RULES_ENERGY if energy else RULES_KISH
        k = json.load(open(os.path.join(ROOT, "data", a.market, "today.json"), encoding="utf-8"))
        kh = json.load(open(os.path.join(ROOT, "data", a.market, "history.json"), encoding="utf-8"))
        date, date_fa = k["date"], k["trade_date"]
        rows, seen = [], {}
        stab_path = os.path.join(ROOT, "data", a.market, f"stable-{date}.json")
        stab = json.load(open(stab_path, encoding="utf-8")) if os.path.exists(stab_path) else {}
        now_utc = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        # owner 1405-07-15: the Kish session lasts ~20 minutes - news are written only after the whole day stopped changing
        dfp = "|".join(sorted(f"{x.get('symbol')}:{x.get('trade_volume')}:{x.get('weighted_price')}:{x.get('total_value')}" for x in k["items"]))
        if stab.get("__day__", [None])[0] != dfp:
            stab["__day__"] = [dfp, now_utc]
        # hall_end (kala_kish_export 1405-07-15, e.g. «11:42:00»): the hall is closed once the data was built 5+ min after it
        he, lr = k.get("hall_end"), k.get("last_run_utc")
        hall_done = False
        if he and lr:
            tz = dt.timezone(dt.timedelta(hours=3, minutes=30))
            end = dt.datetime.combine(dt.date.fromisoformat(date), dt.time.fromisoformat(he), tz)
            hall_done = dt.datetime.fromisoformat(lr) >= end + dt.timedelta(minutes=5)
        closed = hall_done or date < dt.date.today().isoformat() or             (dt.datetime.fromisoformat(now_utc) - dt.datetime.fromisoformat(stab["__day__"][1])).total_seconds() >= STABLE_MIN * 60
        unsettled = 0
        # a symbol sold at two deliveries is two news; a fixed order keeps «-2» on the same delivery every round (content agent 1405-07-27)
        for r in sorted(k["items"], key=lambda r: (r.get("symbol") or "", r.get("delivery_place") or "", r.get("contract_type") or "")):
            if (r.get("trade_volume") or 0) <= 0:
                continue
            r["series_key"] = fetch_energy.key(r) if energy else r["symbol"]
            # owner 1405-07-15: during the day only a settled trade is written - a row must be unchanged for STABLE_MIN minutes
            fp = "|".join(str(r.get(x)) for x in ("trade_volume", "demand_volume", "weighted_price", "total_value"))
            key = f"{r['symbol']}|{r.get('delivery_place')}|{r.get('contract_type')}"
            old = stab.get(key)
            if not old or old[0] != fp:
                stab[key] = [fp, now_utc]
            if not closed and (dt.datetime.fromisoformat(now_utc) - dt.datetime.fromisoformat(stab[key][1])).total_seconds() < STABLE_MIN * 60:
                unsettled += 1
                continue
            n = seen[r["symbol"]] = seen.get(r["symbol"], 0) + 1
            if n > 1:  # same symbol twice in a day (other contract/delivery): one news each
                r["symbol"] = f"{r['symbol']}-{n}"
            rows.append(r)
        json.dump(stab, open(stab_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print(f"{a.market}: {len(rows)} settled rows, {unsettled} still changing (wait {STABLE_MIN} min unchanged)")
    else:
        latest = json.load(open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8"))
        date, date_fa = latest["date"], latest["date_fa"]
        rows = json.load(open(os.path.join(ROOT, "data", date, "today.json"), encoding="utf-8"))["rows"]
        # owner 1405-07-16: no news for the side hall («تالار فرعی»): those symbols have no price and no page on the site
        rows = [r for r in rows if "فرعی" not in (r.get("talar") or "")]
        # content agent 1405-07-25: a symbol is offered several times a day and today.json sums them; the news was written from
        # the first figure (KHCO 80 t, final 4040 t). A row is written only when unchanged across two data builds, or after the last fetch.
        stab_path = os.path.join(ROOT, "data", date, "stable.json")
        stab = json.load(open(stab_path, encoding="utf-8")) if os.path.exists(stab_path) else {}
        ver = __import__("hashlib").md5(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if stab.get("__ver__", [None])[0] != ver:
            stab["__ver__"] = [ver, stab.get("__ver__", [None, 0])[1] + 1]
        n_ver = stab["__ver__"][1]
        teh = dt.datetime.now(dt.timezone(dt.timedelta(hours=3, minutes=30)))
        closed = date < teh.date().isoformat() or teh.hour >= 19  # the last fetch is 18:30 Tehran
        shut = closed_halls(date_fa, teh)
        keep, unsettled = [], 0
        for r in rows:
            fp = "|".join(str(r.get(x)) for x in ("offered_qty", "traded_qty", "demand_qty", "weighted_price", "trade_value"))
            if stab.get(r.get("symbol"), [None])[0] != fp:
                stab[r.get("symbol")] = [fp, n_ver]
            # content agent 1405-07-26: 13 news were written from rows still filling up after a quiet build, so an open hall
            # always waits; a closed hall still needs two unchanged builds (shut is None when the hours could not be read)
            if closed or ((shut is None or r.get("talar") in shut) and n_ver > stab[r.get("symbol")][1]):
                keep.append(r)
            else:
                unsettled += 1
        json.dump(stab, open(stab_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        print(f"kala: {len(keep)} settled rows, {unsettled} wait for the next data build")
        rows = keep
    out_dir = os.path.join(ROOT, a.out)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{date}.json")
    doc = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"date_fa": date_fa, "date": date, "items": []}
    if not kish:  # content agent 1405-07-26: a row that changed after its news was written gets a new news (release.py replaces the page)
        now_fp = {r.get("symbol"): row_fp(r) for r in rows}
        stale = [i for i in doc["items"] if i.get("row_fp") and i["symbol"] in now_fp and now_fp[i["symbol"]] != i["row_fp"]]
        for i in stale:
            note(f"rewrite {i['symbol']}: data changed after it was written ({i['row_fp']} -> {now_fp[i['symbol']]})")
            doc["items"].remove(i)
    done = {i["symbol"] for i in doc["items"]}
    subs = {i["subtitle"] for i in doc["items"]}
    TODAY_TITLES[:] = [i["title"] for i in doc["items"] if i.get("title")]
    todo = [r for r in rows if r.get("symbol") not in done and "فرعی" not in (r.get("talar") or "")  # owner 1405-07-28: no news for the side hall (no price/value in the source)
            and not ((r.get("traded_qty") or 0) > 0 and not r.get("weighted_price"))]
    provs = providers()
    if not provs:
        sys.exit("no model key found (GEMINI_API_KEY / OPENROUTER_API_KEY / GROQ_API_KEY / ...)")
    # one worker per provider (each has its own free quota) so they write in parallel from one shared list;
    # the most valuable trades go to the strongest providers, the weaker ones start from the other end
    # the owner's blind rating (5 best of 12 models, 2026-09-30/10-01) writes the important half; every other model the rest
    groups = {}
    for name, call in provs:
        if name in REPORT_ONLY or name.startswith("gemini:"):  # every Gemini model is kept for the daily report and macro news (owner 2026-10-05)
            continue
        groups.setdefault(name.split(":")[0] + ("/paid" if name in PAID else "/strong" if name in STRONG else "/backup" if name in BACKUP else ""), []).append((name, call))
    # importance tiers come from data/importance.json (tools/importance.py): 0 important (top half by value this year), 1 the rest.
    # A strong-model worker writes only important symbols, every other worker only the rest.
    if kish:  # top half of the day's dollar value -> strong models (rial-export rows are never compared, they go to the rest)
        vk = "total_value_rial" if energy else "total_value_usd"  # energy: every row has a rial value (dollar and premium rows too)
        byv = sorted(rows, key=lambda r: -(r.get(vk) or 0))
        tier = {r["symbol"]: 0 if i < len(byv) // 2 and (energy or r.get("is_usd")) else 1 for i, r in enumerate(byv)}
        value = {r["symbol"]: r.get(vk) or 0 for r in rows}
        seen_t = {}
    else:
        table = importance.update(date, rows)
        tier = {s: e["tier"] for s, e in table.items()}
        value = {s: e["value"] for s, e in table.items()}
        seen_t = {s: e.get("seen_t", 0) for s, e in table.items()}
    todo.sort(key=lambda r: (tier.get(r["symbol"], 1), -value.get(r["symbol"], 0)))  # most important first
    # backend 1405-07-30: one symbol no model could write was tried again by every run all night (gemma: 20 calls a run, 10x the
    # owner's token bill); after GIVE_UP failed runs it waits until its row changes or the next day
    give_path = os.path.join(ROOT, "news-test", f"giveup-{a.market}-{date}.json")
    give = json.load(open(give_path, encoding="utf-8")) if os.path.exists(give_path) else {}
    fp_of = {r["symbol"]: row_fp(r) if not kish else json.dumps(r, sort_keys=True, ensure_ascii=False)[:4000] for r in todo}
    skip = [r for r in todo if give.get(r["symbol"], {}).get("fp") == fp_of[r["symbol"]] and give[r["symbol"]]["runs"] >= GIVE_UP]
    if skip:
        note(f"given up for today (failed {GIVE_UP} runs, row unchanged): {', '.join(r['symbol'] for r in skip)}")
        todo[:] = [r for r in todo if r not in skip]
    del todo[a.limit or len(todo):]
    note(f"{len(todo)} symbols to write; {len(groups)} parallel workers: {', '.join(groups)}")
    lock = threading.Lock()
    run_t0 = time.time()  # kish and energy keep no first-seen time: their 10 minutes start with the run
    tried = {r["symbol"]: set() for r in todo}
    stats = {"ok": 0}
    seen_facts = {}

    def write_one(r, models):
        sym = r["symbol"]
        with lock:
            recent = list(subs)[-5:]
        if energy:
            facts, tables = facts_energy.build(r, kh["series"].get(r["series_key"], []), date_fa, date)
        elif kish:
            facts, tables = facts_kish.build(r, kh["series"].get(r["series_key"], []), rows, date_fa, date)
        else:
            hist_path = os.path.join(ROOT, "data", date, "symbols", f"{sym}.json")
            sd = json.load(open(hist_path, encoding="utf-8")) if os.path.exists(hist_path) else {}
            hist = sd.get("history", [])
            peers = [p for p in rows if p.get("goods_name") == r.get("goods_name")]
            facts, tables = facts_kala.build(r, hist, peers, date_fa, date, sd.get("ytd_value"))
        with lock:
            seen_facts[sym] = (facts, tables)  # backend 1405-07-30: a symbol given up on goes to the content agent with its facts
        prompt = ("حقیقت‌ها (فقط از این‌ها انتخاب کن؛ عددها را عیناً بنویس):\n" + facts +
                  "\n\nجدول‌های پیشنهادی (یکی را انتخاب کن):\n" + json.dumps(tables, ensure_ascii=False) +
                  f"\n\nزاویه‌ی خبرهای قبلی امروز را تکرار نکن. سوتیترهای قبلی: {' | '.join(recent)}")
        for name, call in models:
            if name in exhausted():
                continue
            t0 = time.time()
            try:
                d = staged(name, call, prompt, facts, tables)  # owner 1405-07-25: every model writes in 5 steps; 1405-08-01: no market rules beside style_kala.md v3
                raw = retry(call, prompt + model_hint(name)) if d is None else ""
                for attempt in range(3 if d is None else 0):  # section 6 of style.md: every hit goes back to the model, up to 2 rewrites
                    try:
                        d = parse(raw, facts, tables)
                        break
                    except ValueError as e:
                        if attempt == 2 or not re.search(r"words|markers|table", str(e)):
                            raise
                        short = re.match(r"(\d+) words", str(e))
                        if short and int(short.group(1)) < 120:  # GLM and others stop short: say how many words are missing
                            e = f"{e} (متن تو {short.group(1)} کلمه است؛ دست‌کم {150 - int(short.group(1))} کلمه‌ی دیگر از حقیقت‌ها اضافه کن)"
                        raw = retry(call, prompt + "\n\nپیش‌نویس قبلی تو:\n" + raw + f"\n\nایرادها: {e}. همان خبر را با رفع همه‌ی این ایرادها "
                                    "(text بین ۱۴۰ تا ۲۰۰ کلمه در ۲ یا ۳ پاراگراف، فقط با عددهای حقیقت‌ها) بازنویسی کن و فقط JSON برگردان.")
                with lock:
                    if d["subtitle"] in subs:
                        raise ValueError("repeated subtitle")
                    TODAY_TITLES.append(d["title"])
                    if energy:
                        doc["items"].append({"symbol": sym, "trade_date": date_fa, "market": "energy",
                                             "commodity": facts_energy.clean(r.get("goods_name")), "producer": facts_energy.clean(r.get("producer")),
                                             "ring": facts_energy.market_fa(r), "board": r.get("board_name", ""),
                                             "commodity_key": r["series_key"], "price_kind": facts_energy.kind(r), **d, "model": name})
                    elif kish:
                        doc["items"].append({"symbol": sym, "trade_date": date_fa, "market": "kish",
                                             "commodity": facts_kish.clean_name(r.get("goods_name")), "producer": r.get("producer", ""),
                                             "delivery": facts_kish.delivery(r.get("delivery_place"))[0],
                                             "currency": "USD" if r.get("is_usd") else "rial-export", **d, "model": name})
                    else:
                        doc["items"].append({"symbol": sym, "trade_date": date_fa, "commodity": r.get("goods_name", ""),
                                             "hall": r.get("talar", ""), "producer": r.get("producer_name", ""), **d, "model": name, "row_fp": row_fp(r)})
                    subs.add(d["subtitle"])
                    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    stats["ok"] += 1
                    note(f"OK {sym} <- {name} ({time.time() - t0:.0f}s)")
                    if os.environ.get("PUSH_EACH"):
                        subprocess.run('git add -A news-test queue data/importance.json data/scenarios.jsonl && git commit -qm "queue: progress" && (git pull -q --rebase --autostash || git rebase --abort) && git push -q',
                                       shell=True, cwd=ROOT, check=False, capture_output=True)
                return True
            except Exception as e:  # noqa: BLE001 - any failure means: try the next model of this provider
                if isinstance(e, QuotaError):
                    with lock:
                        daily = bool(DAILY_RE.search(str(e)))  # a bare name = the whole day (see exhausted())
                        open(EXHAUSTED_FILE, "a", encoding="utf-8").write(f"{name}\n" if daily else f"{name} {time.time():.0f}\n")
                    note(f"  {name}: quota used up - skipped " + ("until tomorrow" if daily else "for an hour"))
                note(f"  {sym} {name}: {time.time() - t0:.0f}s {type(e).__name__}: {str(e)[:220]}")
                time.sleep(2)
        return False

    def worker(gname, models, lvl):
        fails = 0
        while (fails < 3 or gname.endswith("/paid")) and not (gname.endswith("/paid") and fails >= PAID_STOP):  # a provider that fails 3 symbols in a row (quota / broken) stops; the paid one never does (owner 2026-10-12)
            with lock:
                live = [m for m in models if m[0] not in exhausted()]
                pick = [r for r in todo if gname not in tried[r["symbol"]] and tier.get(r["symbol"], 1) == lvl]
                if gname.endswith("/backup"):
                    strong_out = not any(t.is_alive() for g, t in zip(groups, threads) if g.endswith("/strong"))
                    late = [r for r in todo if gname not in tried[r["symbol"]] and tier.get(r["symbol"], 1) == 0
                            and (strong_out or time.time() - seen_t.get(r["symbol"], time.time()) > BACKUP_WAIT)]
                    pick = late + pick  # a waiting important symbol comes before the rest
                # a backup worker with nothing to pick waits while a strong worker is still busy: the important symbols a strong
                # model fails on come back to the list minutes later, and the backups had already left by then (kish 2026-10-11)
                if gname.endswith("/paid"):
                    pick = [r for r in todo if gname not in tried[r["symbol"]]
                            and time.time() - (seen_t.get(r["symbol"]) or run_t0) > PAID_WAIT]
                    others = any(t.is_alive() for g, t in zip(groups, threads) if not g.endswith("/paid"))
                    if not live or (not pick and not others and not any(gname not in tried[r["symbol"]] for r in todo)):
                        return
                elif not live or (not pick and (not gname.endswith("/backup") or strong_out)):
                    return
                r = pick[0] if pick else None
                if r is not None:
                    todo.remove(r)
                    tried[r["symbol"]].add(gname)
            if r is None:
                time.sleep(5)
                continue
            if write_one(r, live):
                fails = 0
            else:
                fails += 1
                if gname.endswith("/paid") and fails >= PAID_STOP:
                    note(f"STOP {gname}: {fails} symbols in a row without one accepted news - the paid model rests for an hour")
                    with lock:  # the next 30-min package would start it again; the hour mark in the exhausted file carries over
                        open(EXHAUSTED_FILE, "a", encoding="utf-8").write("".join(f"{m[0]} {time.time():.0f}\n" for m in models))
                with lock:
                    if any(g not in tried[r["symbol"]] and (lvl_of(g) == tier.get(r["symbol"], 1) or g.endswith(("/backup", "/paid"))) for g in groups):
                        todo.append(r)  # another provider may still write it
            time.sleep(4)  # stay under free-tier rate limits

    def lvl_of(g):
        return 0 if g.endswith("/strong") else 1

    def save_give():
        written = {i["symbol"] for i in doc["items"]}
        for sym in tried:
            if tried[sym] and sym not in written:
                e = give.get(sym, {})
                give[sym] = {"fp": fp_of[sym], "runs": (e.get("runs", 0) if e.get("fp") == fp_of[sym] else 0) + 1}
                if sym in seen_facts:
                    give[sym]["facts"], give[sym]["tables"] = seen_facts[sym]
        if give:
            os.makedirs(os.path.dirname(give_path), exist_ok=True)
            json.dump(give, open(give_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)

    # backend 1405-08-03: every package of the night hit the 30-min timeout before this point, so the give-up file was never
    # written and gemma retried the same symbols all night; a timeout (SIGTERM) now saves it before the process ends
    def on_term(*_):
        with lock:
            save_give()
        print("timeout: give-up file saved", flush=True)
        os._exit(0)
    signal.signal(signal.SIGTERM, on_term)
    threads = [threading.Thread(target=worker, args=(g, m, lvl_of(g)), daemon=True) for g, m in groups.items()]
    for t in threads:
        t.start()
    for t in threads:
        while t.is_alive():
            t.join(1)
    ok = stats["ok"]
    save_give()
    print(f"written {ok}/{len(tried)}; total in file {len(doc['items'])}")


if __name__ == "__main__":
    main()
