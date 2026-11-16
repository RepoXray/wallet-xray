"""Wallet Xray copy-replay engine.

Reads a wallet's recent swaps and the price bars of each token, replays every trade the way a
copier would get it (a few seconds late, after a 1% bot fee per side), and scores the result.
The same logic runs in the browser on walletxray.xyz and in the Telegram bot.

Data comes from a read-only GMGN OpenAPI proxy you run yourself:
  RELAY_URL    base URL of the proxy, which forwards
               GET /gmgn?ep=activity&chain=bsc&wallet=0x…[&cursor=…]
               GET /gmgn?ep=kline&chain=bsc&token=0x…&res=30s|1m|5m&from=<ms>&to=<ms>
  RELAY_TOKEN  shared secret sent as the X-Relay-Token header
No keys live in this file. The engine never signs or sends transactions.
"""
import json, math, os, random, re, time, urllib.parse, urllib.request, urllib.error

FEE, MIN_TOKENS, MAX_TOKENS, PAGES = 0.01, 8, 25, 4
RESS = {"30s": 30, "1m": 60, "5m": 300}
CHAIN_NAME = {"bsc": "BNB Chain", "base": "Base", "sol": "Solana"}
EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOL = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
DXL = {"genius": "Copyable, consistent, not luck. Stays good when followed.",
       "sharp": "Real skill, but one or two checks are borderline.",
       "average": "No measurable edge for a copier.",
       "below": "Copying this wallet has lost money.",
       "lucky": "One lucky trade carries the record.",
       "predator": "Sells into its followers. Good for it, bad for you."}
NM = {"genius": "Mastermind", "sharp": "Sharp", "average": "Average", "below": "Below average",
      "lucky": "Lucky", "predator": "Predator"}


class RateLimited(Exception):
    pass


def _relay():
    url = os.environ.get("RELAY_URL", "").rstrip("/")
    if not url:
        raise RuntimeError("set RELAY_URL to your read-only GMGN proxy")
    return url, os.environ.get("RELAY_TOKEN", "")


def api(params, tries=0):
    url, tok = _relay()
    req = urllib.request.Request(url + "/gmgn?" + urllib.parse.urlencode(params), headers={"X-Relay-Token": tok})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read())
        except Exception:
            d = {"error": f"HTTP {e.code}"}
        if e.code == 429:
            if tries < 3:
                reset = d.get("reset_at")
                time.sleep(max(2, min(20, (reset - time.time() + 0.5) if reset else 4)))
                return api(params, tries + 1)
            raise RateLimited("rate limited")
        raise RuntimeError(d.get("error") or f"HTTP {e.code}")


def bars(d):
    L = d if isinstance(d, list) else (d.get("list") or d.get("klines") or [])
    return sorted(({"t": float(b["time"]), "o": float(b["open"]), "c": float(b["close"])} for b in L), key=lambda b: b["t"])


def bar_at(B, t_ms, res_ms):
    lo, hi, best = 0, len(B) - 1, -1
    while lo <= hi:
        m = (lo + hi) >> 1
        if B[m]["t"] <= t_ms:
            best, lo = m, m + 1
        else:
            hi = m - 1
    return best if best >= 0 and t_ms < B[best]["t"] + res_ms else -1


def clamp(x, a, b):
    return max(a, min(b, x))


def js_round(x):
    return math.floor(x + 0.5)


def roi_score(p, dep):
    return 50 if dep <= 0 else js_round(clamp(50 + 250 * p / dep, 0, 100))


def pct_s(p, dep):
    v = 100 * p / dep
    return ("+" if v >= 0 else "−") + f"{abs(v):.1f}%"


