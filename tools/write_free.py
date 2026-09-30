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
import factsheet  # noqa: E402
import importance  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
GH_URL = "https://models.github.ai/inference/chat/completions"
GH_AZURE_URL = "https://models.inference.ai.azure.com/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
OR_PREFER = ("nemotron-3-ultra", "gemma-4-31b", "nemotron-3-ultra", "deepseek", "qwen", "gemma", "llama")  # better Persian first

STYLE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.md"), encoding="utf-8").read()
RULES = ("تو خبرنگار «گروه بورس کالای ام‌اس‌دیتا» هستی و برای یک نماد بازار فیزیکی بورس کالا یک خبر فارسی می‌نویسی. "
         "این دستورالعمل را دقیق رعایت کن:\n\n" + STYLE +
         "\n\nفقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text (slug فارسی با خط تیره، بدون فاصله و علامت) و هیچ متن دیگری.")


def post(url, key, model, prompt, extra=None):
    body = {"model": model, "temperature": 0.7,
            "messages": [{"role": "system", "content": RULES}, {"role": "user", "content": prompt}]}
    if "cloudflare.com" in url:
        body["max_tokens"] = 2048  # Cloudflare cuts answers at a 256-token default; a cap elsewhere starves reasoning models
    auth = {"Authorization": f"Bearer {key}"} if key else {}  # OVH AI Endpoints works anonymously (2 req/min per model)
    req = urllib.request.Request(url, json.dumps(body).encode(), {
        **auth, "Content-Type": "application/json", "User-Agent": "msdata-news/1.0", **(extra or {})})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise ValueError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from None
    try:
        return json.loads(raw)["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        raise ValueError(f"bad response: {raw[:200]!r}") from None


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
        for m in gem[:10]:
            out.append(("gemini:" + m, lambda p, m=m: post(GEMINI_URL, os.environ["GEMINI_API_KEY"], m, p)))
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=60) as r:
            free = [m["id"] for m in json.load(r)["data"] if m["id"].endswith(":free") and "qwen3.8" not in m["id"]]  # qwen3.8 wrote nonsense Persian
        free.sort(key=lambda i: next((n for n, w in enumerate(OR_PREFER) if w in i), 99))
        for m in free[:6]:
            out.append(("openrouter:" + m, lambda p, m=m: post(OR_URL, key, m, p, {"X-Title": "msdata-news"})))
    for env, url, models in (("GROQ_API_KEY", GROQ_URL, ("llama-3.3-70b-versatile", "qwen/qwen3-32b")),
                             ("MISTRAL_API_KEY", MISTRAL_URL, ("mistral-large-latest", "mistral-medium-latest")),
                             ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1/chat/completions", ("qwen-3-235b-a22b-instruct-2507", "llama-3.3-70b")),
                             ("ZAI_API_KEY", "https://api.z.ai/api/paas/v4/chat/completions", ("glm-4.5-flash",)),
                             ("HF_TOKEN", "https://router.huggingface.co/v1/chat/completions", ("deepseek-ai/DeepSeek-V3.1", "Qwen/Qwen3-235B-A22B-Instruct-2507")),
                             ("CF_API_TOKEN", f"https://api.cloudflare.com/client/v4/accounts/{os.environ.get('CF_ACCOUNT_ID', '')}/ai/v1/chat/completions",
                              ("@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/qwen/qwen2.5-coder-32b-instruct")),
                             ("NVIDIA_API_KEY","https://integrate.api.nvidia.com/v1/chat/completions", ("deepseek-ai/deepseek-v3.1", "qwen/qwen3-235b-a22b", "meta/llama-3.3-70b-instruct")),
                             ("LLM7_API_KEY", "https://api.llm7.io/v1/chat/completions", ("DeepSeek-V4-Flash-0731", "minimax-m2.7")),
                             ("COHERE_API_KEY", "https://api.cohere.ai/compatibility/v1/chat/completions", ("command-a-03-2025",))):
        if os.environ.get(env):
            for m in models:
                out.append((env.split("_")[0].lower() + ":" + m, lambda p, m=m, u=url, e=env: post(u, os.environ[e], m, p)))
    for m in ("Qwen3.5-397B-A17B", "gpt-oss-120b", "Meta-Llama-3_3-70B-Instruct"):  # OVH: no signup, no key
        out.append(("ovh:" + m, lambda p, m=m: post("https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions", "", m, p)))
    for m in ("nvidia/nemotron-3-ultra-550b-a55b:free", "stepfun/step-3.7-flash:free"):  # Kilo gateway: no signup, 200 req/h per IP
        out.append(("kilo:" + m, lambda p, m=m: post("https://api.kilo.ai/api/gateway/chat/completions", "", m, p)))
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
            if re.search(r"HTTP 429", str(e)) and QUOTA_RE.search(str(e)):
                raise QuotaError(str(e)) from e
            if not re.search(r"HTTP (429|500|502|503)", str(e)) or wait == 30:
                raise


