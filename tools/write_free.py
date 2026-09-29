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
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import FORBIDDEN, SLUG_BAD, paragraphs, words  # noqa: E402
import factsheet  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
GH_URL = "https://models.github.ai/inference/chat/completions"
GH_AZURE_URL = "https://models.inference.ai.azure.com/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
OR_PREFER = ("qwen3.8", "gemma-4-31b", "nemotron-3-ultra", "deepseek", "qwen", "gemma", "llama")  # better Persian first

STYLE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.md"), encoding="utf-8").read()
RULES = ("تو خبرنگار «گروه بورس کالای ام‌اس‌دیتا» هستی و برای یک نماد بازار فیزیکی بورس کالا یک خبر فارسی می‌نویسی. "
         "این دستورالعمل را دقیق رعایت کن:\n\n" + STYLE +
         "\n\nفقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text (slug فارسی با خط تیره، بدون فاصله و علامت) و هیچ متن دیگری.")


def post(url, key, model, prompt, extra=None):
    body = {"model": model, "temperature": 0.7,
            "messages": [{"role": "system", "content": RULES}, {"role": "user", "content": prompt}]}
    req = urllib.request.Request(url, json.dumps(body).encode(), {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "msdata-news/1.0", **(extra or {})})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
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
    if os.environ.get("USE_GITHUB_MODELS") and os.environ.get("GITHUB_TOKEN"):  # GitHub Models (off: returned "OK" only): free OpenAI models inside Actions, no signup
        gh_hdr = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        for m in ("openai/gpt-4.1", "openai/gpt-4o"):
            out.append(("github:" + m, lambda p, m=m: post(GH_URL, os.environ["GITHUB_TOKEN"], m, p, gh_hdr)))
        for m in ("gpt-4.1", "gpt-4o"):  # older Azure-hosted endpoint of the same free GitHub Models
            out.append(("github-azure:" + m, lambda p, m=m: post(GH_AZURE_URL, os.environ["GITHUB_TOKEN"], m, p)))
    if os.environ.get("GEMINI_API_KEY"):
        # every Gemini model has its own daily free quota, so use all text models the key can see
        gem = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
        try:
            url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=" + os.environ["GEMINI_API_KEY"]
            with urllib.request.urlopen(url, timeout=60) as r:
                seen = [m["name"].split("/", 1)[1] for m in json.load(r).get("models", [])
                        if "generateContent" in m.get("supportedGenerationMethods", [])]
            seen = [m for m in seen if re.match(r"^gem(ini|ma)-", m) and not re.search(r"embed|image|tts|audio|live|vision|exp|preview", m)]
            gem += sorted((m for m in seen if m not in gem), key=lambda m: ("pro" not in m and "flash" not in m, "lite" in m, m))
        except Exception as e:  # noqa: BLE001 - listing is optional; the two known models still work
            note(f"gemini model list failed: {e}")
        for m in gem[:10]:
            out.append(("gemini:" + m, lambda p, m=m: post(GEMINI_URL, os.environ["GEMINI_API_KEY"], m, p)))
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=60) as r:
            free = [m["id"] for m in json.load(r)["data"] if m["id"].endswith(":free")]
        free.sort(key=lambda i: next((n for n, w in enumerate(OR_PREFER) if w in i), 99))
        for m in free[:6]:
            out.append(("openrouter:" + m, lambda p, m=m: post(OR_URL, key, m, p, {"X-Title": "msdata-news"})))
    for env, url, models in (("GROQ_API_KEY", GROQ_URL, ("llama-3.3-70b-versatile", "qwen/qwen3-32b")),
                             ("MISTRAL_API_KEY", MISTRAL_URL, ("mistral-large-latest", "mistral-medium-latest")),
                             ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1/chat/completions", ("qwen-3-235b-a22b-instruct-2507", "llama-3.3-70b")),
                             ("SAMBANOVA_API_KEY", "https://api.sambanova.ai/v1/chat/completions", ("DeepSeek-V3.1", "Meta-Llama-3.3-70B-Instruct"))):
        if os.environ.get(env):
            for m in models:
                out.append((env.split("_")[0].lower() + ":" + m, lambda p, m=m, u=url, e=env: post(u, os.environ[e], m, p)))
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
        sys.exit("no model key found (GITHUB_TOKEN / GEMINI_API_KEY / OPENROUTER_API_KEY / GROQ_API_KEY / MISTRAL_API_KEY)")
    note(f"{len(todo)} symbols to write; models: {', '.join(n for n, _ in provs)}")
    ok = 0
    for r in todo:
        sym = r["symbol"]
        hist_path = os.path.join(ROOT, "data", date, "symbols", f"{sym}.json")
        hist = json.load(open(hist_path, encoding="utf-8")).get("history", []) if os.path.exists(hist_path) else []
        peers = [p for p in rows if p.get("goods_name") == r.get("goods_name")]
        prompt = ("فکت‌شیت (فقط همین عددها را به کار ببر):\n" + factsheet.build(r, hist, peers, date_fa) +
                  f"\n\nزاویه‌ی خبرهای قبلی امروز را تکرار نکن. سوتیترهای قبلی: {' | '.join(list(subs)[-5:])}")
        for name, call in provs:
            if name in exhausted():
                continue
            t0 = time.time()
            note(f"  {sym} {name}: start")
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
                if d["subtitle"] in subs:
                    raise ValueError("repeated subtitle")
            except Exception as e:  # noqa: BLE001 - any failure means: try the next model
                if isinstance(e, QuotaError):
                    open(EXHAUSTED_FILE, "a", encoding="utf-8").write(name + "\n")
                    note(f"  {name}: quota used up - skipped for the rest of the day")
                note(f"  {sym} {name}: {time.time() - t0:.0f}s {type(e).__name__}: {str(e)[:220]}")
                time.sleep(2)
                continue
            doc["items"].append({"symbol": sym, "trade_date": date_fa, "commodity": r.get("goods_name", ""),
                                 "hall": r.get("talar", ""), "producer": r.get("producer_name", ""), **d})
            subs.add(d["subtitle"])
            json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            ok += 1
            note(f"OK {sym} <- {name} ({time.time() - t0:.0f}s)")
            break
        if os.environ.get("PUSH_EACH"):  # publish progress/errors after every symbol
            subprocess.run('git add -A news-test queue && git commit -qm "queue: progress" && git pull -q --rebase && git push -q',
                           shell=True, cwd=ROOT, check=False)
        time.sleep(4)  # stay under free-tier rate limits
    print(f"written {ok}/{len(todo)}; total in file {len(doc['items'])}")


if __name__ == "__main__":
    main()
