#!/usr/bin/env python3
"""Wallet Xray Telegram bot. Standard library + Pillow (for the share card).

Run:   WIQ_BOT_TOKEN=123:abc RELAY_URL=https://your-proxy RELAY_TOKEN=… python3 bot.py
Live tests use ../engine/live.py (the same engine as the website).
Example wallets (invented addresses) come from examples.json.

Users can:
  paste any address                -> live test: both IQs, profile, five checks + card button
  /iq 0x… [base]                   same (BNB Chain by default; Solana addresses are detected)
  /duel 0x… 0x…                    two wallets side by side
  /watch 0x… · /unwatch 0x…        re-tested every 12h; ping when it turns Predator or Your IQ drops
  /masterminds · /predators        wallets tested in the last 7 days
Measures only: never says buy or sell, never touches funds.
"""
import html, json, os, queue, re, sqlite3, threading, time, traceback, urllib.error, urllib.request, uuid
from datetime import datetime, timezone

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
import live  # noqa: E402

D = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(D, "examples.json")
FREE_PER_DAY = 15
FRESH = 6 * 3600            # reuse a result this young instead of re-testing (free, instant)
WATCH_EVERY = 12 * 3600
MAX_WATCH = 5
EVM_ANY = re.compile(r"0x[0-9a-fA-F]{40}|0x[0-9A-Fa-f]{4}…[0-9A-Fa-f]{4}")
TESTS = [("SPD", "Speed"), ("CON", "Consistency"), ("LCK", "Luck"), ("MEM", "Memory"), ("INT", "Integrity")]
SITE = "walletxray.xyz"


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- examples
class Examples:
    def __init__(self, path=DATA):
        doc = json.load(open(path))
        self.by_id = {w["id"].lower(): w for w in doc["wallets"]}
        self.by_short = {w["addr"].lower(): w for w in doc["wallets"]}

    def get(self, a):
        return self.by_id.get(a.lower()) or self.by_short.get(a.lower())


def parse(text):
    """-> list of (address, chain) found in the message."""
    words = (text or "").replace(",", " ").split()
    chain_hint = "base" if any(w.lower() == "base" for w in words) else "bsc"
    out = []
    for w in words:
        for a in EVM_ANY.findall(w):
            out.append((a, chain_hint))
        if not w.startswith(("0x", "/")) and live.SOL.match(w) and any(c.isdigit() for c in w):
            out.append((w, "sol"))
    return out


def scaled(v):
    return "??" if v is None else str(round(1 + v * 18 / 100))


def report(w, left=None):
    dx = w["dx"][0] if isinstance(w["dx"], list) else w["dx"]
    if w.get("iq") is None:
        head = f"<b>WALLET XRAY · {w['addr']}</b>\nWallet IQ  <b>??</b>\nYour IQ    <b>??</b>\nProfile    <b>{w['en']}</b>"
    else:
        star = "*" if w.get("star") else ""
        head = (f"<b>WALLET XRAY · {w['addr']}</b>  {w.get('chain', '')}\n"
                f"Wallet IQ  <b>{w['iq']}{star}</b>  (its own trades)\n"
                f"Your IQ    <b>{w['you']}</b>  (if you copy it)\n"
                f"Profile    <b>{w['en']}</b>")
    checks = "\n".join(f"<code>{k}</code> {scaled(v):>2}  {html.escape(raw)}" for (k, _), v, raw in zip(TESTS, w["s"], w.get("raw") or ["—"] * 5))
    if w.get("example"):
        foot = "Example wallet, invented numbers."
    else:
        foot = f"Tested on {w.get('trades', '?')} trades, {w.get('window', '')}."
    tail = f"\n{left} of {FREE_PER_DAY} free tests left today." if left is not None else ""
    return f"{head}\n\n{checks}\n\n<i>{html.escape(dx)}</i>\n\n{foot} Not financial advice.{tail}"


PROFILE_ICON = {"genius": "🧠", "sharp": "🟢", "average": "⚪", "below": "🔻", "lucky": "🎲", "predator": "🦈", "ghost": "👻"}
CHECK_NAME = ["Speed", "Consistency", "Luck", "Memory", "Integrity"]