def score(r):
    dep = max(1, r["deployed"])
    spd, con, lck = roi_score(r["copy10"], dep), roi_score(r["minus_best"], dep), roi_score(r["boot_p5"], dep)
    mem = roi_score(r["fwd_copy10"], dep) if r["tokens_fwd"] >= 5 else None
    integ = js_round(clamp(100 - 100 * max(r["sold_into"], 1.2 * r["flip60"]), 0, 100))
    s, W = [spd, con, lck, mem, integ], [.4, .15, .15, .1, .2]
    sv = sum(v * w for v, w in zip(s, W) if v is not None)
    sw = sum(w for v, w in zip(s, W) if v is not None)
    him = js_round(100 + 15 * clamp((r["actual"] / dep) / 0.12, -3, 3.4))
    you = js_round(100 + 1.1 * (sv / sw - 50))
    if spd < 50:
        you = min(you, 100 - (50 - spd))
    star, tone = False, ""
    if him >= 115 and you < 90 and integ < 40:
        prof, tone = "predator", "cold"
    elif him >= 115 and (con < 30 or lck < 30):
        prof, tone, star = "lucky", "cold", True
    elif you >= 140:
        prof, tone = "genius", "hot"
    elif you >= 115:
        prof, tone = "sharp", "hot"
    elif you >= 85:
        prof = "average"
    else:
        prof, tone = "below", "cold"
    raw = [f"fast copy {pct_s(r['copy3'], dep)} · normal copy {pct_s(r['copy10'], dep)}",
           f"{pct_s(r['minus_best'], dep)} without best token",
           f"worst 5%: {pct_s(r['boot_p5'], dep)}",
           f"{pct_s(r['fwd_copy10'], dep)} in the latest third" if mem is not None else "not enough recent trades",
           f"{js_round(100 * r['sold_into'])}% sold into rising bars · {js_round(100 * r['flip60'])}% flipped <60s"]
    return {"s": s, "iq": him, "you": you, "profile": prof, "tone": tone, "star": star, "raw": raw, "en": NM[prof], "dx": DXL[prof]}


