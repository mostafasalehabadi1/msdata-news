"""write_free.py - writes symbol news with free LLMs (Gemini free tier + OpenRouter :free models), no Claude.

For each symbol in data/<date>/today.json that has no news yet, ask the providers in order; the first answer
that passes the same checks as tools/validate.py is kept, otherwise the next model is tried. Symbols that fail
on every model stay in the queue for the next run.

env (any subset): GITHUB_TOKEN (GitHub Models, free in Actions), GEMINI_API_KEY, OPENROUTER_API_KEY,
GROQ_API_KEY, MISTRAL_API_KEY.
usage: python tools/write_free.py [--limit N] [--out news]
"""
import argparse
import json
import os
import re
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
STYLE_KALA = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "style_kala.md"), encoding="utf-8").read()
# symbol news (owner 2026-10-05): the old template without clichés; every fact and table is built in code (facts_kala.py)
RULES = ("تو خبرنگار بورس کالای msdata.ir هستی و برای یک نماد بازار فیزیکی بورس کالا یک خبر فارسی می‌نویسی.\n\n" + STYLE_KALA +
         "\n\nفقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text, table و هیچ متن دیگری. "
         "slug فارسی با خط تیره، بدون فاصله و علامت. table فقط شناسه‌ی جدول انتخابی (مثلاً \"T2\").")


def post(url, key, model, prompt, extra=None):
    body = {"model": model, "temperature": 0.7,
            "messages": [{"role": "system", "content": RULES_GEMMA if RULES_GEMMA and model.startswith("gemma") else RULES},
                         {"role": "user", "content": prompt}]}
    if "z.ai" in url:
        body["thinking"] = {"type": "disabled"}  # GLM thinks by default: slow (timeouts) and it spends the answer budget on reasoning
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
STRONG = {"llm7:DeepSeek-V4-Flash-0731", "cohere:command-a-03-2025", "kilo:dots-studio/dots-3-note-preview:free",
          "hf:deepseek-ai/DeepSeek-V3.1", "kilo:stepfun/step-3.7-flash:free"}
# the next three by rating: write the important half too, but only when every strong model is out or a symbol waited 2h
BACKUP = {"zai:glm-4.7-flash", "zai:glm-4.5-flash", "kilo:nvidia/nemotron-3-ultra-550b-a55b:free", "cf:@cf/meta/llama-3.3-70b-instruct-fp8-fast"}
BACKUP_WAIT = 2 * 3600


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
            out.append(("openrouter:" + m, lambda p, m=m: post(OR_URL, key, m, p, {"X-Title": "msdata-news"})))
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
                out.append(("groq:" + m, lambda p, m=m: post(GROQ_URL, key, m, p)))
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
                             ("LLM7_API_KEY", "https://api.llm7.io/v1/chat/completions", ("DeepSeek-V4-Flash-0731", "minimax-m2.7", "mistral-Nemo-Instruct-2407")),
                             ("COHERE_API_KEY", "https://api.cohere.ai/compatibility/v1/chat/completions", ("command-a-03-2025",))):
        if os.environ.get(env):
            for m in models:
                out.append((env.split("_")[0].lower() + ":" + m, lambda p, m=m, u=url, e=env: post(u, os.environ[e], m, p)))
    for m in ("Qwen3.5-397B-A17B", "gpt-oss-120b", "Meta-Llama-3_3-70B-Instruct"):  # OVH: no signup, no key
        out.append(("ovh:" + m, lambda p, m=m: post("https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions", "", m, p)))
    for m in ("dots-studio/dots-3-note-preview:free", "nvidia/nemotron-3-ultra-550b-a55b:free", "stepfun/step-3.7-flash:free"):  # Kilo gateway: no signup, 200 req/h per IP
        out.append(("kilo:" + m, lambda p, m=m: post("https://api.kilo.ai/api/gateway/chat/completions", "", m, p)))
    if os.environ.get("BENCH_NEW"):  # candidates under test (model_bench.py), not used by the writer yet
        for m in ("poolside/laguna-s-2.1:free", "nvidia/nemotron-3-super-120b-a12b:free", "inclusionai/ling-3.1-flash",
                  "thinkingmachines/inkling-small:free", "kilo-auto/free"):
            out.append(("kilo:" + m, lambda p, m=m: post("https://api.kilo.ai/api/gateway/chat/completions", "", m, p)))
        for m in ("Qwen3.6-27B", "Mistral-Small-3.2-24B-Instruct-2506", "gpt-oss-20b"):
            out.append(("ovh:" + m, lambda p, m=m: post("https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions", "", m, p)))
        if os.environ.get("LLM7_API_KEY"):
            for m in ("DeepSeek-V4.1-Flash", "deepseek-v4-pro", "GLM-5.3-Flash", "glm-5.3", "kimi-k2.6", "kimi-k3",
                      "mistral-large-3:675b", "llama-4-maverick", "gemma4:31b", "minimax-m3", "gemini-3.7-flash"):
                out.append(("llm7:" + m, lambda p, m=m: post("https://api.llm7.io/v1/chat/completions", os.environ["LLM7_API_KEY"], m, p)))
        if os.environ.get("COHERE_API_KEY"):
            out.append(("cohere:aya-expanse-32b", lambda p: post("https://api.cohere.ai/compatibility/v1/chat/completions", os.environ["COHERE_API_KEY"], "aya-expanse-32b", p)))
    return out