def dot(v):
    v = scaled(v)
    return "⚪" if v == "??" else "🟢" if int(v) >= 12 else "🟡" if int(v) >= 7 else "🔴"


def usd(x):
    sign = "+" if x >= 0 else "−"
    x = abs(x)
    v = f"{x / 1e6:.2f}M" if x >= 1e6 else f"{x / 1e3:.1f}k" if x >= 1e4 else f"{x:,.0f}"
    return f"{sign}${v}"


def pct(x, dep):
    return f"{'+' if x >= 0 else '−'}{abs(100 * x / max(1, dep)):.0f}%"


def money_lines(w):
    m = w.get("money")
    if not m:
        return ""
    dep = m["dep"]
    return (f"<b>Money</b> · {m['n']} largest tokens, ${dep:,} put in\n"
            f"💰 Wallet made  <b>{usd(m['pnl'])}</b> ({pct(m['pnl'], dep)})\n"
            f"👤 Copying it  <b>{usd(m['copy'])}</b> ({pct(m['copy'], dep)}) · fast copy {usd(m['fast'])}\n"
            f"🎯 Win rate  {m['wins']} of {m['n']} tokens · best token {usd(m['best'])}\n\n")


def caption(w, left=None):
    """Structured result for a photo caption (Telegram limit 1024 chars): verdict, money, scores, checks."""
    chain = "EXAMPLE" if w.get("example") else w.get("chain", "")
    dx = w["dx"][0] if isinstance(w["dx"], list) else w["dx"]
    m = w.get("money")
    line2 = html.escape(dx)
    if m and w.get("iq") is not None:
        line2 += " Copying it would have " + ("lost you money." if m["copy"] < 0 else "made you money.")
    head = f"<b>WALLET XRAY</b> · <code>{w['addr']}</code> · {chain}\n{line2}\n\n"
    money = ""
    if m:
        money = (f"Wallet made  <b>{usd(m['pnl'])}</b> ({pct(m['pnl'], m['dep'])}) · ${m['dep']:,} invested\n"
                 f"Copying it  <b>{usd(m['copy'])}</b> ({pct(m['copy'], m['dep'])}) · fast copy {usd(m['fast'])}\n"
                 f"Win rate  {m['wins']} of {m['n']} tokens · best token {usd(m['best'])}\n\n")
    if w.get("iq") is None:
        nums = f"Wallet IQ  ??\nYour IQ  ??\nVerdict  👻 <b>{w['en']}</b>\n"
    else:
        star = "*" if w.get("star") else ""
        nums = (f"Wallet IQ  <b>{w['iq']}{star}</b> · its own trades\n"
                f"Your IQ  <b>{w['you']}</b> · if you copy it\n"
                f"Verdict  {PROFILE_ICON.get(w.get('profile'), '⚪')} <b>{w['en']}</b>\n")
    raws = w.get("raw") or ["—"] * 5
    if w.get("example"):
        foot = "Example wallet with invented numbers."
    else:
        win = (w.get("window") or "").split(" · ")[0]
        foot = f"📊 {w.get('trades', '?')} trades · {html.escape(win)}"
    if left is not None:
        foot += f"\n🎟 {left} of {FREE_PER_DAY} free tests left today"

    def build(detail):
        rows = "\n".join(f"- {n} {scaled(v)}" + (f" · {html.escape(r)}" if detail else "")
                         for n, v, r in zip(CHECK_NAME, w["s"], raws))
        return f"{head}{money}{nums}\n<b>Five checks</b> (score 1–19)\n{rows}\n\n{foot}\nNot financial advice."
    out = build(True)
    return out if len(out) <= 1000 else build(False)