def test(addr, chain="bsc", progress=lambda m: None):
    """Returns a wallet dict like the site's, or {"insufficient": True, ...}."""
    if chain not in CHAIN_NAME:
        raise ValueError("unsupported chain")
    if not (SOL if chain == "sol" else EVM).match(addr):
        raise ValueError("bad address")
    acts, cursor, oldest = [], None, time.time() - 90 * 86400
    for pg in range(PAGES):
        progress(f"Reading trades · page {pg + 1}")
        q = {"ep": "activity", "chain": chain, "wallet": addr}
        if cursor:
            q["cursor"] = cursor
        d = api(q)
        A = d.get("activities") or []
        acts += A
        if not d.get("next") or len(A) < 50 or (A and float(A[-1]["timestamp"]) < oldest):
            break
        cursor = d["next"]
    toks = {}
    for a in acts:
        side = a.get("event_type")
        if side not in ("buy", "sell"):
            continue
        t = (a.get("token") or {}).get("address")
        if not t:
            continue
        x = {"ts": float(a["timestamp"]), "side": side, "usd": float(a.get("cost_usd") or 0),
             "amt": float(a.get("token_amount") or 0), "px": float(a.get("price_usd") or 0)}
        if not (x["usd"] > 0 and x["amt"] > 0):
            continue
        toks.setdefault(t, []).append(x)
    lst = []
    for t, tr in toks.items():
        tr.sort(key=lambda x: x["ts"])
        dep = sum(x["usd"] for x in tr if x["side"] == "buy")
        if dep > 0:
            lst.append({"tok": t, "tr": tr, "dep": dep})
    lst.sort(key=lambda p: -p["dep"])
    trades, n_tok = len(acts), len(lst)
    if n_tok < MIN_TOKENS:
        return {"insufficient": True, "tokens": n_tok, "trades": trades}
    lst = lst[:MAX_TOKENS]
    for p in lst:
        span = p["tr"][-1]["ts"] - p["tr"][0]["ts"]
        p["res"] = "30s" if span <= 4 * 3600 else "1m" if span <= 24 * 3600 else "5m"
    for i, p in enumerate(lst, 1):
        res_s, f, l = RESS[p["res"]], p["tr"][0]["ts"], p["tr"][-1]["ts"]
        try:
            p["B"] = bars(api({"ep": "kline", "chain": chain, "token": p["tok"], "res": p["res"],
                               "from": int((f - 2 * res_s) * 1000), "to": int((l + 3 * res_s) * 1000)}))
        except RateLimited:
            raise
        except Exception:
            p["B"] = []
        progress(f"Pricing tokens · {i} of {len(lst)}")
    sell_usd = sold_into = flip = 0.0
    for p in lst:
        B, res_s, f = p["B"], RESS[p["res"]], p["tr"][0]["ts"]
        held = c3 = c10 = cash = cash3 = cash10 = 0.0
        first_buy = None
        for x in p["tr"]:
            b3 = bar_at(B, (x["ts"] + 3) * 1000, res_s * 1000)
            b10 = bar_at(B, (x["ts"] + 3 + res_s) * 1000, res_s * 1000)
            p3 = B[b3]["c"] if b3 >= 0 else x["px"]
            p10 = B[b10]["c"] if b10 >= 0 else (B[b3]["c"] if b3 >= 0 else x["px"])
            if not p3 > 0:
                p3 = x["px"]
            if not p10 > 0:
                p10 = x["px"]
            if x["side"] == "buy":
                if first_buy is None:
                    first_buy = x["ts"]
                cash -= x["usd"]; cash3 -= x["usd"]; cash10 -= x["usd"]
                if p3 > 0:
                    c3 += x["usd"] * (1 - FEE) / p3
                if p10 > 0:
                    c10 += x["usd"] * (1 - FEE) / p10
                held += x["amt"]
            else:
                frac = min(1, x["amt"] / held) if held > 0 else 1
                cash += x["usd"]
                cash3 += c3 * frac * p3 * (1 - FEE); cash10 += c10 * frac * p10 * (1 - FEE)
                c3 -= c3 * frac; c10 -= c10 * frac; held = max(0, held - x["amt"])
                sell_usd += x["usd"]
                bs = bar_at(B, x["ts"] * 1000, res_s * 1000)
                if bs > 0 and B[bs]["c"] > B[bs]["o"] and B[bs - 1]["c"] >= B[bs - 1]["o"]:
                    sold_into += x["usd"]
                if first_buy is not None and x["ts"] - first_buy < 60:
                    flip += x["usd"]
        last_px = B[-1]["c"] if B else p["tr"][-1]["px"]   # still-held tokens marked at last known price
        if last_px > 0:
            cash += held * last_px; cash3 += c3 * last_px * (1 - FEE); cash10 += c10 * last_px * (1 - FEE)
        p.update(actual=cash, c3=cash3, c10=cash10, first=f)
    c10s = [p["c10"] for p in lst]
    best = max(c10s)
    boots = sorted(sum(random.choice(c10s) for _ in c10s) for _ in range(2000))
    tmin, tmax = min(p["first"] for p in lst), max(p["first"] for p in lst)
    fwd = [p for p in lst if p["first"] >= tmin + (tmax - tmin) * 2 / 3]
    r = {"deployed": sum(p["dep"] for p in lst), "actual": sum(p["actual"] for p in lst),
         "copy3": sum(p["c3"] for p in lst), "copy10": sum(c10s), "minus_best": sum(c10s) - best,
         "boot_p5": boots[100], "fwd_copy10": sum(p["c10"] for p in fwd), "tokens_fwd": len(fwd),
         "sold_into": sold_into / sell_usd if sell_usd else 0, "flip60": flip / sell_usd if sell_usd else 0}
    sc = score(r)
    days = max(1, js_round((time.time() - tmin) / 86400))
    sc.update(id=addr if chain == "sol" else addr.lower(), addr=addr[:6] + "…" + addr[-4:], chain=CHAIN_NAME[chain],
              example=False, tested=True, trades=trades, tokens=len(lst),
              window=f"last {days} days · {len(lst)} largest tokens", dx=[sc["dx"]],
              money={"dep": round(r["deployed"]), "pnl": round(r["actual"]), "copy": round(r["copy10"]), "fast": round(r["copy3"]),
                     "wins": sum(1 for p in lst if p["actual"] > 0), "n": len(lst), "best": round(max(p["actual"] for p in lst))},
              at=int(time.time()))
    return sc


if __name__ == "__main__":
    import sys
    a = sys.argv[1]
    ch = sys.argv[2] if len(sys.argv) > 2 else ("sol" if not a.startswith("0x") else "bsc")
    t0 = time.time()
    w = test(a, ch, lambda m: print(" ", m, flush=True))
    print(json.dumps({k: w.get(k) for k in ("iq", "you", "profile", "s", "raw", "trades", "tokens", "window", "insufficient")}, ensure_ascii=False))
    print(f"{time.time() - t0:.0f}s")