LOG = []


def note(line):
    print(line, flush=True)
    LOG.append(line)
    if os.environ.get("PUSH_EACH"):
        os.makedirs(os.path.join(ROOT, "news-test"), exist_ok=True)
        open(os.path.join(ROOT, "news-test", "live-log.txt"), "w", encoding="utf-8").write("\n".join(LOG[-200:]))


class QuotaError(ValueError):
    pass


QUOTA_RE = re.compile(r"quota|exceeded|per day|daily limit|RESOURCE_EXHAUSTED", re.I)
EXHAUSTED_FILE = os.path.join(tempfile.gettempdir(), f"exhausted-{time.strftime('%Y-%m-%d', time.gmtime())}.txt")


def exhausted():
    return set(open(EXHAUSTED_FILE, encoding="utf-8").read().split()) if os.path.exists(EXHAUSTED_FILE) else set()


def retry(call, prompt):
    """busy / rate-limited models get 3 more tries with growing pauses before we move to the next model;
    a used-up quota is not busy - it fails at once so the model is dropped for the rest of the day."""
    for wait in (0, 15, 30):
        time.sleep(wait)
        try:
            return call(prompt)
        except ValueError as e:
            if re.search(r"HTTP 429", str(e)) and QUOTA_RE.search(str(e)) and not re.search(r"PerMinute|per minute|PerSecond", str(e), re.I):
                raise QuotaError(str(e)) from e
            if not re.search(r"HTTP (429|500|502|503)", str(e)) or wait == 30:
                raise


# clichés the code fixes by itself (no rewrite needed); the rest of BANNED sends the text back to the model
FIXES = [(re.compile(r"^به گزارش [^،,]{0,40}(?:ام[‌ ]?اس[‌ ]?دیتا|msdata)[^،,]{0,10}[،,]\s*"), ""),
         (re.compile(r"به شمار می[‌ ]?رود|محسوب می[‌ ]?شود"), "است"), (re.compile(r"به ثبت رسید|رقم خورد"), "ثبت شد"),
         (re.compile(r"بالغ شد"), "رسید"), (re.compile(r"شایان ذکر است(?: که)?\s*"), ""), (re.compile(r"در این میان،?\s*"), "")]