def duel(a, b):
    rows = "\n".join(f"<code>{k}  {scaled(a['s'][i]):>2} | {scaled(b['s'][i]):<2}</code>" for i, (k, _) in enumerate(TESTS))
    ya, yb = a.get("you"), b.get("you")
    if ya is None or yb is None:
        verdict = "One wallet is unreadable. No duel result."
    elif ya == yb:
        verdict = f"Tie on Your IQ ({ya})."
    else:
        win, lose = (a, b) if ya > yb else (b, a)
        verdict = f"Higher Your IQ: <b>{win['addr']}</b> ({win['you']} vs {lose['you']})."
    iq = lambda w: "??" if w.get("iq") is None else f"{w['iq']} / {w['you']}"
    return (f"<b>IQ DUEL</b>\n{a['addr']}  {iq(a)}  {a['en']}\n{b['addr']}  {iq(b)}  {b['en']}\n\n"
            f"{rows}\n\n{verdict}\nMeasurement only, not advice.")


def keyboard(w):
    row = []
    if not w.get("example"):
        row.append({"text": "👁 Watch this wallet", "callback_data": "w:" + w["id"]})
    row.append({"text": "🌐 walletxray.xyz", "url": "https://" + SITE})
    return {"inline_keyboard": [row]}


START_KB = {"inline_keyboard": [
    [{"text": "🦈 Try an example", "callback_data": "x:0xa11c00000000000000000000000000000000e5d0"}],
    [{"text": "🧠 Masterminds", "callback_data": "l:m"}, {"text": "🦈 Predators", "callback_data": "l:p"}],
    [{"text": "🌐 walletxray.xyz", "url": "https://" + SITE}]]}


HELP = ("Paste a wallet address and I'll test it (about 1 minute).\n\n"
        "/iq 0x… · both scores and the five checks (add <i>base</i> for Base; Solana is detected)\n"
        "/duel 0x… 0x… · two wallets side by side\n"
        "/watch 0x… · re-tested every 12h, ping if it turns Predator\n"
        "/masterminds · /predators · wallets tested this week\n\n"
        f"{FREE_PER_DAY} free tests a day.")
WELCOME = ("<b>WALLET XRAY</b> · BNB Chain utility\n\n"
           "Copy-trading tools show what a wallet made. I show what <b>you</b> would make copying it: "
           "every trade replayed a few seconds late, after the 1% fee.\n\n"
           "🧠 <b>Wallet IQ</b> · how good it is on its own trades\n"
           "👤 <b>Your IQ</b> · how good it is for you if you copy it\n\n"
           "<b>How to use</b>\n"
           "1. Paste any wallet address\n"
           "2. Wait about 1 minute\n"
           "3. Get both scores, the verdict and a card to share\n\n"
           "/duel 0x… 0x… · two wallets side by side\n"
           "/watch 0x… · alert if it turns Predator\n\n"
           f"🎟 {FREE_PER_DAY} free tests a day · BNB Chain, Base, Solana")


