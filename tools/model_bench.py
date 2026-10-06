"""model_bench.py - one-off (all, parallel, rerun 7) (per provider, logged) Persian writing test: every configured model writes the same news, results go to news-test/bench.json."""
import concurrent.futures
import json
import os
import time

import factsheet
import write_free as w


def main():
    latest = json.load(open(os.path.join(w.ROOT, "data", "latest.json"), encoding="utf-8"))
    date, date_fa = latest["date"], latest["date_fa"]
    rows = json.load(open(os.path.join(w.ROOT, "data", date, "today.json"), encoding="utf-8"))["rows"]
    r = max(rows, key=lambda x: len([p for p in rows if p.get("goods_name") == x.get("goods_name")]))
    hp = os.path.join(w.ROOT, "data", date, "symbols", f"{r['symbol']}.json")
    hist = json.load(open(hp, encoding="utf-8")).get("history", []) if os.path.exists(hp) else []
    peers = [p for p in rows if p.get("goods_name") == r.get("goods_name")]
    prompt = "فکت‌شیت (فقط همین عددها را به کار ببر):\n" + factsheet.build(r, hist, peers, date_fa)
    out = {"symbol": r["symbol"], "prompt": prompt, "results": []}
    only = os.environ.get("BENCH_ONLY", "")
    provs = [x for x in w.providers() if x[0].startswith(tuple(only.split(",")))]

    def run(name, call):
        t0, res = time.time(), {"model": name}
        try:
            raw = call(prompt)
            res["raw"] = raw
            try:
                res["parsed"] = w.parse(raw)
            except Exception as e:  # noqa: BLE001
                res["parse_error"] = str(e)[:200]
        except Exception as e:  # noqa: BLE001
            res["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        res["seconds"] = round(time.time() - t0)
        print(name, "ok" if "parsed" in res else res.get("parse_error") or res.get("error"), flush=True)
        return res

    # every model at once: each has its own quota, so the test takes as long as the slowest model
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(provs) or 1) as ex:
        out["results"] = list(ex.map(lambda x: run(*x), provs))
    os.makedirs(os.path.join(w.ROOT, "news-test"), exist_ok=True)
    json.dump(out, open(os.path.join(w.ROOT, "news-test", "bench.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