BANNED = re.compile(r"به گزارش|بررسی (?:داده|آمار)\S* نشان|نشان[‌ ]?دهنده|حاکی از|بیانگر|؛ رقمی که|این در حالی است|به خود اختصاص|"
                    r"در مجموع|روی میز|رکورد تاریخی|سقف تاریخی|فصل ساخت|منتظر|انتظار می‌رود|احتمالاً|شاید|[?؟]\s*$")
NUM = re.compile(r"[0-9۰-۹]+(?:[٫.][0-9۰-۹]+)?")


def clean(t):
    for rx, rep in FIXES:
        t = rx.sub(rep, t)
    return t


def parse(raw, facts="", tables=None):
    m = re.search(r"\{.*\}", raw, re.S)
    d = json.loads(m.group(0)) if m else None
    if not isinstance(d, dict):
        raise ValueError("no JSON")
    for k in ("title", "slug", "subtitle", "lead", "text"):
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise ValueError(f"empty {k}")
        d[k] = clean(d[k].replace("\r", "").strip())
    w, p = words(d["text"]), paragraphs(d["text"])
    if not 130 <= w <= 210 or not 2 <= p <= 3:
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
    if d["lead"] in d["text"]:
        probs.append("لید عیناً در متن تکرار شده")
    if probs:
        raise ValueError("markers: " + " | ".join(probs))
    d["slug"] = SLUG_BAD.sub("-", d["slug"].strip())
    return d


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
    a = ap.parse_args()
    latest = json.load(open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8"))
    date, date_fa = latest["date"], latest["date_fa"]
    rows = json.load(open(os.path.join(ROOT, "data", date, "today.json"), encoding="utf-8"))["rows"]
    out_dir = os.path.join(ROOT, a.out)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{date}.json")
    doc = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"date_fa": date_fa, "date": date, "items": []}
    done = {i["symbol"] for i in doc["items"]}
    subs = {i["subtitle"] for i in doc["items"]}
    todo = [r for r in rows if r.get("symbol") not in done]
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
        groups.setdefault(name.split(":")[0] + ("/strong" if name in STRONG else "/backup" if name in BACKUP else ""), []).append((name, call))
    # importance tiers come from data/importance.json (tools/importance.py): 0 important (top half by value this year), 1 the rest.
    # A strong-model worker writes only important symbols, every other worker only the rest.
    table = importance.update(date, rows)
    tier = {s: e["tier"] for s, e in table.items()}
    value = {s: e["value"] for s, e in table.items()}
    seen_t = {s: e.get("seen_t", 0) for s, e in table.items()}
    todo.sort(key=lambda r: (tier.get(r["symbol"], 1), -value.get(r["symbol"], 0)))  # most important first
    del todo[a.limit or len(todo):]
    note(f"{len(todo)} symbols to write; {len(groups)} parallel workers: {', '.join(groups)}")
    lock = threading.Lock()
    tried = {r["symbol"]: set() for r in todo}
    stats = {"ok": 0}

    def write_one(r, models):
        sym = r["symbol"]
        hist_path = os.path.join(ROOT, "data", date, "symbols", f"{sym}.json")
        sd = json.load(open(hist_path, encoding="utf-8")) if os.path.exists(hist_path) else {}
        hist = sd.get("history", [])
        peers = [p for p in rows if p.get("goods_name") == r.get("goods_name")]
        with lock:
            recent = list(subs)[-5:]
        facts, tables = facts_kala.build(r, hist, peers, date_fa, date, sd.get("ytd_value"))
        prompt = ("حقیقت‌ها (فقط از این‌ها انتخاب کن؛ عددها را عیناً بنویس):\n" + facts +
                  "\n\nجدول‌های پیشنهادی (یکی را انتخاب کن):\n" + json.dumps(tables, ensure_ascii=False) +
                  f"\n\nزاویه‌ی خبرهای قبلی امروز را تکرار نکن. سوتیترهای قبلی: {' | '.join(recent)}")
        for name, call in models:
            if name in exhausted():
                continue
            t0 = time.time()
            try:
                raw = retry(call, prompt)
                for attempt in range(3):  # section 6 of style.md: every hit goes back to the model, up to 2 rewrites
                    try:
                        d = parse(raw, facts, tables)
                        break
                    except ValueError as e:
                        if attempt == 2 or not re.search(r"words|markers|table", str(e)):
                            raise
                        short = re.match(r"(\d+) words", str(e))
                        if short and int(short.group(1)) < 130:  # GLM and others stop short: say how many words are missing
                            e = f"{e} (متن تو {short.group(1)} کلمه است؛ دست‌کم {150 - int(short.group(1))} کلمه‌ی دیگر از حقیقت‌ها اضافه کن)"
                        raw = retry(call, prompt + "\n\nپیش‌نویس قبلی تو:\n" + raw + f"\n\nایرادها: {e}. همان خبر را با رفع همه‌ی این ایرادها "
                                    "(text بین ۱۴۰ تا ۲۰۰ کلمه در ۲ یا ۳ پاراگراف، فقط با عددهای حقیقت‌ها) بازنویسی کن و فقط JSON برگردان.")
                with lock:
                    if d["subtitle"] in subs:
                        raise ValueError("repeated subtitle")
                    doc["items"].append({"symbol": sym, "trade_date": date_fa, "commodity": r.get("goods_name", ""),
                                         "hall": r.get("talar", ""), "producer": r.get("producer_name", ""), **d, "model": name})
                    subs.add(d["subtitle"])
                    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    stats["ok"] += 1
                    note(f"OK {sym} <- {name} ({time.time() - t0:.0f}s)")
                    if os.environ.get("PUSH_EACH"):
                        subprocess.run('git add -A news-test queue data/importance.json data/scenarios.jsonl && git commit -qm "queue: progress" && git pull -q --rebase --autostash && git push -q',
                                       shell=True, cwd=ROOT, check=False, capture_output=True)
                return True
            except Exception as e:  # noqa: BLE001 - any failure means: try the next model of this provider
                if isinstance(e, QuotaError):
                    with lock:
                        open(EXHAUSTED_FILE, "a", encoding="utf-8").write(name + "\n")
                    note(f"  {name}: quota used up - skipped for the rest of the day")
                note(f"  {sym} {name}: {time.time() - t0:.0f}s {type(e).__name__}: {str(e)[:220]}")
                time.sleep(2)
        return False

    def worker(gname, models, lvl):
        fails = 0
        while fails < 3:  # a provider that fails 3 symbols in a row (quota / broken) stops
            with lock:
                live = [m for m in models if m[0] not in exhausted()]
                pick = [r for r in todo if gname not in tried[r["symbol"]] and tier.get(r["symbol"], 1) == lvl]
                if gname.endswith("/backup"):
                    strong_out = not any(t.is_alive() for g, t in zip(groups, threads) if g.endswith("/strong"))
                    late = [r for r in todo if gname not in tried[r["symbol"]] and tier.get(r["symbol"], 1) == 0
                            and (strong_out or time.time() - seen_t.get(r["symbol"], time.time()) > BACKUP_WAIT)]
                    pick = late + pick  # a waiting important symbol comes before the rest
                if not live or not pick:
                    return
                r = pick[0]
                todo.remove(r)
                tried[r["symbol"]].add(gname)
            if write_one(r, live):
                fails = 0
            else:
                fails += 1
                with lock:
                    if any(g not in tried[r["symbol"]] and (lvl_of(g) == tier.get(r["symbol"], 1) or g.endswith("/backup")) for g in groups):
                        todo.append(r)  # another provider may still write it
            time.sleep(4)  # stay under free-tier rate limits

    def lvl_of(g):
        return 0 if g.endswith("/strong") else 1

    threads = [threading.Thread(target=worker, args=(g, m, lvl_of(g))) for g, m in groups.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok = stats["ok"]
    print(f"written {ok}/{len(tried)}; total in file {len(doc['items'])}")


if __name__ == "__main__":
    main()