# ---------------------------------------------------------------- storage
class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS users(chat INTEGER PRIMARY KEY, day TEXT, used INTEGER DEFAULT 0, lab INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS watches(chat INTEGER, addr TEXT, since INTEGER, PRIMARY KEY(chat, addr));
          CREATE TABLE IF NOT EXISTS results(id TEXT PRIMARY KEY, chain TEXT, at INTEGER, doc TEXT);
        """)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(watches)")]
        if "chain" not in cols:
            self.db.execute("ALTER TABLE watches ADD COLUMN chain TEXT DEFAULT 'bsc'")
            self.db.execute("ALTER TABLE watches ADD COLUMN last_at INTEGER DEFAULT 0")
            self.db.execute("ALTER TABLE watches ADD COLUMN last_doc TEXT")
        self.db.commit()

    def q(self, sql, args=(), commit=False):
        with self.lock:
            cur = self.db.execute(sql, args)
            rows = cur.fetchall()
            if commit:
                self.db.commit()
            return rows

    def left(self, chat):
        self.q("INSERT OR IGNORE INTO users(chat) VALUES(?)", (chat,), True)
        day, used, lab = self.q("SELECT day, used, lab FROM users WHERE chat=?", (chat,))[0]
        if lab:
            return 10 ** 6
        return FREE_PER_DAY - (used if day == today() else 0)

    def spend(self, chat):
        left = self.left(chat)
        if left <= 0:
            return -1
        day, used = self.q("SELECT day, used FROM users WHERE chat=?", (chat,))[0]
        used = used if day == today() else 0
        self.q("UPDATE users SET day=?, used=? WHERE chat=?", (today(), used + 1, chat), True)
        return left - 1

    def refund(self, chat):
        self.q("UPDATE users SET used=MAX(0, used-1) WHERE chat=? AND day=?", (chat, today()), True)

    def save(self, w, chain):
        self.q("INSERT OR REPLACE INTO results VALUES(?,?,?,?)", (w["id"], chain, int(time.time()), json.dumps(w)), True)

    def result(self, wid, max_age=None):
        r = self.q("SELECT at, doc FROM results WHERE id=?", (wid,))
        if not r or (max_age and time.time() - r[0][0] > max_age):
            return None
        return json.loads(r[0][1])

    def week(self):
        return [json.loads(d) for (d,) in self.q("SELECT doc FROM results WHERE at>?", (int(time.time()) - 7 * 86400,))]

    def watch(self, chat, addr, chain, on=True):
        if on:
            n = self.q("SELECT COUNT(*) FROM watches WHERE chat=?", (chat,))[0][0]
            if n >= MAX_WATCH and not self.q("SELECT 1 FROM watches WHERE chat=? AND addr=?", (chat, addr)):
                return False
            self.q("INSERT OR IGNORE INTO watches(chat, addr, since, chain, last_at) VALUES(?,?,?,?,?)",
                   (chat, addr, int(time.time()), chain, int(time.time())), True)
        else:
            self.q("DELETE FROM watches WHERE chat=? AND addr=?", (chat, addr), True)
        return True


# ---------------------------------------------------------------- telegram
class TG:
    def __init__(self, tok):
        self.base = f"https://api.telegram.org/bot{tok}/"

    def call(self, method, **params):
        req = urllib.request.Request(self.base + method, data=json.dumps(params).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=70) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method}: {e.code} {e.read()[:300]!r}") from None

    def photo(self, chat, png, caption="", kb=None):
        """png = bytes to upload, or a Telegram file_id string to resend."""
        if isinstance(png, str):
            p = {"chat_id": chat, "photo": png, "caption": caption, "parse_mode": "HTML"}
            if kb:
                p["reply_markup"] = kb
            return self.call("sendPhoto", **p)
        b = uuid.uuid4().hex
        extra = f'--{b}\r\nContent-Disposition: form-data; name="reply_markup"\r\n\r\n{json.dumps(kb)}\r\n' if kb else ""
        body = b"".join([
            f'--{b}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'.encode(),
            f'--{b}\r\nContent-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'.encode(),
            extra.encode(),
            f'--{b}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode(),
            f'--{b}\r\nContent-Disposition: form-data; name="photo"; filename="walletxray.png"\r\nContent-Type: image/png\r\n\r\n'.encode(),
            png, f"\r\n--{b}--\r\n".encode()])
        req = urllib.request.Request(self.base + "sendPhoto", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)


# ---------------------------------------------------------------- handlers
class Bot:
    def __init__(self, tg, store, ex):
        self.tg, self.s, self.ex = tg, store, ex
        self.jobs = queue.Queue()
        self.banners = {}
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self.watcher, daemon=True).start()

    def show(self, chat, w, left=None, mid=None, prefix=""):
        """Result as the share card image with a structured caption; falls back to text."""
        import card
        if mid:
            try:
                self.tg.call("deleteMessage", chat_id=chat, message_id=mid)
            except Exception:
                pass
        try:
            cap = prefix + caption(w, left)
            r = self.tg.photo(chat, card.render(w), cap if len(cap) <= 1024 else caption(w, left), keyboard(w))
            print(f"result sent chat={chat} {w['addr']} {w.get('iq')}/{w.get('you')}", flush=True)
            return r
        except Exception:
            traceback.print_exc()
            return self.send(chat, prefix + report(w, left), keyboard(w))

    def banner(self, chat, key, make, text, kb=None):
        """Brand banner + caption; the image is uploaded once, then resent by file_id."""
        fid = self.banners.get(key)
        try:
            r = self.tg.photo(chat, fid or make(), text[:1024], kb)
            if not fid:
                self.banners[key] = r["result"]["photo"][-1]["file_id"]
            return r
        except Exception:
            traceback.print_exc()
            return self.send(chat, text, kb)

    def send(self, chat, text, kb=None):
        p = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if kb:
            p["reply_markup"] = kb
        return self.tg.call("sendMessage", **p)

    def edit(self, chat, mid, text, kb=None):
        p = {"chat_id": chat, "message_id": mid, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if kb:
            p["reply_markup"] = kb
        try:
            return self.tg.call("editMessageText", **p)
        except Exception:
            return self.send(chat, text, kb)

    # ---- one live test, with a progress message that gets replaced by the report
    def run_test(self, chat, mid, addr, chain, label=""):
        last = [0.0]

        def progress(msg):
            if time.time() - last[0] > 8:
                last[0] = time.time()
                self.edit(chat, mid, f"{label}Testing <b>{addr[:6]}…{addr[-4:]}</b> · {live.CHAIN_NAME[chain]}\n{html.escape(msg)}\n<i>About 1 minute.</i>")
        wid = addr if chain == "sol" else addr.lower()
        w = self.s.result(wid, FRESH)
        if w:
            return w, True
        w = live.test(addr, chain, progress)
        if not w.get("insufficient"):
            self.s.save(w, chain)
        return w, False

    def worker(self):
        while True:
            job = self.jobs.get()
            try:
                getattr(self, "job_" + job["kind"])(**{k: v for k, v in job.items() if k != "kind"})
            except Exception:
                traceback.print_exc()
            finally:
                self.jobs.task_done()

    def fail_text(self, e):
        if isinstance(e, live.RateLimited):
            return "The data source is busy right now. Try again in a few minutes. This test was not counted."
        return f"Could not read this wallet ({html.escape(str(e)[:80])}). This test was not counted."

    def job_iq(self, chat, mid, addr, chain):
        try:
            w, cached = self.run_test(chat, mid, addr, chain)
        except Exception as e:
            self.s.refund(chat)
            return self.edit(chat, mid, self.fail_text(e))
        left = self.s.left(chat)
        if cached:
            self.s.refund(chat)
            left = self.s.left(chat)
        if w.get("insufficient"):
            self.s.refund(chat)
            return self.edit(chat, mid, f"<b>{addr[:6]}…{addr[-4:]}</b> traded only {w['tokens']} tokens recently. "
                                        "I need at least 8 to tell skill from luck. This test was not counted.")
        self.show(chat, w, min(left, FREE_PER_DAY), mid)

    def job_duel(self, chat, mid, pair):
        out = []
        for i, (addr, chain) in enumerate(pair):
            ex = self.ex.get(addr)
            if ex:
                out.append(ex)
                continue
            try:
                w, _ = self.run_test(chat, mid, addr, chain, f"Duel · wallet {i + 1} of 2\n")
            except Exception as e:
                self.s.refund(chat)
                return self.edit(chat, mid, self.fail_text(e))
            if w.get("insufficient"):
                self.s.refund(chat)
                return self.edit(chat, mid, f"{addr[:6]}…{addr[-4:]} has too few trades to score. Duel not counted.")
            out.append(w)
        self.edit(chat, mid, duel(out[0], out[1]))

    def job_watchcheck(self, chat, addr, chain, prev):
        try:
            w = live.test(addr, chain)
        except Exception:
            return
        if w.get("insufficient"):
            return
        self.s.save(w, chain)
        self.s.q("UPDATE watches SET last_at=?, last_doc=? WHERE chat=? AND addr=?",
                 (int(time.time()), json.dumps(w), chat, addr), True)
        if not prev:
            return
        turned = w["profile"] == "predator" and prev.get("profile") != "predator"
        dropped = prev.get("you") is not None and w["you"] <= prev["you"] - 10
        if turned or dropped:
            why = "It now sells into its followers (Predator)." if turned else f"Your IQ dropped from {prev['you']} to {w['you']}."
            self.show(chat, w, prefix=f"🚨 <b>Watch alert</b> · {why}\n\n")

    def watcher(self):
        """Re-test watched wallets every 12h, only while nobody is waiting for a test."""
        while True:
            time.sleep(600)
            try:
                due = self.s.q("SELECT chat, addr, chain, last_doc FROM watches WHERE last_at < ? ORDER BY last_at LIMIT 3",
                               (int(time.time()) - WATCH_EVERY,))
                for chat, addr, chain, doc in due:
                    while not self.jobs.empty():
                        time.sleep(30)
                    self.s.q("UPDATE watches SET last_at=? WHERE chat=? AND addr=?", (int(time.time()), chat, addr), True)
                    self.jobs.put({"kind": "watchcheck", "chat": chat, "addr": addr, "chain": chain or "bsc",
                                   "prev": json.loads(doc) if doc else None})
            except Exception:
                traceback.print_exc()

    def queued(self, chat, text):
        n = self.jobs.qsize()
        wait = f"\n{n} test{'s' if n != 1 else ''} ahead of you." if n else ""
        return self.send(chat, text + wait)["result"]["message_id"]

    def limit_text(self):
        return (f"You've used today's {FREE_PER_DAY} free tests. They reset at midnight UTC.\n"
                "Subscriptions with more tests are coming soon.")

    def on_message(self, m):
        chat, text = m["chat"]["id"], (m.get("text") or "").strip()
        print(f"msg chat={chat} text={text[:60]!r}", flush=True)
        cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        found = parse(text)
        if cmd == "/start":
            import card
            return self.banner(chat, "welcome", card.welcome, WELCOME, START_KB)
        if cmd == "/help":
            return self.send(chat, HELP)
        if cmd in ("/masterminds", "/genius", "/predators"):
            return self.lists(chat, cmd == "/predators")
        if cmd in ("/watch", "/unwatch"):
            if not found or "…" in found[0][0]:
                return self.send(chat, "Add a full wallet address, e.g. /watch 0x…")
            a, ch = found[0]
            a = a if ch == "sol" else a.lower()
            if cmd == "/unwatch":
                self.s.watch(chat, a, ch, False)
                return self.send(chat, f"Stopped watching {a[:6]}…{a[-4:]}.")
            if not self.s.watch(chat, a, ch):
                return self.send(chat, f"You can watch up to {MAX_WATCH} wallets. /unwatch one first.")
            return self.send(chat, f"Watching {a[:6]}…{a[-4:]}. I re-test it every 12 hours and ping you if it turns Predator "
                                   "or Your IQ drops by 10 or more. Watching doesn't use your daily tests.")
        if cmd == "/duel":
            if len(found) < 2:
                return self.send(chat, "Send two wallets: /duel 0x… 0x…")
            pair = found[:2]
            if any("…" in a and not self.ex.get(a) for a, _ in pair):
                return self.send(chat, "Paste full addresses (42 characters).")
            if sum(1 for a, _ in pair if not self.ex.get(a)) and self.s.spend(chat) < 0:
                return self.send(chat, self.limit_text())
            mid = self.queued(chat, "Duel started. Testing two wallets takes about 2 minutes.")
            return self.jobs.put({"kind": "duel", "chat": chat, "mid": mid, "pair": pair})
        if cmd == "/iq" or found:
            if not found:
                return self.send(chat, "Add a wallet address, e.g. /iq 0x…")
            a, ch = found[0]
            ex = self.ex.get(a)
            if ex:
                return self.show(chat, ex)
            if "…" in a:
                return self.send(chat, "Paste the full address (42 characters), not the short form.")
            if self.s.spend(chat) < 0:
                return self.send(chat, self.limit_text())
            mid = self.queued(chat, f"Testing <b>{a[:6]}…{a[-4:]}</b> · {live.CHAIN_NAME[ch]}\nReplaying its trades as a copier would get them. About 1 minute.")
            return self.jobs.put({"kind": "iq", "chat": chat, "mid": mid, "addr": a, "chain": ch})
        return self.send(chat, HELP)

    def lists(self, chat, predators):
        rows, seen = [], set()
        for w in sorted(self.s.week(), key=lambda w: -(w["iq"] - w["you"]) if predators else -w["you"]):
            if w["id"] in seen:
                continue
            seen.add(w["id"])
            if predators and w["profile"] == "predator" or not predators and w["profile"] in ("genius", "sharp"):
                rows.append(w)
        rows = rows[:10]
        if predators:
            title = "🦈 <b>PREDATOR LIST</b>\nWallets that sell into their copiers · Wallet IQ / Your IQ"
        else:
            title = "🧠 <b>MASTERMIND LIST</b>\nBest Your IQ this week · Wallet IQ / Your IQ"
        body = "\n".join(f"{i}. <code>{w['id']}</code>\n     {w['iq']} / <b>{w['you']}</b> · {w['chain']}" for i, w in enumerate(rows, 1))
        import card
        return self.banner(chat, "pred" if predators else "mm", lambda: card.list_banner(predators), title + "\n\n" + (body or "None yet this week. The list fills as people test wallets.") +
                         "\n\n<i>From wallets tested in the last 7 days. Not advice.</i>")

    def on_callback(self, q):
        chat = q["message"]["chat"]["id"]
        kind, wid = q["data"].split(":", 1)
        self.tg.call("answerCallbackQuery", callback_query_id=q["id"])
        if kind == "l":
            return self.lists(chat, wid == "p")
        if kind == "x":
            ex = self.ex.get(wid)
            return self.show(chat, ex) if ex else None
        w = self.ex.get(wid) or self.s.result(wid)
        if not w:
            return self.send(chat, "That result expired. Test the wallet again.")
        if kind == "c":
            import card
            return self.tg.photo(chat, card.render(w), f"Wallet Xray · {w['addr']} · {w['en']} · {SITE}")
        if kind == "w":
            ch = {"Solana": "sol", "Base": "base"}.get(w.get("chain"), "bsc")
            if not self.s.watch(chat, wid, ch):
                return self.send(chat, f"You can watch up to {MAX_WATCH} wallets. /unwatch one first.")
            self.s.q("UPDATE watches SET last_doc=? WHERE chat=? AND addr=?", (json.dumps(w), chat, wid), True)
            return self.send(chat, f"Watching {w['addr']}. I re-test it every 12 hours and ping you if it turns Predator "
                                   "or Your IQ drops by 10 or more.")


def token():
    return os.environ.get("WIQ_BOT_TOKEN", "")


def main():
    tok = token()
    if not tok:
        raise SystemExit("Set WIQ_BOT_TOKEN to your @BotFather token")
    tg = TG(tok)
    bot = Bot(tg, Store(os.environ.get("WIQ_DB", os.path.join(D, "walletxray.db"))), Examples())
    tg.call("setMyCommands", commands=[
        {"command": "iq", "description": "Test a wallet"},
        {"command": "duel", "description": "Two wallets side by side"},
        {"command": "watch", "description": "Ping when a wallet turns Predator"},
        {"command": "masterminds", "description": "Best wallets tested this week"},
        {"command": "predators", "description": "Predators tested this week"},
    ])
    try:
        tg.call("setMyShortDescription", short_description="Paste a wallet, get its IQ and yours: what copying it really makes you. BNB Chain, Base, Solana.")
        tg.call("setMyDescription", description=("Wallet Xray · BNB Chain utility\n\n"
            "Copy-trading tools show what a wallet made. Wallet Xray shows what YOU would make copying it: "
            "every trade replayed a few seconds late, after bot fees.\n\n"
            "Paste any wallet and get two scores in about a minute: Wallet IQ and Your IQ, plus the verdict "
            "(Mastermind, Lucky, Predator…) and a card to share.\n\n15 free tests a day. Read-only, no wallet connect."))
    except Exception:
        traceback.print_exc()
    try:
        import card
        card.HANDLE = "@" + tg.call("getMe")["result"]["username"]
    except Exception:
        pass
    print("Wallet Xray bot running", flush=True)
    offset = 0
    while True:
        try:
            r = tg.call("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                try:
                    if "message" in u:
                        bot.on_message(u["message"])
                    elif "callback_query" in u:
                        bot.on_callback(u["callback_query"])
                except Exception:
                    traceback.print_exc()
        except Exception as e:
            print("poll error:", repr(e), flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
