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
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import FORBIDDEN, SLUG_BAD, paragraphs, words  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
GH_URL = "https://models.github.ai/inference/chat/completions"
GH_AZURE_URL = "https://models.inference.ai.azure.com/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
OR_PREFER = ("deepseek", "gemini", "qwen", "llama", "mistral", "gemma")  # better Persian first

RULES = """تو خبرنگار «گروه بورس کالای ام‌اس‌دیتا» هستی. برای یک نماد بازار فیزیکی بورس کالا یک خبر فارسی بنویس.
واحدها: weighted_price و weighted_base_price ریال بر کیلوگرم؛ مقدارها به واحد unit (معمولاً تن)؛
trade_value و total_value و demand_value و supply_value هزار ریال (برای میلیارد تومان تقسیم بر 10,000,000)؛
price_change_pct درصد تغییر نسبت به معامله‌ی قبلی همین نماد.
قواعد سخت:
- هر عدد فقط از داده‌ی داده‌شده؛ هیچ علت، خبر بیرونی یا پیش‌بینی نساز. توصیه‌ی خرید و فروش ممنوع. لحن خبری و بی‌طرف.
- text دقیقاً ۱۶۰ تا ۱۹۰ کلمه در ۲ یا ۳ پاراگراف که با یک خط خالی جدا شده‌اند، و با «به گزارش گروه بورس کالای ام‌اس‌دیتا،» شروع شود.
- زاویه را از خبری‌ترین فکت همین نماد انتخاب کن (رکورد نرخ، تضاد عرضه و تقاضا، بی‌خریدار ماندن، بازگشت پس از غیبت، روند history).
- title با نام کالا و تولیدکننده؛ subtitle یک جمله‌ی مکمل و مخصوص همین نماد؛ lead یک جمله‌ی خلاصه.
- slug فارسی با خط تیره، بدون فاصله و علامت.
- عددها با ارقام فارسی و خوانا (مثل «۱ هزار و ۵۷۱ میلیارد تومان»). لینک و HTML ممنوع.
فقط یک JSON برگردان با کلیدهای title, slug, subtitle, lead, text و هیچ متن دیگری."""


def post(url, key, model, prompt, extra=None):
    body = {"model": model, "temperature": 0.7,
            "messages": [{"role": "system", "content": RULES}, {"role": "user", "content": prompt}]}
    req = urllib.request.Request(url, json.dumps(body).encode(), {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "msdata-news/1.0", **(extra or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
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
        for m in ("gemini-3.8-flash", "gemini-3.5-flash-lite"):
            out.append(("gemini:" + m, lambda p, m=m: post(GEMINI_URL, os.environ["GEMINI_API_KEY"], m, p)))
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=60) as r:
            free = [m["id"] for m in json.load(r)["data"] if m["id"].endswith(":free")]
        free.sort(key=lambda i: next((n for n, w in enumerate(OR_PREFER) if w in i), 99))
        for m in free[:6]:
            out.append(("openrouter:" + m, lambda p, m=m: post(OR_URL, key, m, p, {"X-Title": "msdata-news"})))
    for env, url, models in (("GROQ_API_KEY", GROQ_URL, ("llama-3.3-70b-versatile",)),
                             ("MISTRAL_API_KEY", MISTRAL_URL, ("mistral-large-latest",))):
        if os.environ.get(env):
            for m in models:
                out.append((env.split("_")[0].lower() + ":" + m, lambda p, m=m, u=url, e=env: post(u, os.environ[e], m, p)))
    return out


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
    print(f"{len(todo)} symbols to write, {len(provs)} models")
    ok = 0
    for r in todo:
        sym = r["symbol"]
        hist_path = os.path.join(ROOT, "data", date, "symbols", f"{sym}.json")
        hist = json.load(open(hist_path, encoding="utf-8")).get("history", [])[-12:] if os.path.exists(hist_path) else []
        prompt = (f"تاریخ معامله: {date_fa}\nداده‌ی امروز:\n{json.dumps(r, ensure_ascii=False)}\n"
                  f"معامله‌های قبلی همین نماد (قدیمی به جدید):\n{json.dumps(hist, ensure_ascii=False)}")
        for name, call in provs:
            try:
                d = parse(call(prompt))
                if d["subtitle"] in subs:
                    raise ValueError("repeated subtitle")
            except Exception as e:  # noqa: BLE001 - any failure means: try the next model
                print(f"  {sym} {name}: {str(e)[:220]}")
                time.sleep(2)
                continue
            doc["items"].append({"symbol": sym, "trade_date": date_fa, "commodity": r.get("goods_name", ""),
                                 "hall": r.get("talar", ""), "producer": r.get("producer_name", ""), **d})
            subs.add(d["subtitle"])
            json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            ok += 1
            print(f"OK {sym} <- {name}")
            break
        time.sleep(4)  # stay under free-tier rate limits
    print(f"written {ok}/{len(todo)}; total in file {len(doc['items'])}")


if __name__ == "__main__":
    main()