def parse(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    d = json.loads(m.group(0)) if m else None
    if not isinstance(d, dict):
        raise ValueError("no JSON")
    for k in ("title", "slug", "subtitle", "lead", "text"):
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise ValueError(f"empty {k}")
    d["text"] = d["text"].replace("\r", "").strip()
    w, p = words(d["text"]), paragraphs(d["text"])
    if not 150 <= w <= 200 or not 2 <= p <= 3:
        raise ValueError(f"{w} words / {p} paragraphs")
    if not d["text"].startswith("به گزارش گروه بورس کالای ام‌اس‌دیتا"):
        raise ValueError("bad opening")
    if any(FORBIDDEN.search(d[k]) for k in ("title", "subtitle", "lead", "text")):
        raise ValueError("forbidden content")
    d["slug"] = SLUG_BAD.sub("-", d["slug"].strip())
    return d


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
    todo = [r for r in rows if r.get("symbol") not in done][: a.limit or None]
    provs = providers()
    if not provs:
        sys.exit("no model key found (GEMINI_API_KEY / OPENROUTER_API_KEY / GROQ_API_KEY / ...)")
    # one worker per provider (each has its own free quota) so they write in parallel from one shared list;
    # the most valuable trades go to the strongest providers, the weaker ones start from the other end
    groups = {}
    for name, call in provs:
        groups.setdefault(name.split(":")[0], []).append((name, call))
    # importance tiers come from data/importance.json (tools/importance.py): 0 important, 1 medium, 2 low.
    # Provider level: 0 strong, 1 medium, 2 weak; a worker writes only symbols of its level or less important.
    tier = importance.update(date, rows)
    value = {r["symbol"]: r.get("trade_value") or 0 for r in rows}
    todo.sort(key=lambda r: (tier.get(r["symbol"], 1), -value[r["symbol"]]))
    note(f"{len(todo)} symbols to write; {len(groups)} parallel workers: {', '.join(groups)}")
    lock = threading.Lock()
    tried = {r["symbol"]: set() for r in todo}
    stats = {"ok": 0}

    def write_one(r, models):
        sym = r["symbol"]
        hist_path = os.path.join(ROOT, "data", date, "symbols", f"{sym}.json")
        hist = json.load(open(hist_path, encoding="utf-8")).get("history", []) if os.path.exists(hist_path) else []
        peers = [p for p in rows if p.get("goods_name") == r.get("goods_name")]
        with lock:
            recent = list(subs)[-5:]
        prompt = ("فکت‌شیت (فقط همین عددها را به کار ببر):\n" + factsheet.build(r, hist, peers, date_fa) +
                  f"\n\nزاویه‌ی خبرهای قبلی امروز را تکرار نکن. سوتیترهای قبلی: {' | '.join(recent)}")
        for name, call in models:
            if name in exhausted():
                continue
            t0 = time.time()
            try:
                raw = retry(call, prompt)
                try:
                    d = parse(raw)
                except ValueError as e:
                    if "words" not in str(e):
                        raise
                    fix = (prompt + "\n\nپیش‌نویس قبلی تو:\n" + raw + f"\n\nایراد: متن text {e}. همان خبر را با text بین ۱۶۰ تا ۱۹۰ کلمه "
                           "در ۲ یا ۳ پاراگراف بازنویسی کن (فقط با عددهای فکت‌شیت) و فقط JSON برگردان.")
                    d = parse(retry(call, fix))
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
                        subprocess.run('git add -A news-test queue data/importance.json && git commit -qm "queue: progress" && git pull -q --rebase && git push -q',
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
                pick = [r for r in todo if gname not in tried[r["symbol"]] and tier.get(r["symbol"], 2) >= lvl]
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
                    if any(g not in tried[r["symbol"]] and level.get(g, 2) <= tier.get(r["symbol"], 2) for g in groups):
                        todo.append(r)  # another provider may still write it
            time.sleep(4)  # stay under free-tier rate limits

    level = {"gemini": 0, "hf": 0, "openrouter": 0, "sambanova": 0, "zai": 1, "groq": 1, "cf": 1, "llm7": 1, "siliconflow": 1, "ovh": 1, "kilo": 0, "vercel": 0}  # the rest (cohere) = 2
    threads = [threading.Thread(target=worker, args=(g, m, level.get(g, 2))) for g, m in groups.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok = stats["ok"]
    print(f"written {ok}/{len(tried)}; total in file {len(doc['items'])}")


if __name__ == "__main__":
    main()
