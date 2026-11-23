#!/usr/bin/env python3
"""james_pipe.py — "James Private Signal Inbox" -> Hermes brain -> Tiger paper trades.

Flow:
  INGEST   telethon listener on the James channel (+ backlog catch-up, dedupe by msg id)
  PARSE    raw message ( + chart photo) -> Hermes Agent (hermes chat -Q, AGENTS.md charter)
           -> strict JSON trade card. Hermes failure = signal dropped, never guessed.
  MAP      instrument -> SPY / QQQ via config instrument_map (unknown -> ledger-only)
  EXECUTE  long  -> Tiger paper MKT buy (or ledger-simulated if dry_run / no Tiger)
           short -> ledger-simulated short (Tiger paper accounts cannot short)
  MANAGE   every cycle: quotes vs stop/target -> close longs via Tiger MKT sell,
           book simulated closes; fill checks for pending Tiger orders
  LEDGER   signals.csv + Telegram Saved Messages trade cards

File commands (one item per line, consumed each cycle):
  close.txt    "all" or <trade_id>   close open paper position(s)

Usage: python james_pipe.py [--dry]   (--dry forces dry_run for this run)
"""

import asyncio
import calendar
import csv
import json
import re
import subprocess
import sys
import time

from james_images import extract_image_fields, merge_into_record
from james_book import Book, message_to_op, Op, PosState
from james_core import (
    Record, MsgClass, Action, RiskTier, RiskPolicy, ExitEngine, ExitType,
    ScopeResolver, Leg, anchor_close_cap,
)
import winreg
import threading

_DEDUPE_LOCK = threading.Lock()
_INFLIGHT = set()        # DESIGN #1: messages currently being processed


def mark_completed(st, msg_id):
    """Record a message as fully handled. Called ONLY when the queue item
    finishes (success or terminal failure), never on entry."""
    msg_id = str(msg_id)
    _INFLIGHT.discard(msg_id)
    with _DEDUPE_LOCK:
        st.setdefault("seen_msg", {})[msg_id] = now()
        if len(st["seen_msg"]) > 500:
            for k in list(st["seen_msg"])[:-500]:
                st["seen_msg"].pop(k)

from datetime import date as _date, datetime, timezone, timedelta
from pathlib import Path

from telethon import TelegramClient, events


def _refresh_env_keys():
    """Long-lived parents (VBS chain, gateways) cache env at login — pull the
    current GEMINI_API_KEY from the registry so restarts always use the latest key."""
    try:
        k = winreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment"),
                                "GEMINI_API_KEY")[0]
        if k:
            import os
            os.environ["GEMINI_API_KEY"] = k
    except OSError:
        pass


_refresh_env_keys()

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
STATE = HERE / "state.json"
LEDGER = HERE / "signals.csv"
PRED_FILE = HERE / "james_predictions.csv"
PRED_HEAD = ["date", "msg_ref", "prediction", "type", "level", "claimed",
             "actual_outcome", "result", "notes", "", "verified"]
JOUT = HERE / "james_outcomes.csv"
JOUT_COLS = ["ts", "msg_id", "underlying", "structure", "strikes", "qty",
             "last_mark_pnl", "claimed_pct", "result", "our_trade", "raw"]
PKL = HERE / "parking_lot.csv"
CLOSE = HERE / "close.txt"
QUERY_FILE = HERE / "parse_query.txt"
PHOTOS = HERE / "photos"
ENV_PATH = Path(r"F:\Projects on Trading\Telegram\.env")
SESSION = HERE / "telegram_watcher.session"
PUSH_SESSION = HERE / "telegram_push.session"

DEFAULTS = {
    "channel": "James Private Signal Inbox",
    "catchup_limit": 10,
    "catchup_max_age_min": 720,
    "hermes_bin": r"C:\Users\passm\AppData\Local\hermes\bin\hermes.cmd",
    "hermes_provider": "nous",
    "hermes_model": "poolside/laguna-xs-2.1:free",
    "hermes_fallback_models": ["stepfun/step-3.7-flash:free"],
    "hermes_timeout": 180,
    "notional_usd": 1000,
    "default_stop_pct": 1.5,
    "index_divisors": {"SPX500": 10.0, "NDX100": 44.0},
    "instrument_map": {"SPX500": "SPY", "NDX100": "QQQ", "SPY": "SPY", "QQQ": "QQQ"},
    "tiger_props": str(HERE / "tiger_openapi_config.properties"),
    "dry_run": True,
    "recheck_seconds": 60,
    "max_parse_tries": 48,
    "max_open": 20,
    "reentry_minutes": 60,
    "health_check_minutes": 15,
    "health_reminder_hours": 2,
    "tiger_stock_fallback": False,   # OFF: Tiger's delayed feed caused bad paper fills
    "max_open_retry_hours": 6,   # session hours; queued entries wait outside market hours
    "gap_backscan": 60,
    "alert_cap_per_day": 40,
    "max_qty": 10,
    "single_stop_pct": 40.0,   # singles need room; 2.5% is noise
    "single_max_risk_usd": 300.0,
    "img_enrich_timeout": 45,
    "itm_guard": True,
    "itm_buffer_pct": 0.05,
    # break-even must sit >=1x credit from spot; replaced by a real
    # expected-move test once Skylit is paid (skylit_paid_backlog.md S1)
    "itm_cushion_credits": 0.0,   # 0 = break-even must not be behind spot
    "strike_far_pct": 15.0,  # beyond this the strike is a different bet
    "strike_far_guard": True,
    "naked_breaker_usd": 300.0,
    "naked_loss_streak": 3,
}

now = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
today = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d")


def age_hours(ts):
    """Hours since a UTC timestamp written by now(). time.mktime() would read the
    string as LOCAL time, inflating the age by the UTC offset (13h in NZ)."""
    try:
        return (time.time() - calendar.timegm(
            time.strptime(ts, "%Y-%m-%d %H:%M:%S"))) / 3600
    except (TypeError, ValueError):
        return 0.0


def _is_test_run():
    """D102 (2026-11-18): test fixtures (order T1/T45, msg 2, msg X9) were
    writing into the LIVE james_pipe.log that the watchdog + overnight watch
    scan, so every suite run inflated the alert count. Under a test module,
    log() writes to a throwaway file instead - test noise must never become a
    production alert."""
    import sys
    argv0 = (sys.argv[0] or "").lower()
    return "test_" in argv0 or "_test" in argv0 or "pytest" in argv0


def log(msg):
    line = f"{now()}  {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    target = HERE / ("james_pipe.test.log" if _is_test_run() else "james_pipe.log")
    try:
        with target.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
def load(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save_state(st):
    STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")

# ------------------------------------------------------------ durable queue
# DESIGN #1 (2026-11-18). The in-memory asyncio.Queue lost everything on a
# restart: items sitting in it were already marked `seen_msg`, so a crash
# mid-queue meant SILENT LOSS of signals that had arrived. This queue writes
# each item to disk BEFORE processing and removes it only AFTER completion, so
# a restart resumes exactly where it stopped.
#
# PRIORITY (user model 2026-11-18): a time-bound message must not wait behind
# backlog. Lower `prio` wins, then FIFO by enqueue time:
#   0 = live traffic (arrived while running)
#   1 = backlog / catch-up / gap recovery
#   2 = stale re-delivery (recorded, never traded)
# A `deadline` (epoch seconds, or None) marks a signal that stops being
# actionable at a moment — a 0DTE "for today" is worthless after the close.
QUEUE_FILE = HERE / "msg_queue.json"

_AUTO = object()   # sentinel: "derive the deadline from the text"
_QUEUE = None      # the live DurableQueue (set in main) for supersession


def _item_deadline(text, msg_dt, expiry=None):
    """DESIGN #4: when does this message stop being actionable?
    A 0DTE 'for today' parsed at 15:58 must NOT trade at 16:05 — by then the
    instruction describes a market that no longer exists. Returns epoch secs
    or None (no deadline: swing/dated signals stay valid until their expiry).

    Rules (deliberately conservative — a wrong deadline parks money):
      * 'for today' / 0DTE  -> the US cash close of that session
      * an explicit expiry date -> 16:00 ET on that date
      * anything else       -> None (no deadline)
    """
    t = (text or "").lower()
    today_intraday = bool(re.search(r"\bfor\s+today\b|\b0\s*dte\b", t))
    if today_intraday:
        try:
            sess = us_session_for_today() if not msg_dt else                 us_session_at(time.strftime("%Y-%m-%d %H:%M:%S",
                                            time.localtime(msg_dt)))
        except Exception:
            sess = us_session_for_today()
        # 16:00 ET close == 21:00 UTC (EST) / 20:00 UTC (EDT). Use 21:00 UTC
        # as the outer bound: erring LATE never blocks a valid trade.
        close = datetime(sess.year, sess.month, sess.day, 21, 0,
                         tzinfo=timezone.utc)
        return close.timestamp()
    return None


def _supersede_targets(text, msg_id, queue):
    """DESIGN #3: a later message can RETIRE an earlier one that has not run yet.

    Only explicit retraction language counts. Guessing from topic overlap would
    drop real signals, so the rules are deliberately narrow:
      * "ignore that" / "disregard the last" / "scratch that" / "cancel that"
        -> retract the most recent still-pending ENTRY
      * "cancel <TICKER>" -> retract pending entries naming that ticker
    A message that merely CONTRADICTS an earlier one is NOT a retraction:
    contradiction is ordinary trading; only explicit withdrawal retires a signal.
    """
    if queue is None:
        return []
    t = (text or "").lower()
    pat = r"\b(?:ignore|disregard|scratch|cancel|forget|scrap)\s+(?:that|this|it|the last|my last|previous)\b"
    m = re.search(pat, t)
    tick = re.search(r"\bcancel\s+([A-Za-z]{2,5})\b", text or "")
    if not m and not tick:
        return []
    pend = [x for x in queue.snapshot() if x.get("kind") != "retry"]
    if tick:
        want = tick.group(1).upper()
        return [x["mid"] for x in pend if want in (x.get("text") or "").upper()]
    if pend:
        newest = max(pend, key=lambda x: float(x.get("enq", 0)))
        try:
            if int(str(newest["mid"])) > int(str(msg_id)):
                return []          # the queued one is NEWER: never retract forward
        except (TypeError, ValueError):
            pass
        return [newest["mid"]]
    return []


class DurableQueue:
    """Crash-proof message queue: disk is the source of truth."""

    def __init__(self, path=None):
        self.path = path or QUEUE_FILE
        self.items = []
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = data if isinstance(data, list) else []
        except Exception:
            self.items = []

    def _save(self):
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.items, indent=0), encoding="utf-8")
            tmp.replace(self.path)          # atomic: never a half-written queue
        except Exception as e:
            log(f"durable queue save failed: {type(e).__name__}: {e}")

    # -- api ---------------------------------------------------------------
    def put(self, kind, text, mid, dt=None, photo=None, stale=False,
            tries=0, prio=0, deadline=_AUTO):
        """Enqueue. Returns False when the message is already queued (a
        re-delivery or a retry of something still pending)."""
        mid = str(mid)
        if any(str(x.get("mid")) == mid for x in self.items):
            return False
        if deadline is _AUTO:            # DESIGN #4: derive it from the text
            deadline = _item_deadline(text, dt)
        self.items.append({"kind": kind, "text": text or "", "mid": mid,
                           "dt": dt, "photo": photo, "stale": bool(stale),
                           "tries": int(tries), "prio": int(prio),
                           "deadline": deadline, "enq": time.time()})
        self._save()
        return True

    def _audit(self, event, item, reason=""):
        """Append the transition to the LEDGER (append-only history). The queue
        file is mutable STATE; the ledger is the permanent audit trail."""
        try:
            ledger_row({"event": event, "msg_id": str(item.get("mid") or ""),
                        "instrument": None, "direction": None,
                        "reason": reason or f"queue {event.lower()} "
                                            f"(prio {item.get('prio', 0)})",
                        "raw": (item.get("text") or "")[:100]})
        except Exception:
            pass

    def _sort(self):
        self.items.sort(key=lambda x: (int(x.get("prio", 0)),
                                       float(x.get("enq", 0))))

    def peek(self, skip_mid=None):
        """The next item WITHOUT removing it (DESIGN #2: peek-ahead join).
        `skip_mid` skips the currently-processed message."""
        if not self.items:
            return None
        self._sort()
        for it in self.items:
            if skip_mid is not None and str(it.get("mid")) == str(skip_mid):
                continue
            return it
        return None

    def pop(self):
        """Highest priority, oldest first. None when empty."""
        if not self.items:
            return None
        self._sort()
        it = self.items.pop(0)
        self._save()
        self._audit("QUEUE_POP", it)
        return it

    def drop(self, mid):
        """Remove a message from the queue (supersession / cancel)."""
        mid = str(mid)
        before = len(self.items)
        self.items = [x for x in self.items if str(x.get("mid")) != mid]
        if len(self.items) != before:
            self._save()
            self._audit("QUEUE_DROP", {"mid": mid},
                        reason="removed from the queue (supersession/cancel)")
            return True
        return False

    def depth(self):
        return len(self.items)

    def snapshot(self):
        return list(self.items)



CFG = {**DEFAULTS, **load(CONFIG, {})}


# ---------------------------------------------------------------- blackbull executor

_bb = None


def bbc():
    global _bb
    if _bb is None:
        import bb_executor as mod
        _bb = mod
    return _bb


BB_ETF_QUOTES = {"SPY": "SPY.P", "QQQ": "QQQ.OQ"}   # real-time marks on BlackBull


def quote_for(t):
    """Price in the trade's own terms: native index CFD for bb, ETF for tiger.
    Tiger's US quotes are ~15min delayed — mark tiger-leg exits with BlackBull's
    real-time SPY.P / QQQ.OQ prices when available, Tiger delayed otherwise."""
    try:
        if t.get("executor") == "tradier_stock":
            return _TRADIER().quote_stock(t.get("symbol") or "")
        if t.get("executor") == "bb":
            q = bbc().get_quote(t.get("instrument") or "")
            if q:
                return q
        etf = BB_ETF_QUOTES.get(t.get("symbol"))
        if etf:
            q = bbc().get_symbol_quote(etf)
            if q:
                return q
    except Exception:
        pass
    sym = t.get("symbol")
    return get_quote(sym) if sym and sym != "-" else None


_UND_QUOTE = {"SPXW": "SPX500", "SPX": "SPX500", "RUTW": "RUT",
              "RUT": "RUT", "NDX": "NAS100", "SPX500": "SPX500"}


_LIST_CACHE = {}


def _listed(under, expiry, right, strike):
    """(listed?, checked?, nearest strike). Unknown chain failures pass."""
    nearest = None
    key = f"{under}|{expiry}"
    hit = _LIST_CACHE.get(key)
    if hit and time.time() - hit[0] < 60:
        opts = hit[1]
    else:
        opts = None
        for env in ("prod", "sandbox"):
            try:
                import urllib.request
                from james_exec import TradierConfig
                cfg = TradierConfig.load(env)
                req = urllib.request.Request(
                    f"{cfg.base_url}/markets/options/chains?symbol={under}"
                    f"&expiration={expiry}&greeks=false",
                    headers={"Authorization": f"Bearer {cfg.access_token}",
                             "Accept": "application/json"})
                j = json.loads(urllib.request.urlopen(req, timeout=12).read())
                opts = (j.get("options") or {}).get("option") or []
                break
            except Exception:
                opts = None
        if not opts:                 # chain not served (SPXW on both envs)
            return True, False, None
        _LIST_CACHE[key] = (time.time(), opts)
    for o in opts:
        try:
            os_ = float(o.get("strike"))
            if str(o.get("option_type", "")).lower().startswith(str(right)[:1]):
                if nearest is None or abs(os_ - float(strike)) < abs(nearest - float(strike)):
                    nearest = os_
            if (abs(os_ - float(strike)) < 1e-6
                    and str(o.get("option_type", "")).lower().startswith(str(right)[:1])):
                return True, True, os_
        except (TypeError, ValueError):
            continue
    return False, True, nearest


def _under_quote(under):
    """Underlying print for the entry-time guards. EQUITIES use the broker's
    real feed first: the MT5 CFD print can lag by points, and a stale print that
    sits above a short strike makes an ITM spread look safe (QQQ 3978). Index
    roots stay on the CFD feed, which is the venue's own level."""
    sym = _UND_QUOTE.get((under or "").upper(), (under or "").upper())
    _u = str(under or "").upper()
    idx = _u in _INDEX_ROOTS or _u in ("SPX500", "NDX100", "ES", "NQ", "SPXW500")
    def _bb():
        try:
            q = bbc().get_quote(sym)
            return float(q) if q else None
        except Exception:
            return None
    def _st():
        try:
            q = _TRADIER().quote_stock(sym)
            return float(q) if q else None
        except Exception:
            return None
    if idx:
        return _bb() or _st() or get_quote(sym)
    return _st() or _bb() or get_quote(sym)


def _anchor_spot(under, legs):
    """D140: the REAL underlying print for the close anchor. Goes through
    _under_quote (index roots = the venue's own level, equities = the real
    feed) and is band-checked against the strikes — the D133 lesson: BlackBull's
    RUT print came back as an SPX level, and an anchor on a wrong-market print
    is worse than no anchor. Implausible spot -> None -> callers keep their
    mark band (never block an exit on bad data)."""
    try:
        ks = []
        for l in legs or []:
            v = l.get("strike") if isinstance(l, dict) else (l[0] if l else None)
            if v is not None:
                ks.append(float(v))
    except (TypeError, ValueError):
        return None
    if not ks:
        return None
    try:
        S = float(_under_quote(under) or 0)
    except (TypeError, ValueError):
        return None
    if not S or not (0.4 * min(ks) <= S <= 2.5 * max(ks)):
        if S:
            log(f"anchor {under}: spot {S:,.2f} implausible vs strikes "
                f"{sorted(ks)} — anchor off (D133 band)")
        return None
    return S


def tiger_stock_quote(ticker):
    """Live-ish quote for a US equity on Tiger (delayed feed, no real-time perm)."""
    _, _, qc = tiger()
    if qc is None:
        return None
    try:
        df = qc.get_stock_delay_briefs([ticker])
        if df is not None and len(df):
            row = df.iloc[0]
            for col in ("close", "last", "pre_close"):
                v = row.get(col)
                if v and float(v) > 0:
                    return float(v)
    except Exception:
        pass
    return None


def open_trade_tiger_stock(card, msg_id, st, why_bb):
    """Fallback: BlackBull has no symbol for this ticker -> trade it on Tiger paper.
    Longs place real paper orders; Tiger paper cannot short, so shorts are
    ledger-simulated at the Tiger quote (same convention as the old tiger path)."""
    ticker = (card["instrument"] or "").strip().upper()
    trade_id = f"T{int(time.time())}"
    q = tiger_stock_quote(ticker)
    if not q:
        ledger_row({"event": "LOGGED", "trade_id": trade_id, "msg_id": msg_id,
                    "instrument": ticker, "direction": card["direction"],
                    "reason": f"no BlackBull symbol and no Tiger quote ({why_bb})",
                    "raw": (card.get("rationale") or "")[:80]})
        log(f"fallback: {ticker} unavailable on both venues — ledger-only")
        push(f"⚠️ NO VENUE for {ticker} (msg {msg_id})\n"
             f"not on BlackBull, no Tiger quote — ledger only.\n"
             f"\"{(card.get('rationale') or '')[:90]}\"", st)
        save_state(st)
        return
    # re-entry cooldown, same rule as the BB path
    for o in st.get("open", {}).values():
        if (_canon_underlying(o["instrument"]) == _canon_underlying(ticker)
                and o["direction"] == card["direction"]
                and o["status"].startswith("open")):
            try:
                age = age_hours(o.get("opened", "")) * 3600
            except ValueError:
                age = 0
            if age < CFG["reentry_minutes"] * 60:
                ledger_row({"event": "SKIP", "trade_id": trade_id, "msg_id": msg_id,
                            "instrument": ticker, "direction": card["direction"],
                            "reason": f"re-entry cooldown ({age/60:.0f}min, {o['id']} open)"})
                log(f"fallback: re-entry cooldown {ticker} — skipped")
                save_state(st)
                return
    entry = card["entry"] if card["entry"] and 0.5 * q <= card["entry"] <= 2 * q else q
    stop = card["stop"]
    if stop and entry:
        ok_side = (0.8 * entry <= stop < entry) if card["direction"] == "long" \
            else (entry < stop <= 1.2 * entry)
        if not ok_side:
            stop = None
    if stop and entry and abs(stop - entry) <= 0.001 * entry:
        stop = None
    if stop is None and entry:
        pct = CFG["default_stop_pct"] / 100
        stop = entry * (1 - pct) if card["direction"] == "long" else entry * (1 + pct)
    targets = [x for x in card["targets"]
               if ((x > entry and 1.5 * entry >= x) if card["direction"] == "long"
                   else (entry * 0.85 <= x < entry))]
    qty = max(1, int(CFG["notional_usd"] / entry))
    t = {"id": trade_id, "msg_id": msg_id, "instrument": ticker,
         "symbol": ticker, "direction": card["direction"], "entry": entry,
         "stop": stop, "targets": targets,
         "confidence": card["confidence"], "horizon": card["horizon"],
         "rationale": card["rationale"], "opened": now(),
         "status": "pending", "executor": "tiger", "k": 1.0,
         "order_id": None, "qty": qty, "exit_price": None, "exit_ts": None,
         "pnl": None, "fill_note": f"BB had no symbol ({why_bb}) -> Tiger"}
    if card["direction"] == "long":
        place_tiger_long(t, st)
    else:
        # Tiger paper cannot short: simulate at the Tiger quote
        t["status"] = "open_sim"
        t["fill_note"] += " | short simulated (Tiger paper has no shorting)"
        log(f"fallback SIM short {ticker} qty {qty} @ ~{entry:.2f}")
    st.setdefault("open", {})[trade_id] = t
    ledger_row({**t, "event": "ORDER" if t["status"] != "pending" else "PENDING",
                "targets": "|".join(f"{x:.2f}" for x in targets),
                "reason": t["fill_note"]})
    push(trade_card_text(t), st)
    save_state(st)


def _lc_start(trade_id, msg_id, instrument, detail="", raw=""):
    """D128: start a trade's lifecycle from any opener (only the multileg
    path had an inline open_lifecycle call — single-option / BB / stock
    openers never wrote OPEN, so their narratives had nothing to link to).
    Advisory only, never raises."""
    try:
        import trade_lifecycle as _tlc
        _tlc.open_lifecycle(trade_id, msg_id, instrument or "", detail, raw)
    except Exception:
        pass


def _lc_finish(t, event="EXIT", detail="", raw=""):
    """D128: close/trim a trade's lifecycle from ANY booking path (async
    Tradier close, option close, BB close, manage-loop retires...). Writes
    nothing when the trade never opened a lifecycle (order failed at
    placement) — no dangling EXIT rows. Advisory only, never raises."""
    try:
        import trade_lifecycle as _tlc
        tid = t.get("id") if isinstance(t, dict) else None
        if not tid or not _tlc.get_lifecycle(tid):
            return
        _inst = (t.get("opt_underlying") or t.get("instrument") or "")
        if event == "TRIM":
            _tlc.trim_lifecycle(tid, t.get("msg_id", ""), detail, raw, _inst)
        else:
            _tlc.exit_lifecycle(tid, t.get("msg_id", ""), detail, raw, _inst)
    except Exception:
        pass


def open_trade_bb(card, msg_id, st):
    """BlackBull MT5 executor: cash index CFDs at James's NATIVE levels.
    No index->ETF conversion (k=1); shorts are real CFD sells."""
    bb = bbc()
    trade_id = f"B{int(time.time())}"
    instrument = (card["instrument"] or "").strip().upper()
    sym = bb.resolve_symbol(instrument)
    if not sym:
        # not a BlackBull CFD -> fall back to Tiger paper for the raw ticker
        if re.fullmatch(r"[A-Z][A-Z.\-]{0,9}", instrument):
            log(f"bb: no symbol for {instrument} — trying Tradier equity")
            open_trade_tradier_stock(card, msg_id, st, f"no MT5 symbol for {instrument}")
            return
        if CFG.get("tiger_stock_fallback", True) and re.fullmatch(r"[A-Z][A-Z.\-]{0,9}", instrument):
            log(f"bb: no symbol for {instrument} — trying Tiger fallback")
            open_trade_tiger_stock(card, msg_id, st,
                                   f"no MT5 symbol for {instrument}")
            return
        ledger_row({"event": "LOGGED", "trade_id": trade_id, "msg_id": msg_id,
                    "instrument": instrument, "direction": card["direction"],
                    "reason": f"no BlackBull symbol for {instrument}",
                    "raw": (card.get("rationale") or "")[:80]})
        log(f"bb: no symbol for {instrument} — ledger-only (no delayed-feed fallback)")
        push(f"⚠️ NOT ON BLACKBULL — {instrument} (msg {msg_id})\n"
             f"No trade placed. Not in MT5's 1,669 US share CFDs, and the\n"
             f"delayed-feed fallback is OFF (it caused bad fills).\n"
             f"\"{(card.get('rationale') or '')[:90]}\"\n"
             f"Logged to ledger for tracking only.", st)
        save_state(st)
        return
    q = bb.get_quote(instrument)
    if not q:
        ledger_row({"event": "LOGGED", "trade_id": trade_id, "msg_id": msg_id,
                    "instrument": instrument, "direction": card["direction"],
                    "reason": "no bb quote", "raw": (card.get("rationale") or "")[:80]})
        log(f"bb: no quote for {sym}")
        save_state(st)
        return
    # re-entry cooldown: same instrument+direction opened recently -> skip
    for o in st.get("open", {}).values():
        if (_canon_underlying(o["instrument"]) == _canon_underlying(instrument)
                and o["direction"] == card["direction"]
                and o["status"].startswith("open")):
            opened = o.get("opened", "")
            try:
                age = age_hours(opened) * 3600
            except ValueError:
                age = 0
            if age < CFG["reentry_minutes"] * 60:
                ledger_row({"event": "SKIP", "trade_id": trade_id, "msg_id": msg_id,
                            "instrument": instrument, "direction": card["direction"],
                            "reason": f"re-entry cooldown ({age/60:.0f}min < {CFG['reentry_minutes']}min, {o['id']} open)"})
                log(f"bb: re-entry cooldown {instrument} {card['direction']} — skipped")
                save_state(st)
                return
    entry = card["entry"] if card["entry"] and 0.5 * q <= card["entry"] <= 2 * q else q
    stop = card["stop"]
    # stop must sit on the correct side at a sane distance, else use default
    if stop and entry:
        ok_side = (0.8 * entry <= stop < entry) if card["direction"] == "long" \
            else (entry < stop <= 1.2 * entry)
        if not ok_side:
            stop = None        # option-premium stop (4.5) etc. — discard
    if stop and entry and abs(stop - entry) <= 0.001 * entry:
        stop = None
    if stop is None and entry:
        pct = CFG["default_stop_pct"] / 100
        stop = entry * (1 - pct) if card["direction"] == "long" else entry * (1 + pct)
    targets = [x for x in card["targets"]
               if ((x > entry and 1.5 * entry >= x) if card["direction"] == "long"
                   else (entry * 0.85 <= x < entry))]
    t = {"id": trade_id, "msg_id": msg_id, "instrument": instrument,
         "symbol": sym, "direction": card["direction"], "entry": entry,
         "stop": stop, "targets": list(card["targets"]),
         "confidence": card["confidence"], "horizon": card["horizon"],
         "rationale": card["rationale"], "opened": now(),
         "status": "pending_open", "executor": "bb", "k": 1.0,
         "order_id": None, "bb_ticket": None, "qty": None,
         "exit_price": None, "exit_ts": None, "pnl": None,
         "fill_note": ""}
    lots = round(CFG["notional_usd"] / q, 2)
    # don't stack duplicate pendings: same instrument+direction already queued
    if any(_canon_underlying(o["instrument"]) == _canon_underlying(instrument)
           and o["direction"] == card["direction"]
           and o["status"] == "pending_open"
           for o in st.get("open", {}).values()):
        ledger_row({"event": "SKIP", "trade_id": trade_id, "msg_id": msg_id,
                    "instrument": instrument, "direction": card["direction"],
                    "reason": "duplicate pending_open for same instrument+direction"})
        log(f"bb: duplicate pending {instrument} {card['direction']} — skipped")
        save_state(st)
        return
    r = bb.open_cfd(instrument, card["direction"], lots,
                    sl=stop, tp=t["targets"][0] if t["targets"] else None)
    if r["ok"]:
        t.update(status="open_bb", bb_ticket=r["deal"], qty=r["volume"],
                 entry=r["price"] or entry)
        log(f"bb {card['direction']} {sym} {r['volume']} lots @ {r['price']}")
    else:
        t["fill_note"] = r["reason"]
        log(f"bb open deferred: {r['reason']}")
    st.setdefault("open", {})[trade_id] = t
    # D128: BB CFD trades also start a lifecycle (B1795098590 had none)
    _lc_start(trade_id, msg_id, instrument,
              f"{card['direction']} {t.get('qty') or lots} lots @ "
              f"{t.get('entry')} [{t['status']}] {(t.get('fill_note') or '')[:40]}",
              (card.get("rationale") or "")[:120])
    ledger_row({**t, "event": "ORDER" if r["ok"] else "PENDING",
                "targets": "|".join(f"{x:.2f}" for x in t["targets"]),
                "reason": t["fill_note"] or "ok"})
    push(trade_card_text(t), st)
    save_state(st)


def open_trade_tradier_stock(card, msg_id, st, why_bb):
    """Fallback when BlackBull lacks the ticker (SNDK-class): trade the equity
    on Tradier paper. Long = buy, short = sell_short."""
    ticker = (card["instrument"] or "").strip().upper()
    trade_id = f"TB{int(time.time())}"
    while trade_id in st.get("open", {}) or trade_id in st.get("closed", {}):
        trade_id += "a"
    q = _TRADIER().quote_stock(ticker)
    if not q or q <= 0:
        ledger_row({"event": "LOGGED", "trade_id": trade_id, "msg_id": msg_id,
                    "instrument": ticker, "direction": card["direction"],
                    "reason": f"no venue quote ({why_bb}) — ledger-only",
                    "raw": (card.get("rationale") or "")[:80]})
        log(f"tradier_stock: no quote {ticker} — ledger-only")
        push(f"⚠️ NO VENUE QUOTE {ticker} (msg {msg_id}) — ledger only.", st)
        save_state(st)
        return
    entry = q
    pct = CFG["default_stop_pct"] / 100
    stop = card["stop"] if card["stop"] and 0.8 * entry <= card["stop"] < entry else None
    if stop is None:
        stop = entry * (1 - pct) if card["direction"] == "long" else entry * (1 + pct)
    targets = [x for x in card["targets"] if x > entry] if card["direction"] == "long"         else [x for x in card["targets"] if x < entry]
    qty = max(1, min(int(CFG["max_qty"]), int(CFG["notional_usd"] / q)))
    t = {"id": trade_id, "msg_id": msg_id, "instrument": ticker, "symbol": ticker,
         "direction": card["direction"], "entry": entry, "stop": stop,
         "targets": targets, "confidence": card["confidence"],
         "horizon": card["horizon"], "rationale": card["rationale"],
         "opened": now(), "status": "pending", "executor": "tradier_stock",
         "k": 1.0, "order_id": None, "qty": qty, "exit_price": None,
         "exit_ts": None, "pnl": None,
         "fill_note": f"BB had no symbol ({why_bb}) -> Tradier equity",
         "tier": "high" if card["direction"] == "short" else "middle"}
    side = "buy" if card["direction"] == "long" else "sell_short"
    ex = _TRADIER().place_stock(ticker, side, qty)
    if not ex.ok:
        t["status"], t["fill_note"] = "order_failed", f"stock order failed: {ex.reason}"
        log(f"tradier_stock order failed {ticker}: {ex.reason}")
    else:
        t["order_id"] = ex.order_id
        time.sleep(3)
        st_o, fill = _tradier_close_status(ex.order_id)
        su = (st_o or "").upper()
        if "FILL" in su:
            rebase_levels(t, fill)
            t["status"] = "open_tradier_stock"
            t["fill_note"] += f" | filled @ {fill or 'mkt'}"
        elif any(x in su for x in ("REJECT", "CANCEL", "EXPIRED", "FAILED")):
            t["status"], t["pnl"] = "closed", 0.0
            t["fill_note"] = f"stock entry {su} — no position"
        else:
            t["fill_note"] += " | resting"
    if t["status"] == "closed":
        st.setdefault("closed", {})[trade_id] = t
    else:
        st.setdefault("open", {})[trade_id] = t
        # D128: stock trades start a lifecycle too (open orders only)
        if t.get("order_id"):
            _lc_start(trade_id, msg_id, ticker,
                      f"{card['direction']} {qty} @ {entry} [{t['status']}] "
                      f"{(t.get('fill_note') or '')[:40]}",
                      (card.get("rationale") or "")[:120])
    ledger_row({**t, "event": "ORDER" if t.get("order_id") else "LOGGED",
                "targets": "|".join(f"{x:.2f}" for x in targets),
                "reason": t["fill_note"]})
    push(trade_card_text(t), st)
    save_state(st)


def _us_session_bounds(d):
    """(open_utc, close_utc) naive UTC datetimes for the US cash session on
    calendar date d. D117 (2026-11-19): the old code hardcoded 13:30/21:00 UTC
    — the SUMMER (EDT) offset. In winter (EST) 13:30 UTC is 08:30 ET, a full
    hour BEFORE the 09:30 open, so pre-market signals (08:30-09:30 ET) were
    wrongly counted as in-hours and their retry timer burned early."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    open_et = datetime(d.year, d.month, d.day, 9, 30, tzinfo=et)
    close_et = datetime(d.year, d.month, d.day, 16, 0, tzinfo=et)
    return (open_et.astimezone(timezone.utc).replace(tzinfo=None),
            close_et.astimezone(timezone.utc).replace(tzinfo=None))


def open_hours_between(ts):
    """US-session hours between ts (UTC string) and now. A signal placed after the
    close, or across a weekend, must not burn its retry window while shut — TSLA
    was placed 2h post-close and its 3h timer cancelled it 11h before the open.
    D117: the session boundary is derived from the DST-aware US timezone, not a
    hardcoded UTC offset."""
    try:
        start = datetime(*time.strptime(ts, "%Y-%m-%d %H:%M:%S")[:6], tzinfo=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return 0.0
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    total, d = 0.0, start.date()
    while d <= end.date():
        if d.weekday() < 5:
            o, c = _us_session_bounds(d)
            a, b = max(start, o), min(end, c)
            if b > a:
                total += (b - a).total_seconds() / 3600
        d += timedelta(days=1)
    return total


def retry_open_bb(t, st):
    """Deferred open (close-only or market closed): retry each cycle, but never
    open a stale signal — a call from hours ago must not fill at a new price.
    While the venue is SHUT the entry waits and its timer does not move."""
    bb = bbc()
    if not market_open_now():
        if "queued" not in (t.get("fill_note") or ""):
            t["fill_note"] = "entry queued — market shut, opens on the open"
            log(f"bb entry queued (market shut) {t['id']}")
        return
    age_h = open_hours_between(t.get("opened", now()))
    if age_h > CFG["max_open_retry_hours"]:
        t["status"], t["pnl"] = "closed", 0.0
        t["fill_note"] = f"expired before fill ({age_h:.1f}h > {CFG['max_open_retry_hours']}h)"
        st.setdefault("closed", {})[t["id"]] = t
        st.get("open", {}).pop(t["id"], None)
        ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
        _lc_finish(t, "EXIT", t["fill_note"])   # D128
        log(f"bb deferred open expired after {age_h:.1f}h: {t['symbol']}")
        push(f"⌛ EXPIRED {t['symbol']} {t['direction']} [BB] — never filled\n"
             f"{t['fill_note']}\nNo position taken (price moved on).", st)
        save_state(st)
        return
    r = bb.open_cfd(t["instrument"], t["direction"], t["qty"] or
                    round(CFG["notional_usd"] / (bb.get_quote(t["instrument"]) or 1), 2),
                    sl=t["stop"], tp=t["targets"][0] if t["targets"] else None)
    if r["ok"]:
        t.update(status="open_bb", bb_ticket=r["deal"], qty=r["volume"],
                 entry=r["price"] or t["entry"], fill_note="opened on retry")
        ledger_row({**t, "event": "ORDER", "reason": "opened after close-only lift"})
        log(f"bb retry open ok: {t['symbol']} {t['qty']} @ {r['price']}")
    save_state(st)


def close_trade_bb(t, reason, st):
    bb = bbc()
    if not t.get("bb_ticket"):
        # never filled (pending_open): book it closed at zero
        t["pnl"], t["exit_price"], t["exit_ts"] = 0.0, t["entry"], now()
        t["status"], t["fill_note"] = "closed", reason + " (before fill, PnL 0)"
        st.setdefault("closed", {})[t["id"]] = t
        st.get("open", {}).pop(t["id"], None)
        ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
        _lc_finish(t, "EXIT", t["fill_note"])   # D128
        log(f"bb {t['id']} closed before fill (PnL 0)")
        save_state(st)
        return
    r = bb.close_cfd(t["bb_ticket"])
    if not r["ok"]:
        # D99/D111 (2026-11-19): 'position not found' is TERMINAL for RETRYING,
        # but it is NOT proof the position never existed — it may have already
        # closed at the broker (TP/SL hit, which is the usual reason we were
        # trying to close it). Before booking a phantom zero, recover the REAL
        # close from the deal history; only when no exit deal exists is it a
        # true phantom.
        if "not found" in str(r.get("reason", "")).lower() or \
                "no position" in str(r.get("reason", "")).lower():
            _rec = None
            try:
                _rec = bb.recover_closed_deal(t["bb_ticket"])
            except Exception as _e:
                log(f"bb recover failed {t['id']}: {type(_e).__name__}")
            if _rec and _rec.get("price") is not None:
                _px = float(_rec["price"])
                _dir = 1 if t.get("direction") == "long" else -1
                t["pnl"] = round(_dir * (_px - float(t.get("entry") or _px))
                                 * (float(t.get("qty") or 0) or 1), 2)
                t["exit_price"], t["exit_ts"] = _px, now()
                t["status"] = "closed"
                t["fill_note"] = (reason + " | recovered from deal history "
                                  f"(close {_px:.2f})")
                st.setdefault("closed", {})[t["id"]] = t
                st.get("open", {}).pop(t["id"], None)
                ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
                _lc_finish(t, "EXIT", t["fill_note"])   # D128
                push(f"✅ RECOVERED {t['symbol']} {t['direction']} [BB] — position "
                     f"already closed at broker; booked real exit {_px:.2f}, "
                     f"PnL ${t['pnl']:+.2f} (D111)", st)
                log(f"bb {t['id']} recovered close {_px:.2f} pnl {t['pnl']:+.2f}")
                save_state(st)
                return
            t["pnl"], t["exit_price"], t["exit_ts"] = 0.0, t.get("entry"), now()
            t["status"] = "closed"
            t["fill_note"] = (reason + " | broker says position not found — "
                              "booked closed-at-zero (phantom, D99)")
            st.setdefault("closed", {})[t["id"]] = t
            st.get("open", {}).pop(t["id"], None)
            ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
            _lc_finish(t, "EXIT", t["fill_note"])   # D128
            push(f"⚠️ PHANTOM POSITION {t['id']} {t.get('symbol')} — broker has no "
                 f"such position, booked closed-at-zero. Reconcile the book.", st)
            log(f"bb phantom {t['id']} closed-at-zero (position not found)")
            save_state(st)
            return
        log(f"bb close FAILED {t['symbol']}: {r['reason']} — stays open")
        return
    t["pnl"] = round((1 if t["direction"] == "long" else -1) *
                     (r["price"] - t["entry"]) * (t["qty"] or 0), 2)
    t["exit_price"], t["exit_ts"], t["status"] = r["price"], now(), "closed"
    t["fill_note"] = reason
    st.setdefault("closed", {})[t["id"]] = t
    st.get("open", {}).pop(t["id"], None)
    ledger_row({**t, "event": "EXIT", "reason": reason})
    _lc_finish(t, "EXIT", f"{reason} @ {r['price']} | PnL ${t['pnl']:+.2f}")  # D128
    push(f"{'🎯' if t['pnl'] and t['pnl'] > 0 else '🚪'} CLOSED {t['symbol']} "
         f"{t['direction']} [BB] — {reason}\nentry {t['entry']:.2f} → exit "
         f"{r['price']:.2f} | PnL ${t['pnl']:+.2f}", st)
    save_state(st)


# ---------------------------------------------------------------- options (tiger paper)

def us_now():
    """Wall-clock in the US session timezone (America/New_York). James writes
    in US session time; the NZ clock (UTC+13) runs ~a day AHEAD of it, so every
    'for today' / expiry decision must be made on the US date. ZoneInfo needs
    the tzdata package on Windows; fall back to a fixed offset derived from the
    US DST rule (2nd Sun Mar .. 1st Sun Nov) if it is missing."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        from datetime import timedelta as _td
        u = datetime.now(timezone.utc)
        y = u.year

        def _nth_sun(month, n):
            d0 = _date(y, month, 1)
            return d0 + _td(days=(6 - d0.weekday()) % 7 + (n - 1) * 7)
        dst = _nth_sun(3, 2) <= u.date() < _nth_sun(11, 1)
        return u.astimezone(timezone(_td(hours=-4 if dst else -5)))


def us_today():
    return us_now().date()


def us_session_for_today():
    """What James's 'for today' names as a US session date: the current US
    session while the market is open (weekday before 16:00 ET), else the NEXT
    weekday session — his signals land in the US evening/night after the
    close, so 'for today' then means the next open (0DTE for that session)."""
    from datetime import timedelta as _td
    n = us_now()
    d = n.date()
    if n.hour >= 16 or n.weekday() >= 5:
        d += _td(days=1)
    while d.weekday() >= 5:                    # skip the weekend
        d += _td(days=1)
    return d


def market_open_now():
    """True inside the US cash session (Mon-Fri 09:30-16:00 ET). Signals arrive
    around the clock including weekends; anything received while this is False
    must QUEUE — never burn its expiry timer while the venue is shut."""
    n = us_now()
    if n.weekday() >= 5:
        return False
    mins = n.hour * 60 + n.minute
    return 9 * 60 + 30 <= mins < 16 * 60


def next_friday(from_day=None):
    """Nearest Friday strictly AFTER the current US session date (or after
    from_day, for a retro settle anchored to the row's own date). James's
    undated options are for the upcoming Friday; the US date — not the NZ
    clock — decides how far ahead that is (at ET-Thursday evening the imminent
    Friday is tomorrow, not a week away)."""
    from datetime import timedelta
    d = (from_day or us_today()) + timedelta(days=1)
    while d.weekday() != 4:                    # nearest Friday strictly ahead
        d += timedelta(days=1)
    return d.isoformat()


def _earnings_advisory(under, expiry, msg_id):
    """DESIGN (2026-11-18): is this underlying reporting during the trade's
    life? James trades earnings constantly and they have hurt us (WPM -960 on
    a 15-day bull put that held through the event). ADVISORY ONLY: prints and
    records, never blocks, never sizes."""
    try:
        import earnings_guard as _eg
        line = _eg.check(under, expiry)
        if not line:
            return ""
        log(f"msg {msg_id}: {line}")
        ledger_row({"event": "EARNINGS", "msg_id": str(msg_id),
                    "instrument": under, "reason": line,
                    "raw": (str(under) + " " + str(expiry))[:80]})
        return line
    except Exception as e:
        log(f"earnings check skipped msg {msg_id}: {type(e).__name__}")
        return ""


def _log_split_suspect(item, nxt):
    """DESIGN #2 evidence arm: record every lone-leg message that found no
    partner, so 'do split pairs exist in this feed?' is answered by data."""
    try:
        new = not SPLIT_SUSPECTS.exists()
        with SPLIT_SUSPECTS.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ts", "msg_id", "text",
                                              "next_msg", "next_text"])
            if new:
                w.writeheader()
            w.writerow({"ts": now(), "msg_id": str(item.get("mid")),
                        "text": (item.get("text") or "")[:120],
                        "next_msg": str((nxt or {}).get("mid") or ""),
                        "next_text": ((nxt or {}).get("text") or "")[:120]})
    except Exception:
        pass


def _is_lone_leg(text):
    """DESIGN #2: does this message describe a HALF structure?
    One strike + one verb + no partner inside the same text."""
    try:
        legs = text_struct(text)[0]
    except Exception:
        return False
    return len(legs) == 1


def _joinable(a_text, b_text):
    """Are two messages halves of one spread? Conservative: same underlying
    root, one sell + one buy, different strikes, and neither carries a
    partner already. A false positive would merge two real trades, so every
    condition must hold."""
    try:
        la, lb = text_struct(a_text)[0], text_struct(b_text)[0]
    except Exception:
        return False
    if len(la) != 1 or len(lb) != 1:
        return False
    # NOTE: text_struct returns leg DICTS, not tuples - unpacking one as a
    # 3-tuple yields its KEYS ('action','strike','right'), which silently
    # broke every join test. Read the fields by name.
    _a, _b = la[0], lb[0]
    sa, ra, aa = _a.get("strike"), str(_a.get("right", "")), _a.get("action")
    sb, rb, ab = _b.get("strike"), str(_b.get("right", "")), _b.get("action")
    if None in (sa, sb) or not ra or not rb:
        return False
    if aa == ab:                       # both sell or both buy: not a vertical
        return False
    # rights must MATCH (both puts or both calls): a put+call pair is not
    # a vertical. The old test compared the booleans and always passed.
    if str(ra)[:1].lower() != str(rb)[:1].lower():
        return False
    if float(sa) == float(sb):         # same strike: not a vertical
        return False
    # same underlying: the exchange root prefix must match
    ra_root = _root_of(a_text)
    rb_root = _root_of(b_text)
    if ra_root and rb_root and ra_root != rb_root:
        return False
    return True


_TICKERS = re.compile(r"\b(SPX|SPXW|QQQ|SPY|IWM|RUT|NDX|TSLA|NVDA|AMD|SOXL|EOG|WPM|GOOG|AAPL|MSFT|META|AMZN|MSTR|HOOD|COIN)\b", re.I)


def _root_of(text):
    m = _TICKERS.search(text or "")
    return m.group(1).upper() if m else None


_EDIT_TEXT = {}


def _levels_changed(old, new):
    """Did an edit change a strike, credit or direction? Compare the numeric
    and directional tokens only - punctuation/typo edits must not raise an
    alarm (an over-eager alert trains the reader to ignore it)."""
    def sig(t):
        t = (t or "").lower()
        strikes = sorted(re.findall(r"\b\d{2,5}(?:\.d+)?\s*[cp]\b", t))
        creds = sorted(re.findall(r"collect\w*\s*\$?\s*([\d.]+)", t))
        side = "sell" if "sell" in t else ("buy" if "buy" in t else "")
        return (tuple(strikes), tuple(creds), side)
    if not old:
        return False
    return sig(old) != sig(new)


def _fill_regate(t, st):
    """D104 (2026-11-18): a pending_entry must re-earn its fill at the OPEN.
    Between parse time and the open the underlying can move against the trade
    (TL4469: short 7385 put parsed pre-open, SPX fell to 7379 - 5 pts ITM by
    the bell, with $43M of LSE put flow inside the strike). Return True to
    CANCEL the entry, False to let it fill.

    Checks, each independent:
      1. short leg now ITM vs the live underlying print
      2. 0DTE and the session has begun (no room to recover)
      3. LSE flow: put premium at/below the short strike is a spike (tape
         buying protection inside our strike)
    A failed check that cannot run (no spot, no flow) NEVER blocks - a
    missing check must not cancel a good trade, only a POSITIVE signal can."""
    try:
        under = t.get("instrument") or t.get("symbol") or ""
        legs = t.get("legs") or []
        if len(legs) < 2:
            return False
        short_leg = next((l for l in legs if str(l[2]).startswith("sell")), None)
        if not short_leg:
            return False
        strike = float(short_leg[0])
        right = str(short_leg[1])[:1].lower()
        S = _under_quote(under)
        if not S:
            return False
        # 1. short leg now ITM by more than a hair
        itm = (S < strike) if right == "p" else (S > strike)
        if itm:
            push(f"🛑 FILL GATE (msg {t.get('msg_id')}): short {right} {strike:g} "
                 f"now ITM vs spot {S:,.2f} at the open - cancelling the resting "
                 f"entry {t['id']} instead of filling a losing 0DTE.", st)
            return True
        # 2. LSE flow: for a short PUT, is the tape buying protection at/below
        # our strike? (D105: the $43M put wall behind TL4469 - a positive flow
        # signal cancels even when the leg is still OTM.)
        # D125 (2026-11-20): the SAME test for a short CALL — call premium
        # AT/ABOVE the strike is the tape buying upside against our short call.
        # Before this only puts got the flow check, so a short call spread
        # (TL4500/TL4503) was gated on the ITM test alone while every short
        # put got the tape check too.
        if right in ("p", "c"):
            try:
                import lse
                import re as _re
                _key = _re.search(
                    r"LSE_API_KEY=(.+)",
                    pathlib.Path(__import__("os").environ.get("LOCALAPPDATA", ""))
                    / "hermes/.env").group(1).strip().strip('\"').strip("'")
                _c = lse.LSE(_key)
                # D108: US-session anchor, not the host clock (NZ is a day
                # ahead; date.today() pointed at a future, print-less session).
                from zoneinfo import ZoneInfo as _zi
                _to = datetime.datetime.now(_zi("America/New_York")).date()
                _from = (_to - datetime.timedelta(days=1)).isoformat()
                _to = _to.isoformat()
                _rows = _c.options_flow(underlying=under, start=_from, end=_to,
                                        min_premium=20000, limit=200)
                _ctype = "put" if right == "p" else "call"
                # put wall: premium at/below our strike; call wall: at/above
                _sel = [r for r in _rows
                        if r.get("contract_type") == _ctype
                        and r.get("strike")
                        and ((float(r["strike"]) <= strike) if right == "p"
                             else (float(r["strike"]) >= strike))]
                _prem = sum(float(r.get("premium") or 0) for r in _sel)
                # spike threshold: > $10M of premium inside our strike is a wall
                # worth refusing (WPM ~$4.8M, TL4469 ~$43M)
                if _prem > 10_000_000:
                    push(f"🛑 FILL GATE (msg {t.get('msg_id')}): LSE shows "
                         f"${_prem:,.0f} of {_ctype} premium "
                         f"{'at/below' if right == 'p' else 'at/above'} {strike:g} "
                         f"since yesterday - the tape is buying protection "
                         f"inside our short strike. Cancelling {t['id']}.", st)
                    return True
            except Exception:
                pass     # flow is a bonus check; a missing flow never blocks
        return False
    except Exception:
        return False


def _message_tag_on_arrival(text, msg_id, under):
    """DESIGN (2026-11-18): tag every message at arrival with what we can do,
    plus an LSE flow check where the rule says it informs a decision. Runs
    OFF-THREAD so the serial queue is never stalled by a flow call."""
    def _work():
        try:
            import message_tag as _mt
            _mt.tag(msg_id, text or "", under or "",
                    session_phase(), log=lambda m: None)
        except Exception as e:
            log(f"message tag skipped msg {msg_id}: {type(e).__name__}")
    try:
        import threading
        threading.Thread(target=_work, daemon=True).start()
    except Exception:
        pass


def _flow_spike_advisory(under, msg_id, expiry):
    """DESIGN (2026-11-18): LSE put-premium spike, ADVISORY. On a short-put
    entry, check whether the underlying's fresh put premium is a multiple of
    its trailing daily median (WPM: 14.9x on entry day, the -$960 we missed).
    Runs in a daemon thread; never blocks, never sizes."""
    def _work():
        try:
            import flow_spike as _fs
            entry_day = us_session_for_today().isoformat()
            line, _row = _fs.flag_entry(under, entry_day, log=lambda m: None)
            if not line:
                return
            log(f"msg {msg_id}: {line}")
            ledger_row({"event": "FLOW_SPIKE", "msg_id": str(msg_id),
                        "instrument": under, "reason": line,
                        "raw": (str(under) + " " + str(expiry))[:80]})
        except Exception as e:
            log(f"flow spike skipped msg {msg_id}: {type(e).__name__}")
    try:
        import threading
        threading.Thread(target=_work, daemon=True).start()
    except Exception:
        pass


def _uw_snapshot_on_park(under, msg_id, reason):
    """DESIGN (user 2026-11-18): when a signal is parked for staleness/expiry,
    capture the TAPE at that moment. A park is paid-for information - the
    would-have trade AND the flow that surrounded it. Runs in a daemon thread
    with a hard timeout so the FIFO consumer is never blocked."""
    def _work():
        try:
            import uw_flow
            got = uw_flow.snapshot([under], log=lambda m: None, sessions=2)
            series = got.get(str(under).upper())
            if not series:
                return
            latest = series[0]
            row = {"ts": now(), "msg_id": str(msg_id), "underlying": str(under),
                   "session": latest[0], "close": latest[1], "pcr": latest[2],
                   "net_premium": latest[3], "park_reason": (reason or "")[:80]}
            new = not UW_PARK.exists()
            with UW_PARK.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["ts", "msg_id", "underlying",
                                                 "session", "close", "pcr",
                                                 "net_premium", "park_reason"])
                if new:
                    w.writeheader()
                w.writerow(row)
            log(f"uw: park snapshot msg {msg_id} {under} "
                f"net ${latest[3]:+,} (session {latest[0]})")
        except Exception as e:
            log(f"uw park snapshot failed msg {msg_id}: {type(e).__name__}")
    try:
        import threading
        threading.Thread(target=_work, daemon=True).start()
    except Exception:
        pass


def _asof_for_msg(msg_id, st):
    """D86: the session a message belongs to, from its ARRIVAL time. A queued
    message must date from when it was SENT — Monday's 0DTE parsed on Tuesday
    became a Tuesday expiry (TL4399: a Mon signal traded as Tue, -442)."""
    try:
        rec = (st.get("parse_retry") or {}).get(str(msg_id))
        if rec:
            at = rec.get("seen_at") or rec.get("at") or 0
            return us_session_at(time.strftime("%Y-%m-%d %H:%M:%S",
                                               time.localtime(at)))
    except Exception:
        pass
    return None


def _signal_expiry(brain_expiry, text, asof=None):
    """Resolve the expiry a signal is actually for, text-first (the brain
    mislabels 0DTE as next-Friday, and NZ-side date checks mislabel US-valid
    dates as past). Precedence:
      1. text 'for today' / 'for tomorrow' -> the US session it names
      2. text 'for M/D'                    -> that date in the US year, rolled
         to next year ONLY when >1 day past (a January date quoted in December
         wraps; a just-passed 0DTE stays itself so the caller can park it)
      3. the brain's expiry
      4. next Friday after the US session date
    asof anchors every relative date to a PAST session (a retro settle resolves
    'for today' against the parked row's own date, never the wall clock).
    Returns an ISO date string, or None when the text date is unparseable."""
    today = asof or us_today()
    t = (text or "").lower()
    m = re.search(r"\bfor\s+(today|tomorrow)\b", t)
    m_wd = re.search(r"\bfor\s+(monday|tuesday|wednesday|thursday|friday)\b", t)
    if m:
        d = asof if asof is not None else us_session_for_today()
        if m.group(1) == "tomorrow":
            from datetime import timedelta as _td
            d += _td(days=1)
            while d.weekday() >= 5:
                d += _td(days=1)
        return d.isoformat()
    if m_wd:
        from datetime import timedelta as _td
        _idx = ["monday", "tuesday", "wednesday", "thursday", "friday"].index(m_wd.group(1))
        d = today
        if d.weekday() != _idx:
            d += _td(days=1)
        while d.weekday() != _idx:
            d += _td(days=1)
        return d.isoformat()
    m = re.search(r"\bfor\s+(\d{1,2})\s*/\s*(\d{1,2})\b", t)
    if m:
        mo, dy = int(m.group(1)), int(m.group(2))
        try:
            cand = _date(today.year, mo, dy)
        except ValueError:
            return None
        if (cand - today).days < -1:
            # A day or two late (or a re-send days later): a parenthesised
            # weekday is metadata — '11/2 (Monday)' sent after 11/2 means the
            # COMING Monday, not the same date next year.
            _wd = re.search(r"\b(\d{1,2}\s*/\s*\d{1,2})\s*\(\s*"
                            r"(monday|tuesday|wednesday|thursday|friday)\s*\)", t)
            if _wd:
                from datetime import timedelta as _td2
                _i = ["monday", "tuesday", "wednesday", "thursday", "friday"].index(_wd.group(2))
                d2 = today + _td2(days=1)
                while d2.weekday() != _i:
                    d2 += _td2(days=1)
                if (d2 - today).days <= 14:
                    return d2.isoformat()
            # a re-send within a fortnight keeps the REAL date (past = the
            # caller logs it as expired); only a genuinely cyclical date
            # (January quoted in December) rolls a whole year
            if (today - cand).days <= 14:
                return cand.isoformat()
            try:
                cand = _date(today.year + 1, mo, dy)
            except ValueError:
                return None
        return cand.isoformat()
    if brain_expiry:
        s = str(brain_expiry)
        if len(s) == 8 and s.isdigit():
            s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
        try:
            _date.fromisoformat(s)
            return s
        except ValueError:
            pass
    return next_friday(today)


def opt_identifier(underlying, expiry, right, strike):
    exp = str(expiry).replace("-", "")[2:]     # YYMMDD
    return (f"{underlying} {exp}{'C' if right == 'call' else 'P'}"
            f"{int(round(strike * 1000)):08d}")


_Q_CACHE = {}
_PROXY = {"SPXW": ("SPY", 10.0), "SPX": ("SPY", 10.0), "NDX": ("QQQ", 44.0),
          "RUTW": ("IWM", 10.0), "RUT": ("IWM", 10.0)}
_INDEX_ROOTS = ("SPXW", "SPX", "NDX", "RUTW", "RUT")


def _set_leg_strike(leg, strike):
    try:
        leg.strike = float(strike)
    except Exception:
        pass


def _parse_q(j):
    """mid, else last/close — None when unquoted."""
    q = ((j or {}).get("quotes") or {}).get("quote") or {}
    bid, ask = q.get("bid"), q.get("ask")
    if bid and ask:
        return round((float(bid) + float(ask)) / 2, 4)
    for k in ("last", "close"):
        if q.get(k):
            return float(q[k])
    return None


_ROOT_ORDER = ("SPXW", "RUTW", "NDX", "SPX", "RUT", "SPY", "QQQ", "IWM")


def _split_opt_id(opt_id):
    """'SPXW261107C07700000' / 'SPXW 2611...' -> (root, rest) by longest known
    root prefix; (None, None) when unknown."""
    u = (opt_id or "").replace(" ", "").upper()
    for r in _ROOT_ORDER:
        if u.startswith(r):
            return r, u[len(r):]
    return None, None


def _proxy_occ(opt_id):
    """SPXW261107C07700000 -> (SPY261107C00770000, 10.0): strike/divisor snapped."""
    try:
        root, rest = _split_opt_id(opt_id)
        if not root:
            return None, None
        prox, div = _PROXY[root]
        ymd, right, k8 = rest[:6], rest[6], rest[7:]
        k = int(k8) / 1000 / div
        k_new = max(1, int(round(k)))
        return f"{prox}{ymd}{right}{int(round(k_new * 1000)):08d}", div
    except Exception:
        return None, None


def _fetch_quote(env, occ):
    import urllib.request
    from james_exec import TradierConfig
    cfg = TradierConfig.load(env)
    req = urllib.request.Request(
        f"{cfg.base_url}/markets/quotes?symbols={occ.replace(' ', '')}",
        headers={"Authorization": f"Bearer {cfg.access_token}",
                 "Accept": "application/json"})
    return _parse_q(json.loads(urllib.request.urlopen(req, timeout=12).read()))


def opt_quote(opt_id, ttl=5):
    """Real-time-ish option premium. Index roots quote on SANDBOX (prod has no
    SPXW chain); equities on PROD; ETF proxy (SPY/QQQ/IWM, scaled) as fallback.
    Cached per contract for ttl seconds."""
    now_t = time.time()
    hit = _Q_CACHE.get(opt_id)
    if hit and now_t - hit[0] < ttl:
        return hit[1]
    root, _rest = _split_opt_id(opt_id)
    val = None
    envs = ("sandbox",) if root in _INDEX_ROOTS else ("prod", "sandbox")
    for env in envs:
        try:
            val = _fetch_quote(env, opt_id)
        except Exception:
            val = None
        if val:
            break
    if val is None and root in _PROXY:
        pocc, div = _proxy_occ(opt_id)
        if pocc:
            try:
                raw = _fetch_quote("prod", pocc)
                if raw:
                    val = round(raw * div, 2)
            except Exception:
                pass
    if val is None and root in _PROXY:
        pocc, div = _proxy_occ(opt_id)
        if pocc:
            try:
                raw = _fetch_quote("sandbox", pocc)
                if raw:
                    val = round(raw * div, 2)
            except Exception:
                pass
    _Q_CACHE[opt_id] = (now_t, val)
    return val


def opt_mark(t):
    """Live premium for an open option trade: Tradier quote adapter first,
    Tiger briefs only as a fallback (US-OPT permission is denied)."""
    _q = None
    try:
        _q = opt_quote(t.get("opt_id") or "")
    except Exception:
        _q = None
    if _q:
        return _q
    _, _, qc = tiger()
    if qc is None:
        return None
    try:
        df = qc.get_option_briefs([t["opt_id"]])
        if df is None or not len(df):
            return None
        row = df.iloc[0]
        for col in ("last_price", "latest_price", "mid_price", "close", "ask_price", "bid_price"):
            v = row.get(col)
            if v:
                return float(v)
    except Exception as e:
        log(f"opt mark failed {t['opt_id']}: {type(e).__name__}: {e}")
    return None


def spread_credit_mid(under, expiry, legs):
    """Net mid credit of a structure from the chain (sell legs +, buy legs -).
    None if any leg cannot be quoted."""
    tot = 0.0
    for l in legs or []:
        try:
            occ = opt_identifier(under, expiry, l.get("right"),
                                 float(l["strike"])).replace(" ", "")
        except (KeyError, TypeError, ValueError):
            return None
        q = opt_quote(occ)
        if q is None:
            return None
        tot += q if str(l.get("action")) == "sell" else -q
    return round(tot, 2)


def spread_intrinsic(legs, spot):
    """Package intrinsic from spot: |intrinsic(K1) - intrinsic(K2)| for a
    2-leg same-right structure. This is the FLOOR a spread cannot trade below
    at that spot (D147's geometric reference when the chain has no quote —
    dte0 weeklies quote-blind)."""
    if not spot or not legs or len(legs) != 2:
        return None
    try:
        ks, rights = [], set()
        for l in legs:
            k = float(l["strike"])
            r = str(l.get("right") or l.get("type") or "call")[0].lower()
            ks.append(k)
            rights.add(r)
        if len(rights) != 1:
            return None
        r = rights.pop()
        intr = [max(0.0, spot - k) if r == "c" else max(0.0, k - spot)
                for k in ks]
        return round(abs(intr[0] - intr[1]), 2)
    except (KeyError, TypeError, ValueError):
        return None


def spread_mark(t):
    """Current mid debit to close a multi-leg structure; None if any leg fails.
    D54: floor the mark at the structure's INTRINSIC debit — stale chain mids
    must never mask a stop the underlying price has already crossed. WPM's
    140/135 bull puts marked 3.6 on frozen quotes while WPM at 130.7 meant the
    intrinsic debit alone was 5.0 (both legs ITM): the 4.8 stop was breached and
    invisible. The floor is price-truth; the mids may only add time value."""
    tot = 0.0
    for leg in (t.get("legs") or []):
        occ = _occ_key(t, leg)
        _q = opt_quote(occ) if occ else None
        if _q is None:
            return None
        tot += _q if str(leg[2]) == "sell" else -_q
    try:
        S = _under_quote(t.get("instrument") or t.get("symbol"))
    except Exception:
        S = None
    if S and t.get("legs"):
        fl = -_spread_expiry_pnl([tuple(l) for l in t["legs"]], 0, 1, float(S)) / 100.0
        if fl > tot:
            tot = fl
    return round(tot, 2)



# Option underlyings Tiger paper actually accepts (verified 2026-10-27):
# SPXW = SPX weekly index options OK; SPX monthly and NDX/NDXW are rejected
# by the account. None = unsupported -> ledger-only, never a guessed proxy.
OPT_UNDERLYING_MAP = {
    # index option roots Tradier actually supports (verified via /markets/lookup):
    # SPXW (weekly), RUTW, NDX. NDXW does not exist -> use NDX.
    "SPX": "SPXW", "SPXW": "SPXW", "SPX500": "SPXW", "S&P500": "SPXW",
    "S&P": "SPXW", "ES": "SPXW", "SPXW500": "SPXW",
    "RUT": "RUTW", "RUTW": "RUTW", "RUSSELL": "RUTW", "RUSSELL2000": "RUTW",
    "RTY": "RUTW",
    "NDX": "NDX", "NDXW": "NDX", "NDX100": "NDX", "NAS100": "NDX",
    "NASDAQ": "NDX", "USTEC": "NDX",
    "SPY": "SPY", "QQQ": "QQQ",
}


def opt_underlying(raw):
    key = (raw or "").strip().upper()
    if key in OPT_UNDERLYING_MAP:
        return OPT_UNDERLYING_MAP[key]
    return key or None            # single-name equities pass through as-is


def opt_strike_valid(sym, strike):
    """SPXW strikes trade on a 5-point grid; ETFs/equities on 1-point."""
    step = 5 if sym == "SPXW" else 0.5    # equities trade half-point strikes
    return round(strike / step) * step


def opt_tick(sym):
    """Minimum price increment for the option's limit price.
    SPXW quotes in 0.1 (verified: 3.91 rejected, 15.0 accepted); ETFs 0.01."""
    return 0.1 if sym == "SPXW" else 0.01


def opt_price(sym, price, up=True):
    """Snap a limit price onto the tick grid, rounding toward a fill."""
    tick = opt_tick(sym)
    n = price / tick
    n = __import__("math").ceil(n) if up else __import__("math").floor(n)
    return round(n * tick, 2)


def looks_like_spread(text):
    """Heuristic guard: two option legs in one message, or credit collection.
    James writes e.g. 'Sell 106P n Buy 102P n collect $270 for 11/20'."""
    t = (text or "").lower()
    if not t:
        return False
    strikes = re.findall(r"\b\d{2,5}(?:\.\d+)?\s*[cp]\b", t)
    if len(strikes) >= 2:
        return True
    if re.search(r"\b(sell|short)\b[^.\n]{0,40}\b(buy|long)\b", t) and strikes:
        return True
    if re.search(r"\bcollect\b|\bcredit\b|\biron fly\b|\bcondor\b|\bstrangle\b|\bstraddle\b", t):
        return True
    return False


def text_single_opt(text, under):
    """'EOG 146P at 2.75' -> (146.0, 'put') for that ticker. The brain drops
    leading digits (146 became 36 and 15 became 36 — both rejected), so the
    written strike must beat the card. Ticker-anchored so levels never match."""
    if not text or not under:
        return None
    m = re.search(r"\b" + re.escape(str(under)) + r"\s+(\d{1,6}(?:\.\d+)?)\s*([CP])\b",
                  text, re.I)
    if not m:
        return None
    try:
        k = float(m.group(1))
    except ValueError:
        return None
    if k <= 0:
        return None
    return k, ("call" if m.group(2).lower() == "c" else "put")


def text_option_card(text):
    """A single written contract ('buy IONQ 29C at 0.8') -> dict, else None.
    The brain sometimes returns neither an option object nor a ticker, and the
    message then fell through to the equity path and logged 'no symbol for '.
    Only fires when the text carries ONE leg (spreads route elsewhere)."""
    if len(text_struct(text)[0]) >= 2:
        return None
    m = re.search(r"\b([A-Z]{2,5})\s+(\d{2,6}(?:\.\d+)?)\s*([CP])\b", text or "")
    if not m or m.group(1) in _TICK_STOP:
        return None
    pm = re.search(r"\bat\s*\$?(\d+(?:\.\d+)?)", text or "")
    return {"underlying": m.group(1).upper(), "strike": float(m.group(2)),
            "right": "call" if m.group(3) == "C" else "put",
            "expiry": _signal_expiry(None, text),
            "entry": float(pm.group(1)) if pm else None,
            "matched": m.group(0)}

def _sync_fill_qty(t):
    """D131: the FILL is the broker's truth for SIZE, not just price. The
    9-lot orphans (TL4411/TL4403) were born because the book said 1 while
    orders 41785228/41785313 were already resting at 10: the fill then
    booked 1, the exit closed 1, and 9 lots rode unmanaged. Read the order's
    filled quantity and correct the book at the source. Advisory only —
    a failed fetch must never block a fill booking."""
    try:
        oid = t.get("order_id")
        if not oid:
            return
        q = _TRADIER().order_quantity(oid)
        if not q:
            return
        legs = t.get("legs") or []
        filled = int(round(q / len(legs))) if len(legs) > 1 else int(q)
        if filled and filled != (t.get("qty") or 1):
            old = t.get("qty") or 1
            t["qty"] = filled
            t["fill_note"] = (t.get("fill_note") or "") + \
                f" | qty synced to broker fill {filled} (was {old}, D131)"
            log(f"D131 qty sync {t.get('id')}: book {old} -> broker fill {filled} "
                f"(order {oid}, raw qty {q})")
            ledger_row({**t, "event": "ALERT",
                        "reason": f"D131 qty sync: book said {old}, broker "
                                  f"filled {filled} (order {oid})"})
    except Exception as e:
        log(f"D131 qty sync skipped {t.get('id')}: {type(e).__name__}: {e}")


def rebase_levels(t, fill):
    """The stop/targets are built from the CARD premium; when the real fill lands
    at a different premium they must move with it (KTOS card said 1.28, filled
    3.90, leaving a stop 68% away; EOG's stop sat above its fill).

    A fill far from the card means the card is misattributed or the credit was
    mis-read, both of which poison every level after it: flag it so the entry
    gate can reject the position rather than carry a mis-anchored trade."""
    try:
        fill = float(fill)
    except (TypeError, ValueError):
        return
    _sync_fill_qty(t)          # D131: size truth comes from the broker too
    orig = t.get("entry")
    if not orig:
        t["entry"] = fill
        return
    try:
        orig = float(orig)
    except (TypeError, ValueError):
        t["entry"] = fill
        return
    if orig > 0 and abs(fill - orig) / orig > 0.25:
        log(f"{t.get('id')}: fill {fill:.2f} is far from card {orig:.2f} "
            f"(possible misattributed card) — levels rebased")
        t["card_entry_far"] = True
        t["card_credit_gap_pct"] = round(abs(fill - orig) / orig * 100, 1)
    if orig > 0 and fill != orig:
        f = fill / orig
        if t.get("stop"):
            t["stop"] = round(float(t["stop"]) * f, 4)
        if t.get("targets"):
            t["targets"] = [round(float(x) * f, 4) for x in t["targets"]]
    t["entry"] = fill


def distrust_qty(card, text, msg_id=None):
    """Drop a card qty that belongs to a different message: the card's symbol is
    not the one the text trades. Keeps the qty when the text states a size
    ('TO OPEN 5', '2C')."""
    if not isinstance(card, dict):
        return card
    _co = (card.get("option") or {}).get("underlying") or card.get("instrument")
    _mct = re.search(r"\b([A-Z]{2,5})\b\s+\d", text or "")
    _ct = scope_instrument(text) or (_mct.group(1) if _mct else None)
    if _co and _ct and str(_co).upper() != str(_ct).upper()             and not re.search(r"TO OPEN\s+\d+|\d+\s*C", text or "", re.I):
        if msg_id:
            log(f"msg {msg_id}: card says {_co}, text trades {_ct} — qty "
                f"{card.get('qty')} distrusted")
        card["qty"] = None
    return card


def open_option_trade(card, msg_id, st, text=None):
    """Option-contract signal -> Tiger paper BUY TO OPEN at James's premium."""
    cfg, trade, qc = tiger()
    o = dict(card["option"])
    raw_under = o["underlying"]
    # CROSSED CARD (KTOS 4007 got DBX's card): a ticker written against a strike
    # in the text wins when the card's own symbol is not in the text at all
    _mta = re.search(r"\b([A-Z]{2,5})\s+(\d{2,6}(?:\.\d+)?)\s*([CP])\b",
                     text or "")
    if _mta and str(raw_under).upper() != _mta.group(1).upper()             and not re.search(r"\b" + re.escape(str(raw_under)) + r"\b",
                              text or "", re.I):
        log(f"opt msg {msg_id}: card says {raw_under}, text writes {_mta.group(1)} "
            f"{_mta.group(2)}{_mta.group(3)} — card symbol overridden")
        raw_under = _mta.group(1).upper()
        o["underlying"] = raw_under
        o["strike"] = float(_mta.group(2))
        o["right"] = "call" if _mta.group(3).upper() == "C" else "put"
    # symbol hygiene (TSLA 337.5P-class 'Undefined symbol'): prefer a known
    # symbol token over a garbage brain label
    _caps1 = {w for w in re.findall(r"\b[A-Z]{2,5}\b", text or "")
              if w not in _TICK_STOP}
    _tok1 = (scope_instrument(text)
             or next((c for c in sorted(_caps1)
                      if c in OPT_UNDERLYING_MAP or c in _UND_QUOTE), None)
             or (next(iter(_caps1)) if len(_caps1) == 1 else None))
    _fix1, _ = repair_symbol(_tok1 or raw_under, text, card)
    if _fix1 and raw_under and _fix1 != str(raw_under).upper().strip():
        _log_symfix(msg_id, raw_under, _fix1)
    underlying = opt_underlying(_fix1 or raw_under)
    right = o["right"]
    trade_id = f"O{int(time.time())}"
    text = text if text is not None else (card.get("_raw_text") or card.get("rationale") or "")
    if looks_like_spread(text):
        # the brain emitted ONE leg of a multi-leg order (msg 3667: 'Sell QQQ
        # 697P n Buy 695P n collect $91' as a single-leg card). If the TEXT
        # itself parses to >=2 resolved legs, execute it as a multileg; only
        # text we cannot resolve parks below.
        tl, tflags, tcredit = text_struct(text)
        if len(tl) >= 2 and not any(l["right"] is None for l in tl) \
                and not is_explicit_close(text):
            card = dict(card)
            card["spread"] = {"type": "credit", "legs": tl, "source": "text"}
            if tcredit:
                card["entry"] = tcredit
            card["qty"] = distrust_qty(card, text, msg_id)
            log(f"opt: msg {msg_id} single-leg card but text has {len(tl)} legs "
                f"— handing off to multileg")
            open_multileg_tradier(card, text, msg_id, st)
            return
        # only truly multi-strike text is refused here: 'Sell QQQ 711P n
        # collect $116' is ONE leg (a naked short) and must fall through
        if len(text_strikes(text)) >= 2 or len(tl) >= 2:
            ledger_row({"event": "SPREAD", "trade_id": trade_id, "msg_id": msg_id,
                        "instrument": card.get("instrument"),
                        "direction": card["direction"], "entry": card["entry"],
                        "reason": "spread language detected — single leg refused",
                        "raw": (text or "")[:120]})
            log(f"opt: spread language in msg {msg_id} — refusing single-leg order")
            push(f"🧩 SPREAD DETECTED (msg {msg_id}) — not traded\n"
                 f"{card.get('instrument')} — message describes multiple option legs.\n"
                 f"\"{(text or '')[:90]}\"", st)
            save_state(st)
            return
    if underlying is None:
        # index options Tiger paper cannot trade (NDX/NDXW) — park, don't proxy
        label = f"{raw_under} {o['strike']:g}{right[0].upper()}"
        park_signal({**card, "instrument": raw_under,
                     "spread": {"type": "single_option", "legs": [
                         {"action": "buy", "strike": o["strike"], "right": right}],
                         "net": card["entry"]}},
                    msg_id, card.get("rationale") or "",
                    f"no venue support for {raw_under} options (Tiger paper rejects {raw_under})")
        log(f"opt: {raw_under} options unsupported on Tiger paper — PARKED")
        save_state(st)
        return
    expiry = _signal_expiry(o.get("expiry"), text, asof=_asof_for_msg(msg_id, st))
    if len(str(expiry)) == 8:
        expiry = f"{str(expiry)[:4]}-{str(expiry)[4:6]}-{str(expiry)[6:]}"
    # reject expiries that are already gone; a signal can arrive after the close.
    # Compare against the US session date, not the NZ clock (msg 3669 'for 10/30'
    # arrived US morning and was rejected by an NZ date already on 10/31).
    try:
        exp_d = _date.fromisoformat(str(expiry))
    except ValueError:
        exp_d = None
    if exp_d is None or exp_d < us_today():
        label = f"{underlying} {o['strike']:g}{right[0].upper()} {expiry}"
        ledger_row({"event": "LOGGED", "trade_id": trade_id, "msg_id": msg_id,
                    "instrument": label, "direction": card["direction"],
                    "entry": card["entry"],
                    "reason": f"expiry {expiry} already past at signal time",
                    "raw": (card.get("rationale") or "")[:80]})
        log(f"opt: expiry {expiry} already past — ledger-only")
        save_state(st)
        return
    _ts = text_single_opt(text, underlying)
    if _ts:
        if abs(_ts[0] - float(o.get("strike") or 0)) > 1e-6:
            log(f"opt msg {msg_id}: text strike wins {o.get('strike')} -> {_ts[0]:g}")
            o["strike"] = _ts[0]
        right = _ts[1]
    strike = opt_strike_valid(underlying, o["strike"])
    opt_id = opt_identifier(underlying, expiry, right, strike)
    label = f"{underlying} {strike:g}{right[0].upper()} {expiry[5:]}"

    if any(t.get("opt_id") == opt_id for t in st.get("open", {}).values()):
        log(f"opt: duplicate {opt_id} already in book — skipped")
        ledger_row({"event": "SKIP", "trade_id": trade_id, "msg_id": msg_id,
                    "instrument": label, "direction": card["direction"],
                    "reason": f"option already open: {opt_id}"})
        save_state(st)
        return
    if trade is None:
        ledger_row({"event": "LOGGED", "trade_id": trade_id, "msg_id": msg_id,
                    "instrument": label, "direction": card["direction"],
                    "reason": "no tiger — option ledger-only",
                    "raw": (card.get("rationale") or "")[:80]})
        save_state(st)
        return

    entry = card["entry"]
    if not entry:
        qc_mark = None
        try:
            df = qc.get_option_briefs([opt_id])
            if df is not None and len(df):
                row = df.iloc[0]
                qc_mark = row.get("last_price") or row.get("ask_price") or row.get("close")
        except Exception:
            pass
        entry = float(qc_mark) if qc_mark else None
    _tp = text_credit(text)          # text premium beats a mis-scaled brain value
    if _tp:
        entry = _tp
    if not entry:
        # no premium quoted and no quote permission: cannot size honestly
        ledger_row({"event": "LOGGED", "trade_id": trade_id, "msg_id": msg_id,
                    "instrument": label, "direction": card["direction"],
                    "reason": "no premium stated and no option quote permission",
                    "raw": (card.get("rationale") or "")[:80]})
        log(f"opt: {opt_id} no premium and no quote — ledger-only")
        save_state(st)
        return

    # qty: screenshot qty wins when present (resolve_qty clamps it); without
    # one, fall back to the notional-based default (no ambiguity -> no change).
    qty = resolve_qty(card.get("qty")) if card.get("qty") else \
        max(1, min(int(CFG.get("max_qty", 10)), int(CFG["notional_usd"] / (entry * 100))))
    is_lotto = _is_lotto(text)
    _short = text_is_naked_short(text)     # sell-to-open single (naked put)
    if is_lotto:
        qty = 1
    _mc = re.search(r"\b(\d+)\s*C\b", text or "")
    _tq = re.search(r"TO OPEN\s+(\d+)", text or "", re.I)
    if _tq:
        qty = max(1, min(int(CFG.get("max_qty", 10)), int(_tq.group(1))))
    if re.search(r"\bsmall size\b", text or "", re.I):
        qty = min(qty, 2)
    if _mc and int(_mc.group(1)) != int(round(strike)):
        # '2C' is a contract count — but '62C' is the strike itself
        qty = max(1, min(int(CFG.get("max_qty", 10)), int(_mc.group(1))))          # a lotto is a 1-contract flyer by definition
    # RISK CAP: the screenshot qty is not a risk decision (KTOS 7 x 3.90 was
    # $2,730 at risk next to DBX 2 x 1.35). Cap the premium at a fixed dollar
    # amount; a lotto is already 1.
    if not is_lotto:
        _est = None
        try:
            _est = opt_quote(opt_identifier(underlying, expiry, right, strike))
        except Exception:
            _est = None
        _px_risk = max([x for x in (_est, entry) if x] or [0])
        _risk_cap = float(CFG.get("single_max_risk_usd", 300.0))
        if _px_risk and _px_risk > 0:
            qty = max(1, min(qty, int(_risk_cap / (_px_risk * 100))))
    stop = card["stop"]
    if stop and entry and abs(stop - entry) <= 0.001 * entry:
        stop = None
    if stop is None and entry:
        pct = float(CFG.get("single_stop_pct", 40.0)) / 100
        stop = round(entry * (1 + pct), 2) if _short else round(entry * (1 - pct), 2)
    targets = list(card["targets"])
    if not targets and stop and entry and not _short:
        targets = [round(entry + 2 * (entry - stop), 2)]        # house 2R
    t = {"id": trade_id, "msg_id": msg_id, "instrument": label,
         "symbol": underlying, "direction": ("short" if _short else "long"), "entry": entry,
         "stop": stop, "targets": targets,
         "confidence": card["confidence"], "horizon": card["horizon"],
         "rationale": card["rationale"], "opened": now(),
         "status": "pending", "executor": "tradier", "k": 1.0,
         "order_id": None, "qty": qty, "opt_id": opt_id,
         "opt_underlying": underlying, "opt_expiry": expiry, "expiry": expiry,
         "exit_price": None, "exit_ts": None, "pnl": None, "fill_note": ""}
    _sq1 = _under_quote(underlying)
    _itm1 = False
    if _sq1:
        _itm1 = (strike > float(_sq1)) if right.startswith("p") else (strike < float(_sq1))
    try:
        _dte1 = (_date.fromisoformat(str(expiry)) - us_today()).days
    except (ValueError, TypeError):
        _dte1 = 0
    t["tier"] = tier_of(defined=bool(not _short), legs_n=1, short=_short,
                        lotto=is_lotto, quality=None, dte=_dte1, itm=_itm1)
    # NAKED PUT = OBSERVATIONAL (user 2026-11-04): tagged, and controlled by a
    # circuit breaker — too many naked losses today stops further naked entries.
    if _short:
        t["naked"] = True
        lost = [c for c in st.get("closed", {}).values()
                if c.get("naked") and (c.get("exit_ts") or "").startswith(today())]
        lsum = sum(c.get("pnl") or 0 for c in lost)
        streak = 0
        for c in sorted(lost, key=lambda x: x.get("exit_ts") or "", reverse=True):
            if (c.get("pnl") or 0) < 0:
                streak += 1
            else:
                break
        if (lsum <= -abs(float(CFG.get("naked_breaker_usd", 300)))
                or streak >= int(CFG.get("naked_loss_streak", 3))):
            ledger_row({**card, "event": "BREAKER", "msg_id": msg_id,
                        "instrument": label, "direction": "short",
                        "reason": f"naked breaker: today {lsum:+.2f}, streak {streak}",
                        "raw": (text or "")[:80]})
            push("🛑 NAKED BREAKER (msg %s): today's naked PnL %+.2f, loss streak %d — "
                 "not placing. Existing positions unaffected." % (msg_id, lsum, streak), st)
            park_signal(card, msg_id, text, f"naked circuit breaker ({lsum:+.2f}/{streak})")
            save_state(st)
            return
    try:
        from tigeropen.common.util.contract_utils import option_contract_by_symbol
        # TRADIER single-leg option (real-time data, free). The multileg path
        # already runs here; single brackets route to the same venue.
        from james_core import Record as _R, MsgClass as _M, Action as _A, Leg as _L
        _ok_l2, _ch2, _n2 = _listed(underlying, expiry, right, strike)
        if _ch2 and not _ok_l2:
            # a SCALE miss (split-adjusted contract, 146P on a 35 stock) is not
            # a 2% snap — if the nearest listed strike is 4x away in either
            # direction, take it and log the scale correction
            _ratio = (max(_n2, strike) / min(_n2, strike)) if _n2 else 0
            if _n2 and (abs(_n2 - strike) / strike <= 0.02 or _ratio >= 4):
                log(f"msg {msg_id}: strike snapped {strike:g}->{_n2:g}"
                    + (" (scale adjustment)" if _ratio >= 4 else ""))
                strike = _n2
            else:
                park_signal(card, msg_id, text,
                            f"strike/expiry not listed: {underlying} {strike:g}{right[0].upper()} {expiry}")
                save_state(st)
                return
        # the OCC symbol and label were built BEFORE any snap — rebuild them
        opt_id = opt_identifier(underlying, expiry, right, strike)
        label = f"{underlying} {strike:g}{right[0].upper()} {expiry[5:]}"
        t["instrument"], t["opt_id"] = label, opt_id
        # long singles are allowed to sit far OTM (29C on a 45 stock is a cheap
        # flyer); only a SHORT premium leg changes character when it is far
        if (CFG.get("strike_far_guard", True) and not is_lotto
                and text_is_naked_short(text)):
            _sq = _under_quote(underlying)
            if not _sq:
                log(f"msg {msg_id}: far-strike guard skipped — no spot for {underlying}")
            _far, _fpct = strike_far(_sq, strike, right)
            if _far:
                park_signal(card, msg_id, text,
                            f"strike {strike:g} is {_fpct:.1f}% from spot "
                            f"{float(_sq):,.2f} (>{CFG.get('strike_far_pct')}%) "
                            f"— not the option the message reads as")
                save_state(st)
                return
        _leg = _L(strike=strike, right=right, action="buy", qty=qty,
                  underlying=underlying, expiry=expiry)
        # D139: single-option entries carry a buffered LIMIT (signal ±15%) in
        # credit — the fill engine can no longer pay far above the call (MRNA
        # filled +29% over signal on a market order) or accept far below it
        # (WMT -48%); an unfilled limit rests and the EXISTING recheck/6h-
        # cancel machinery owns it exactly as multileg entries already do
        # (their credit/debit types were always limit-at-signal). None if the
        # signal price is unknown — market, as before.
        try:
            _e_f = float(entry) if entry else 0.0
        except (TypeError, ValueError):
            _e_f = 0.0
        _lim_e = (round(_e_f * (0.85 if _short else 1.15), 2)
                  if _e_f > 0 else None)
        _rec = _R(msg_id=msg_id, ts=now(), cls=_M.OPTION_BRACKET, action=_A.ENTRY,
                  raw="", underlying=underlying, legs=[_leg], credit=_lim_e,
                  qty=qty, expiry=expiry,
                  direction=("short" if _short else "long"),
                  stop=stop, targets=list(card.get("targets") or []))
        # a future-session entry ('for Monday when market opens') must outlive
        # today's DAY order: GTC until that session
        try:
            if exp_d > us_session_for_today():
                _rec.duration = "gtc"
                _rec.close_date = str(expiry)
        except TypeError:
            pass
        ex = _TRADIER().place(_rec)
        if ex.ok:
            t["order_id"], t["status"] = ex.order_id, "open_tradier"
            t["fill_note"] = ex.reason
            log(f"opt TRADIER {qty}x {opt_id} order {ex.order_id}")
            # FILL VERIFICATION: acceptance is not a fill (AEHR was rejected and
            # the book still showed it open for hours)
            time.sleep(3)
            st_o, fill = _tradier_close_status(ex.order_id)
            su = (st_o or "").upper()
            if "FILL" in su:
                _sig_e = t.get("entry")      # D139: signal, before rebase overwrites
                rebase_levels(t, fill)
                t["fill_note"] = ex.reason + (f" | filled @ {fill}" if fill else "")
                _warn_entry_fill(t, fill, _sig_e, st)
            elif any(x in su for x in ("REJECT", "CANCEL", "EXPIRED", "FAILED")):
                t["status"], t["pnl"] = "closed", 0.0
                t["fill_note"] = f"entry order {su} — no position"
                log(f"opt entry terminal at placement: {su}")
            else:
                t["status"] = "pending"      # resting: manage polls/cancels
        else:
            t["status"], t["fill_note"] = "order_failed", ex.reason
            log(f"opt Tradier failed: {ex.reason}")
    except Exception as e:
        msg = str(e)
        if "tick size" in msg.lower():
            # snap to the grid and retry once
            try:
                lmt = opt_price(underlying, entry * 1.03, up=True)
                order = limit_order(account=cfg.account, contract=contract,
                                    action="BUY", quantity=qty, limit_price=lmt)
                oid = trade.place_order(order)
                t["order_id"], t["status"] = oid, "pending"
                log(f"opt BUY retry {qty}x {opt_id} LMT {lmt} order {oid}")
                time.sleep(3)
                check_fill(t)
            except Exception as e2:
                t["status"] = "order_failed"
                t["fill_note"] = f"tick retry failed: {str(e2)[:80]}"
                log(f"opt tick retry failed: {str(e2)[:120]}")
        elif "合约" in msg or "contract" in msg.lower():
            # contract genuinely absent from the chain — never retry
            t["status"], t["fill_note"] = "unsupported", f"contract rejected: {opt_id}"
            log(f"opt contract not tradeable: {opt_id} — {msg[:90]}")
        else:
            t["status"], t["fill_note"] = "order_failed", f"{type(e).__name__}: {msg[:90]}"
            log(f"opt order failed: {msg[:120]}")
    if is_lotto:
        t["fill_note"] = "LOTTO (qty 1) " + (t.get("fill_note") or "")
    if _short:
        t["fill_note"] = "🫥 NAKED PUT (observational) " + (t.get("fill_note") or "")
    if t["status"] == "closed":
        st.setdefault("closed", {})[trade_id] = t
    else:
        st.setdefault("open", {})[trade_id] = t
        # D128: single-option trades never started a lifecycle — narratives
        # about them (MRNA/FUTU chains) had nothing to link to
        if t.get("order_id"):
            _lc_start(trade_id, msg_id, underlying or label,
                      f"{t['direction']} {qty}x {opt_id} @ {t.get('entry')} "
                      f"[{t['status']}] {(t.get('fill_note') or '')[:40]}",
                      (text or "")[:120])
    ledger_row({**t, "event": "ORDER" if t["order_id"] else "LOGGED",
                "targets": "|".join(f"{x:.2f}" for x in t["targets"]),
                "reason": t["fill_note"] or "ok"})
    push(trade_card_text(t), st)
    if t["status"] in ("order_failed", "unsupported"):
        # never let a rejected option order pass silently
        ledger_row({**t, "event": "ALERT", "reason": t["fill_note"]})
        log(f"opt alert pushed for {trade_id} ({t['status']})")
    save_state(st)


def _order_fill(trade, oid):
    """(status, avg_fill_price) for an order id; (None, None) on failure."""
    try:
        o = trade.get_order(id=oid)
        return str(getattr(o, "status", "")), (
            float(getattr(o, "avg_fill_price", None) or 0) or None)
    except Exception:
        return None, None


def _check_exit_diverge(t, fill, tag, st):
    """D129: an exit fill that diverges >30% from the mark the decision was
    made on means our quote feed and the fill disagree (seen live: FUTU mark
    2.44 → fill 1.42; MRNA exit filled 1.41 while James reported +80-100%).
    Alert only — never blocks or re-opens a close (tightening must not miss)."""
    try:
        mk = t.pop("_exit_mark", None)
        if not (mk and fill):
            return
        mk, fill = float(mk), float(fill)
        if mk <= 0 or fill <= 0:
            return
        dev = abs(fill - mk) / mk
        if dev > 0.30:
            msg = (f"⚠️ EXIT FILL DIVERGED {t.get('instrument')} {t.get('id')}: "
                   f"decision mark {mk:.2f} → fill {fill:.2f} ({dev:.0%}) at {tag} "
                   f"— quote feed and fill disagree (D129)")
            log(msg)
            push(msg, st)
    except Exception:
        pass


def book_option_close(t, exit_px, qty_close, reason, st):
    """Book an option close at the REAL fill price (never the limit guess)."""
    _full = qty_close >= (t.get("qty") or 1)
    if t.get("entry") is None:
        t["pnl"] = None
    else:
        t["pnl"] = round((exit_px - t["entry"]) * 100 * qty_close, 2)
    if _full:
        t["exit_price"], t["exit_ts"], t["status"] = exit_px, now(), "closed"
        t["fill_note"] = reason
        st.setdefault("closed", {})[t["id"]] = t
        st.get("open", {}).pop(t["id"], None)
    else:
        t["qty"] = (t["qty"] or 1) - qty_close
        t["fill_note"] = f"trimmed {qty_close} @ {exit_px:.2f} — {reason}"
    t.pop("close_order_id", None)
    t.pop("close_qty", None)
    # D128: the Tiger option close path never wrote EXIT/TRIM rows
    _lc_finish(t, "EXIT" if _full else "TRIM",
               f"{t['fill_note']} | PnL {t.get('pnl')}")
    # D129: fill vs the mark the exit decision was made on
    _check_exit_diverge(t, exit_px, "option close", st)
    ledger_row({**t, "event": "EXIT", "reason": f"{reason} @ fill {exit_px:.2f}"})
    arrow = "🎯" if t["pnl"] and t["pnl"] > 0 else "🚪"
    entry_s = f"{t['entry']:.2f}" if t.get("entry") is not None else "?"
    pnl_s = f"${t['pnl']:+.2f}" if t.get("pnl") is not None else "n/a (entry unparsed)"
    push(f"{arrow} {'TRIMMED' if t['status'] != 'closed' else 'CLOSED'} "
         f"{t['instrument']} [OPT] — {reason}\n"
         f"entry {entry_s} → fill {exit_px:.2f} | PnL {pnl_s} "
         f"(paper, {qty_close} contract(s))", st)
    save_state(st)


def _tradier_close_status(oid):
    try:
        return _TRADIER().order_fill(oid)
    except Exception:
        return None, None


def book_tradier_close(t, fill_px, st):
    """Book a Tradier close at the REAL fill (abs net). Short strips pay a debit
    to close, long strips receive a credit — direction carries the PnL sign.
    A close_qty below the position size books a TRIM and keeps the rest open."""
    stock = t.get("executor") == "tradier_stock"
    mult = 1 if t.get("direction") == "long" else -1
    per = 1 if stock else 100
    open_status = "open_tradier_stock" if stock else "open_tradier"
    qc = t.pop("close_qty", None)
    part = qc is not None and qc < (t.get("qty") or 1)
    size = qc if qc is not None else (t.get("qty") or 1)
    if t.get("entry") is None:
        t["pnl"] = None          # credit never parsed: PnL not computable
    else:
        t["pnl"] = round(mult * (fill_px - t["entry"]) * per * size, 2)
    t["exit_price"], t["exit_ts"] = fill_px, now()
    t.pop("close_order_id", None)
    if part:
        t["qty"] = (t.get("qty") or 1) - size
        t["status"] = open_status
        t["fill_note"] = (f"trimmed {size} of {size + t['qty']} @ {fill_px:.2f} — "
                          f"{t.pop('close_reason', None) or 'close'}; {t['qty']} remain")
        ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
        # D128: async Tradier closes never wrote lifecycle rows
        _lc_finish(t, "TRIM", t["fill_note"])
        # D129: fill vs the mark the exit decision was made on
        _check_exit_diverge(t, fill_px, "tradier trim", st)
        push(f"✂️ TRIMMED {t['instrument']} by {size} @ {fill_px:.2f} — "
             f"{t['qty']} remain (PnL {t['pnl']})", st)
        save_state(st)
        return
    t["status"] = "closed"
    t["fill_note"] = f"{t.pop('close_reason', None) or 'close'} @ fill {fill_px:.2f}"
    st.setdefault("closed", {})[t["id"]] = t
    st.get("open", {}).pop(t["id"], None)
    ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
    # D128: the async Tradier close booked no EXIT row (TL4415/TL4455/MRNA/FUTU
    # closes all landed here) — closes could never appear in a lifecycle story
    _lc_finish(t, "EXIT", f"{t['fill_note']} | PnL {t.get('pnl')}")
    # D129: fill vs the mark the exit decision was made on
    _check_exit_diverge(t, fill_px, "tradier close", st)
    arrow = "🎯" if t["pnl"] and t["pnl"] > 0 else "🚪"
    entry_s = f"{t['entry']:.2f}" if t.get("entry") is not None else "?"
    pnl_s = f"${t['pnl']:+.2f}" if t.get("pnl") is not None else "n/a (entry unparsed)"
    push(f"{arrow} CLOSED {t['instrument']} {t['direction']} @ fill {fill_px:.2f}\n"
         f"{t['fill_note']} | entry {entry_s} | PnL {pnl_s} (Tradier fill, paper)", st)
    save_state(st)


def check_tradier_close(t, st):
    """Reconcile a resting Tradier close: book on fill, revert on cancel/reject."""
    st_close, fill = _tradier_close_status(t.get("close_order_id"))
    if st_close is None:
        return
    s = st_close.upper()
    if "FILLED" in s and fill is not None:
        book_tradier_close(t, fill, st)
    elif any(x in s for x in ("CANCEL", "REJECT", "EXPIRED", "FAILED")):
        t.pop("close_order_id", None)
        t["status"] = ("open_tradier_stock" if t.get("executor") == "tradier_stock"
                       else "open_tradier")
        t["fill_note"] = f"close order {st_close} — still open, will retry"
        ledger_row({**t, "event": "ALERT", "reason": t["fill_note"]})
        log(f"tradier close {st_close} {t['id']} — back to open")
        push(f"⚠️ CLOSE {st_close} {t['instrument']} {t['id']} — position stays "
             f"open; retrying on next exit trigger", st)
        save_state(st)


def close_option(t, exit_premium, qty_close, reason, st):
    """SELL TO CLOSE (LMT at mark -3%) all or part of an option position.
    If the ENTRY order never filled, cancel it instead of sending a naked SELL
    (a SELL without a fill would open a short we never intended)."""
    cfg, trade, _ = tiger()
    qty_close = max(1, min(qty_close, t["qty"] or 1))
    if trade is None:
        return
    if t.get("status") != "open_opt":
        # entry never filled — cancel the resting BUY, book zero, never sell
        if t.get("order_id"):
            try:
                trade.cancel_order(id=t["order_id"])
                log(f"opt entry unfilled, cancelled BUY {t['order_id']} ({t['opt_id']})")
            except Exception as e:
                log(f"opt cancel failed {t['order_id']}: {e}")
        t["pnl"], t["status"] = 0.0, "closed"
        t["exit_price"], t["exit_ts"] = t.get("entry"), now()
        t["fill_note"] = f"{reason} (entry never filled, order cancelled, PnL 0)"
        st.setdefault("closed", {})[t["id"]] = t
        st.get("open", {}).pop(t["id"], None)
        ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
        _lc_finish(t, "EXIT", t["fill_note"])   # D128
        log(f"opt {t['id']} closed with no fill (PnL 0)")
        save_state(st)
        return
    try:
        from tigeropen.common.util.contract_utils import option_contract_by_symbol
        from tigeropen.common.util.order_utils import limit_order
        o = card_strike_right_from_id(t["opt_id"])
        contract = option_contract_by_symbol(t["opt_underlying"],
                                             t["opt_expiry"].replace("-", ""),
                                             o["strike"], o["right"], "USD")
        order = limit_order(account=cfg.account, contract=contract, action="SELL",
                            quantity=qty_close,
                            limit_price=max(opt_tick(t["opt_underlying"]),
                                            opt_price(t["opt_underlying"],
                                                      exit_premium * 0.97, up=False)))
        oid = trade.place_order(order)
        log(f"opt SELL close {qty_close}/{t['qty']}x {t['opt_id']} LMT "
            f"{round(exit_premium * 0.97, 2)} order {oid} ({reason})")
    except Exception as e:
        log(f"opt close FAILED {t['opt_id']}: {e} — stays open")
        return
    # book only on a REAL fill; otherwise hold as pending_close and reconcile
    t.update(close_order_id=oid, close_qty=qty_close,
             close_reason=reason, close_premium=exit_premium)
    time.sleep(2)
    status, fill = _order_fill(trade, oid)
    if fill and status and "FILLED" in status:
        book_option_close(t, fill, qty_close, reason, st)
        return
    t["status"] = "pending_close"
    ledger_row({**t, "event": "ORDER", "reason":
                f"close resting {oid} @ {round(exit_premium * 0.97, 2)} "
                f"(order {status or 'unknown'})"})
    log(f"opt close rests {oid} status={status} — will reconcile")
    push(f"⏳ CLOSE RESTING {t['instrument']} [OPT] — {reason}\n"
         f"limit {round(exit_premium * 0.97, 2)} | order {oid} ({status})\n"
         f"PnL books at the real fill, not the limit.", st)
    save_state(st)


def card_strike_right_from_id(opt_id):
    """'SPY 261030P00763000' -> {'strike': 763.0, 'right': 'PUT'}"""
    sym, rest = opt_id.split(" ", 1)
    right = "PUT" if "P" in rest[6] else "CALL"
    strike = int(rest[7:]) / 1000
    return {"strike": strike, "right": right}


# ---------------------------------------------------------------- tiger (lazy)

_tiger = None


def tiger():
    """(config, trade_client, quote_client) once, or (None, None, None)."""
    global _tiger
    if _tiger is not None:
        return _tiger
    try:
        from tigeropen.tiger_open_config import TigerOpenClientConfig
        from tigeropen.trade.trade_client import TradeClient
        from tigeropen.quote.quote_client import QuoteClient
        props = Path(CFG["tiger_props"])
        if not props.exists():
            log(f"tiger props not found: {props} — trades will be ledger-simulated")
            _tiger = (None, None, None)
            return _tiger
        cfg = TigerOpenClientConfig(props_path=str(props))
        trade = TradeClient(cfg)
        quote = QuoteClient(cfg)
        _tiger = (cfg, trade, quote)
        log(f"tiger ready: account {cfg.account} license {getattr(cfg, 'license', '?')}")
    except Exception as e:
        log(f"tiger init failed: {type(e).__name__}: {e} — ledger-simulated mode")
        _tiger = (None, None, None)
    return _tiger


def get_quote(sym):
    """Last price via Tiger quotes (real-time, then delayed); None on failure.
    D100 (2026-11-18): INDEX ROOTS must never reach Tiger. `get_stock_briefs`
    on SPXW/NDX/RUT returns ApiException code=4 (quote_delay rate limit) or
    code=1010 (biz param error) because those are NOT stocks. The index quote
    belongs to BlackBull/Tradier; falling through to Tiger only spams the
    rate-limiter with requests that can never succeed."""
    try:
        import ticker_taxonomy as _tx
        if _tx.bucket_of(sym) in ("index", "junk", "contract"):
            return None
    except Exception:
        pass
    _, _, qc = tiger()
    if qc is None:
        return None
    try:
        briefs = qc.get_stock_briefs([sym])
        for b in briefs or []:
            last = getattr(b, "last_price", None) or getattr(b, "latestPrice", None)
            if last:
                return float(last)
    except Exception:
        pass                                    # fall through to delayed quotes
    try:
        df = qc.get_stock_delay_briefs([sym])
        if df is not None and len(df):
            row = df.iloc[0]
            last = row.get("last") or row.get("close")
            if last:
                return float(last)
    except Exception as e:
        log(f"quote failed {sym}: {type(e).__name__}: {e}")
    return None


# ---------------------------------------------------------------- ledger / push

SIG_COLS = ["ts", "event", "trade_id", "msg_id", "instrument", "symbol", "direction",
            "entry", "stop", "targets", "qty", "order_id", "confidence", "horizon",
            "status", "reason", "raw"]
PARK_COLS = ["ts", "event", "msg_id", "structure", "underlying", "body", "wing_low",
             "wing_high", "width", "legs", "qty", "credit", "prob", "profit",
             "maxloss", "reason", "missing", "raw"]


def text_strikes(text):
    """Strike numbers named in the TEXT. Reliable form: number followed by C/P
    ('106P', '7735C'), plus bare 4-5 digit index strikes. Used to reject legs the
    brain fabricated (it invents strikes not present in the message)."""
    t = text or ""
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)      # 7,635 -> 7635 (screenshot form)
    found = set()
    for m in re.findall(r"(?<!\d)(\d{2,5}(?:\.\d+)?)\s*[CP]\b", t, re.I):
        found.add(float(m))
    for m in re.findall(r"(?<!\d)(\d{4,5}(?:\.\d+)?)(?!\d)", t):
        found.add(float(m))
    # vertical notation: "32.5/37.5 Bear calls", "177.5/182.5", "78/83" — two
    # strikes split by "/" (James's standard short-spread form). NOT a date
    # ("for 10/30", "exp 11/6"): skip whole-number MM/DD pairs.
    for m in re.finditer(r"(?<!\d)(\d{2,5}(?:\.\d+)?)\s*/\s*(\d{2,5}(?:\.\d+)?)(?!\d)", t):
        a, b = float(m.group(1)), float(m.group(2))
        if a == int(a) and b == int(b) and 1 <= a <= 12 and 1 <= b <= 31:
            continue                      # date-like (MM/DD), not a strike pair
        found.add(a)
        found.add(b)
    return sorted(found)


def entry_language(text):
    """True only when the text issues an actual trade command.
    'We are holding Bear calls from yesterday. we will see a big drop' has NO
    command (it is a status + prediction) — yet the brain labelled it entry and
    the pipeline opened a live short. Commands are what make a message an entry."""
    return bool(re.search(r"\b(buy|sell|collect|enter)\w*\b",
                          text or "", re.I))


def has_tradeable_content(text):
    """A strike + a premium (e.g. '38C at 1.8') is an entry even without a
    buy/sell verb. Keeps AMKR-style brackets from being over-blocked."""
    t = text or ""
    has_strike = bool(re.search(r"\d{2,5}(?:\.\d+)?\s*[CP]\b", t, re.I))
    has_premium = bool(re.search(r"\d+\.\d{1,2}\b", t))
    return has_strike and has_premium


def text_is_naked_short(text):
    """Selling a call/put with NO offsetting buy and no stated spread = a naked
    write (UNLIMITED loss). This — and only this — is undefined risk. 'Sell
    7735C n Buy 7745C' has a buy (a spread, capped at its width); 'enter IWM
    Puts' / 'buy GLW 130C' is LONG the option (capped at the premium paid).

    D113 (2026-11-19): delegates to signal_intent — the ONE canonical verb
    lexicon. NO fallback verb list here: a hard dependency, not a shadow copy.
    If signal_intent cannot be imported the pipeline must fail loudly (the
    alternative is silently drifting back to a stale list — the D110 bug)."""
    import signal_intent as _si
    if not _si.OPTION_RE.search(text or ""):
        return False
    if _si.is_spread_language(text):
        return False
    return _si.direction(text) == "short"


def is_explicit_close(text):
    """An EXIT instruction that names legs (L27: 'Sell to Close 7730P n Buy to close
    7735P'). The verbs (sell/buy) make it look like an entry, but 'to close' marks
    it as an exit. Must route to the exit path BEFORE the spread-park logic."""
    return bool(re.search(r"(sell|buy)\s+to\s+clos|buy\s+back|close\s+(it|them|out)",
                          text or "", re.I))


# D121 (2026-11-19): an EXPLICIT action verb is what separates a management
# COMMAND from a progress STATUS. '35% on bull puts' (4496) was a status — the
# real instruction came next ('trim 80%', 4497) — but the bare-% rule closed the
# whole position on the status. Only a message carrying one of these verbs may
# command a close; a bare '<n>% on <family>' becomes a lifecycle COMMENT instead.
# Every genuine command form ('trim 80%', 'book gains', 'take gains', 'cut 80%',
# 'close it', 'cover') contains one of these, so tightening never drops a real
# instruction — it only stops us acting on an observation.
ACTION_VERB = re.compile(
    r"\b(trim|cut|book(?:ed|ing)?|close(?:s|d|ing)?|cover(?:ed|ing)?|"
    r"tak(?:e|ing)|took|reduce(?:d|s)?|exit(?:s|ed|ing)?)\b", re.I)


def non_entry_class(text):
    """Classify a non-entry message from its language (spec §1).

    TENSE matters. The old regex matched the bare word "gains" and labelled
    forecasts ("Bull Puts will make nice gains at Open") as exits, then wasted an
    exit attempt on them. A management INSTRUCTION carries a verb acting on a
    position ("pl book gains", "trim 80%"); a forecast merely describes a future
    move ("will make 100%"). Proximity "close to N" is not a close command."""
    t = re.sub(r"\bclose\s+to\b", "near", (text or "").lower())
    # 'yesterday close' / "yesterday's close" is the noun, not the verb
    t = re.sub(r"\b(yesterday|today|week|month)(?:'?s)?\s+close\b", r"\1 cls", t)
    # trim a % = partial management, wins even when the msg also forecasts.
    # D126: TRIM_RE lives in signal_intent — ONE definition for both classifiers.
    from signal_intent import TRIM_RE
    if TRIM_RE.search(t):
        return "trim"
    # D126: the guidance test lives in signal_intent — ONE definition, both
    # classifiers use it, so the arrival tag and the trade path cannot drift.
    from signal_intent import is_guidance
    # D121/D126: conditional TRADE GUIDANCE ('Take Overnight trades only if you
    # can close them overnight') instructs FUTURE entries — it is not a close
    # now. The verb's object is a CLASS of trades, not existing gains; and the
    # message names no position to scope. Refuse the exit, classify as narrative.
    # D126: delegate to signal_intent.is_guidance so the arrival tag and the
    # trade path can never disagree about it (the D121 guard had been added to
    # THIS function only, and the arrival tag still read 4501 as an exit).
    if is_guidance(t):
        return "narrative"
    # D121: a BARE '<n>% on <family>' with NO action verb is a STATUS COMMENT,
    # not a command. msg 4496 '35% on bull puts' was a progress report — the
    # real instruction came NEXT (4497 'trim 80%') — but this rule closed the
    # whole position on the status (cost: exited at -$40 instead of +$323).
    # Only fire when an explicit action verb is present ('take 35% on ...').
    # Every real command form still fires — see ACTION_VERB net below.
    if re.search(r"\d{1,3}\s*%\s*on\b", t) and \
            re.search(r"\bbear\s+calls?\b|\bbull\s+puts?\b", t) and \
            ACTION_VERB.search(t):
        return "exit"
    # scanner / scoring signal source (Swing scanner, score thresholds)
    if re.search(r"\b(scanner|scan|score|scored|graded|rating|graded)\b", t):
        return "scanner"
    # an EXIT needs a management VERB + an object (gain/profit/out/it/them/pos),
    # not just the word "gains" — this is what separates "book gains" (exit) from
    # "will make gains" (prediction).
    mgmt_verb = re.search(r"\b(pls?|please)?\s*(book|booked|tak(?:e|en|ing)|took|close|exit|cut)\w*\b", t)
    mgmt_obj = re.search(r"\b(gains?|profits?|out|it|them|position|here)\b", t)
    future = re.search(r"\b(will|going to|gonna|expect\w*|should|may|might|hoping)\b", t)
    # only a FUTURE MANAGEMENT VERB negates the exit — 'taking gains on bear
    # calls, will wait for pull back to enter' is a command NOW with a later plan
    fut_mgmt = re.search(r"\b(?:will|going to|gonna)\s+(?:be\s+)?"
                         r"(?:book|take|close|exit|cut)", t)
    if mgmt_verb and mgmt_obj and not fut_mgmt:
        return "exit"
    # D121 safety net: an ACTION VERB + a concrete object (%/gains/out) is a
    # management command even when no strikes are named — so tightening the
    # bare-% rule can never drop a real instruction ('cut 80%', 'cover 50%',
    # 'reduce 30%'). Conditional/future phrasing still defers to prediction.
    # NOTE: no trailing \b after the % branch — '%' is not a word character, so
    # \b after it fails at end-of-string and silently dropped 'cut 80%'.
    if not future and ACTION_VERB.search(t) and \
            re.search(r"\bgains?\b|\bprofits?\b|\bout\b|\d{1,3}\s*%", t):
        return "exit"
    if future:
        return "prediction"
    if re.search(r"\b(we are holding|holding|held|still in|open position)\b", t):
        return "hold"
    return "narrative"


def exit_command_wins(text):
    """EXIT-COMMAND PRECEDENCE (msg 3553 miss: 'Pl book them and we can enter
    again if it breaks the box'). A management COMMAND is the actionable
    instruction even when the message mentions an entry in a CONDITIONAL
    future. Only defer to the entry path when the message carries actual
    sell/buy leg verbs ('book at 50%' inside an entry stays an entry).

    'Book <strikes>' (mgmt verb + named structures, no sell/buy verb) is an
    exit too — bare 'take small' is a SIZE note, not an exit (lotto style)."""
    t = text or ""
    # mask the noun uses of 'close' before reading it as a verb
    t = re.sub(r"\bclose\s+to\b", "near", t, flags=re.I)
    t = re.sub(r"\b(yesterday|today|week|month)(?:'?s)?\s+close\b",
               r"\1 cls", t, flags=re.I)
    # '<n>% on bull puts' is a standing trim with NO strikes in it — but D121:
    # a BARE % (no action verb) is a status, so gate it on ACTION_VERB the same
    # way non_entry_class does. Real trims ('trim 80%', 'take 35% on ...') carry
    # the verb and still win.
    pct_struct = bool(re.search(r"\b\d{1,3}\s*%\s*on\b", t, re.I)) and bool(
        re.search(r"\b(?:bull\s+puts?|bear\s+calls?|puts?|calls?)\b", t, re.I)) and bool(
        ACTION_VERB.search(t))
    mgmt = (non_entry_class(t) in ("exit", "trim")
            or pct_struct
            or ((bool(re.search(r"\b(book|trim|cut|tak(?:e|ing)\s+"
                               r"(?:gains?|profits?|out|\d{1,3}\s*%))\b", t, re.I))
                 or bool(re.search(r"(?<!'s )\bclos(?:e|es|ed|ing)\b(?!\s+to\b)", t, re.I)))
                # 'close TO 177.5' is proximity, not a command (QCOM/TXN premarket
                # predictions were firing exits); a real close names strikes
                and bool(text_strikes(t))))
    return (bool(mgmt)
            and not re.search(r"\b(sell|buy)\b", t, re.I)
            and not has_tradeable_content(t))


def _structure_family(text):
    """Which option family a message names: 'All Bear calls' -> calls only,
    'bull puts' -> puts only. None = no structure family named."""
    t = (text or "").lower()
    if re.search(r"\bbear\s+calls?\b", t):
        return "C"
    if re.search(r"\bbull\s+puts?\b", t):
        return "P"
    # plain shorthand: '50% up on FTNT Puts, Pl take gains'
    if re.search(r"\bputs?\b", t):
        return "P"
    if re.search(r"\bcalls?\b", t):
        return "C"
    return None


def scope_family(text):
    """Family of the CLOSED object — look right after the mgmt verb, not at the
    whole message: 'Take gains on remaining Bull Puts if anyone holding,
    holding Bear calls' closes the BULL PUTS (the held bear calls are named
    later and must not win). Falls back to the whole text when no family
    follows the verb."""
    t = text or ""
    m = re.search(r"\b(?:take|book|close|exit|cut|trim|sell)\w*\s+"
                  r"(?:gains?\s+on\s+)?(?:all\s+|the\s+|every\s+)*"
                  r"(?:remaining\s+|our\s+|my\s+)*"
                  r"(?:(\d{1,3}\s*%)?\s*(?:of\s+)?)?(.{0,40})", t, re.I)
    scoped = m.group(2) if m else t
    return _structure_family(scoped or "") or _structure_family(t)


_TICK_STOP = {"ALL", "THE", "OUR", "MY", "PL", "AND", "ON", "OF", "FOR", "TO",
              "BEAR", "BULL", "REMAINING", "HOLDING", "SEE", "ANY", "ANYONE",
              "WHEN", "THAT", "THIS", "AGAIN", "WITH", "NICE", "GAINS", "HOLD",
              "SELL", "BUY", "OPEN", "CLOSE", "TRIM", "EXIT", "LOTTO", "SIZE",
              "LIMIT", "MARKET", "COLLECT", "TODAY", "TOMORROW",
              "DEBIT", "CREDIT",           # "Debit Puts" is a structure, not a ticker
              # TradingView session vocabulary, read off chart images (4047):
              # "Break PMH" / "PMH" / "NYFP1" / "1.16x range (Europe)"
              "PMH", "PML", "PDH", "PDL", "NYFP", "NYFP1", "RANGE", "BREAK",
              "EUROPE", "ASIA", "LONDON", "SPX5"}


def scope_instruments(text):
    """ALL tickers in an exit scope ('QQQ Bear calls and SPX bear calls')."""
    out = set()
    for m in re.finditer(r"\b([A-Za-z]{2,5})\b\s+(?:bear\s+calls?|bull\s+puts?|puts?|calls?)", text or "", re.I):
        t = m.group(1).upper()
        if t not in _TICK_STOP:
            out.add(t)
    return out


def scope_instrument(text):
    """Ticker printed right before a structure family ('95% on QQQ Bear
    calls') — beats a mislabelled brain instrument (3567 came back SLV).
    Common words ('All Bear calls') are excluded."""
    for m in re.finditer(r"\b([A-Za-z]{2,5})\b\s+(?:bear\s+calls?|bull\s+puts?|puts?|calls?)",
                         text or "", re.I):
        t = m.group(1).upper()
        if t not in _TICK_STOP:
            return t
    # 'Trim 80% ON FISV' — the trim object outranks an earlier 'with X' list
    mt = re.search(r"\b(?:trim|book|close|take|exit|cut)\w*[^.]{0,24}?\bon\s+([A-Za-z]{2,5})\b",
                   text or "", re.I)
    if mt and mt.group(1).upper() not in _TICK_STOP:
        return mt.group(1).upper()
    # 'Take gains with RDDT' / 'gains on SMCI' — ticker after a preposition
    for m in re.finditer(r"\b(?:with|on|in)\s+([A-Za-z]{2,5})\b",
                         text or "", re.I):
        raw = m.group(1)
        t = raw.upper()
        if t not in _TICK_STOP and (raw[:1].isupper() or t in OPT_UNDERLYING_MAP):
            return t
    return None


def _fam_ok(t, fam):
    """A 'bear calls' exit addresses call structures only; 'bull puts' only
    puts. Non-option positions are NEVER in scope for a structure-family exit."""
    if not fam:
        return True
    legs = t.get("legs") or []
    if legs:
        rights = set()
        for l in legs:
            if isinstance(l, dict):
                rights.add(str(l.get("right", ""))[:1].lower())
            else:
                rights.add(str(l[1])[:1].lower())
        return bool(rights) and bool(rights & {fam.lower()})
    rid = str(t.get("opt_id") or "")
    if " " in rid:
        try:
            return rid.split(" ", 1)[1][6].upper() == fam
        except IndexError:
            return False
    return False


def reason_text(norm, text):
    """Reason for the ledger: the brain's rationale, but only when it is about
    THIS message — the brain mixes neighbours' text in (msg 3553 carried
    3555's text as reason). No token overlap = fall back to the raw excerpt."""
    r = (norm.get("rationale") or "").strip() if isinstance(norm, dict) else ""
    t = (text or "").strip()
    if not r:
        return t[:80]
    toks = {w for w in re.findall(r"[a-z]{3,}", r.lower())}
    if not toks:
        return t[:80] if t else r[:80]
    if not (toks & set(re.findall(r"[a-z]{3,}", t.lower()))):
        return t[:80]
    return r[:80]


def _strikes_ok(t, named):
    """STRIKE-SCOPED exits (msg 3725: 'take gains on 80% of 7625/7635 and 7620
    and 7610, 7615/7605, we can hold all others'). When the message NAMES
    strikes, a position is in scope only if ALL its strikes are named — the
    untold positions are 'the others' he says to hold. No named strikes = the
    whole scope passes."""
    if not named:
        return True
    # 2-DIGIT STRIKE SHORTHAND: 'gains on 35/45' means 7735/7745. When every
    # named strike is < 100 and the position's strikes are index-scale, match
    # on the last two digits instead of full equality.
    if all(n < 100 for n in named):
        g0 = t.get if isinstance(t, dict) else (lambda k, d=None: getattr(t, k, d))
        raw_ks = []
        for _l in (g0("legs") or []):
            raw_ks.append(float(_l["strike"]) if isinstance(_l, dict) else float(_l[0]))
        if raw_ks and all(k >= 1000 for k in raw_ks):
            return all(int(round(k)) % 100 in {int(n) for n in named} for k in raw_ks)
        if g0("body") is not None:
            _bk = [float(g0("body"))] + ([float(g0("wing_high"))] if g0("wing_high") is not None else [])
            if all(k >= 1000 for k in _bk):
                return all(int(round(k)) % 100 in {int(n) for n in named} for k in _bk)
    g = t.get if isinstance(t, dict) else (lambda k, d=None: getattr(t, k, d))
    legs = g("legs") or []
    if legs:
        ks = set()
        for l in legs:
            if isinstance(l, dict):
                ks.add(float(l.get("strike")))
            else:
                ks.add(float(l[0]))
        return bool(ks) and ks <= named
    if g("body") is not None:                     # book Position
        ks = {float(g("body"))}
        if g("wing_high") is not None:
            ks.add(float(g("wing_high")))
        return ks <= named
    rid = str(g("opt_id") or "")
    if " " in rid:                                # single option: strike in the id
        try:
            return float(int(rid.split(" ", 1)[1][7:]) / 1000) in named
        except (IndexError, ValueError):
            return False
    return False                                  # stock CFD: never strike-scoped


def parse_target_close(text):
    """A PLANNED exit at a level ('7660 next target to close all remaining Bull
    Puts', 'close all QQQ puts at 3.2') -> (level, scope_text) or None. This is
    the exit LADDER: the close fires when the level is reached, not at market."""
    t = text or ""
    m = re.search(r"(\d{1,5}(?:\.\d+)?)\s+next\s+target\s+(?:to|for)\s+"
                  r"(?:close|book|exit|take)\s*(?:gains\s+on)?\s*(.+?)(?:[.\n]|$)",
                  t, re.I)
    if m:
        return float(m.group(1)), m.group(2).strip()
    m = re.search(r"\b(?:close|book|exit)\s+(?:all\s+)?(?:remaining\s+)?(.+?)\s+"
                  r"(?:at|@)\s+\$?(\d{1,5}(?:\.\d+)?)", t, re.I)
    if m:
        return float(m.group(2)), m.group(1).strip()
    return None


def _exit_target_hit(t):
    """(hit?, price) for a position's armed level target. Bull-put structures
    profit from price ABOVE, call structures from BELOW."""
    et = t.get("exit_target") or {}
    lvl = et.get("level")
    if not lvl:
        return False, None
    try:
        px = bbc().get_quote("SPX500") \
            if _canon_underlying(t.get("instrument")) == "SPX" else quote_for(t)
    except Exception:
        return False, None
    if px is None:
        return False, None
    hit = px >= lvl if et.get("side") == "above" else px <= lvl
    return bool(hit), px


def arm_colour_levels(levels, text, msg_id, st, instrument=None):
    """Market-colour messages ('SPX below 7550 now', 'Big bounce on SPX') carry
    levels that walk an open position into profit. Arm the level as an exit
    trigger on each matching open position — the same mechanism the exit ladder
    uses, but without requiring a target-close verb. An armed colour level
    fires when price crosses it; a held-short spread (call) wins below, a
    held-put spread wins above. Returns the armed position ids.

    D124 (2026-11-20): the level's INSTRUMENT must match the position's.
    msg 4502 '7345 target' is an SPX level, but _structure_family returns None
    for it (no 'puts'/'calls' word) so the family guard was skipped and the
    7345 target armed on SOXL (TL4415) and NVDA (TL4455) — levels those
    underlyings can never reach (they trade ~50 and ~155). A level only walks a
    position whose own instrument it is quoted in."""
    if not (levels and st.get("open")):
        return []
    fam = _structure_family(text)
    lvl_inst = _canon_underlying(instrument) if instrument else None
    armed = []
    for lvl, role in levels:
        if role not in ("below", "above", "target", "support", "resistance"):
            continue
        # colour direction: "below/at resistance" means price is BELOW the level
        # (a call spread banks there); "above/at support" means ABOVE (a put does).
        want_side = "below" if role in ("below", "resistance") else "above"
        for tid, t in st.get("open", {}).items():
            if not str(t.get("status", "")).startswith("open"):
                continue
            if fam and not _fam_ok(t, fam):
                continue
            # D124: the level is quoted in ONE instrument — only arm positions
            # in that instrument (SPXW/SPX both canonicalise to SPX; SOXL/NVDA
            # do not). Unknown instruments on either side do not block.
            if lvl_inst:
                pos_inst = _canon_underlying(t.get("instrument"))
                if pos_inst and pos_inst != lvl_inst:
                    continue
            legs = t.get("legs") or []
            t_side = "below" if legs and str(legs[0][1]).startswith("c") else "above"
            if t_side != want_side:
                continue  # colour direction does not match the position's profit direction
            if t.get("exit_target"):
                continue  # an explicit ladder target already armed: don't let colour override it
            t["exit_target"] = {"level": lvl, "side": want_side, "msg": msg_id,
                                "ts": now(), "source": "market_colour"}
            armed.append(tid)
    if armed:
        ledger_row({"event": "ARMED", "msg_id": msg_id,
                    "reason": f"market-colour armed {armed} at {levels[0][0]:g}",
                    "raw": (text or "")[:80]})
        push("🎯 COLOUR ARMED (msg %s): %s — closes %s when price reaches %g "
             "(%s)" % (msg_id, (text or "")[:50], "/".join(armed),
                       levels[0][0], levels[0][1]), st)
    return armed


def arm_exit_targets(norm, text, msg_id, st):
    """Arm level-target closes on matching OPEN positions (exit ladder).
    Returns True when the message was a target-close (handled), else False."""
    pc = parse_target_close(text)
    if not pc:
        return False
    level, scope = pc
    fam = _structure_family(scope) or _structure_family(text)
    named = set(text_strikes(scope))
    armed = []
    for t in (st.get("open") or {}).values():
        if not str(t.get("status", "")).startswith("open"):
            continue
        if fam and not _fam_ok(t, fam):
            continue
        if named and not _strikes_ok(t, named):
            continue
        legs = t.get("legs") or []
        if fam == "P":
            side = "above"
        elif fam == "C":
            side = "below"
        else:
            r0 = legs[0][1] if legs and not isinstance(legs[0], dict) else ""
            side = "above" if str(r0).startswith("p") else "below"
        t["exit_target"] = {"level": level, "side": side, "msg": msg_id, "ts": now()}
        armed.append(t["id"])
    ledger_row({"event": "TARGET", "msg_id": msg_id,
                "instrument": norm.get("instrument"),
                "targets": f"{level:g}={scope[:40]}",
                "status": "armed" if armed else "no_match",
                "reason": (f"target-close {level:g} armed on {armed}" if armed
                           else f"target-close {level:g}, nothing matched "
                                f"'{scope[:40]}'"),
                "raw": (text or "")[:80]})
    if armed:
        push("🎯 EXIT TARGET ARMED (msg %s): close %s when price reaches %g "
             "(%s) — fires from the manage loop" % (msg_id, "/".join(armed),
                                                    level, scope[:40]), st)
    else:
        push("🎯 EXIT TARGET (msg %s): %g for '%s' — nothing open matched, "
             "level stored" % (msg_id, level, scope[:40]), st)
    save_state(st)
    return True


def text_trigger(text):
    """A CONDITIONAL plan ('we can enter again if it breaks the box'): not a
    trade, but the condition is a documented plan — captured as a TRIGGER so a
    later break message has the plan behind it instead of looking unprovoked."""
    # a levels VIEW is a plan too: 'bearish below 28710 / bullish above 28880'
    mv = re.search(r"(((?:bearish|bullish)\s+(?:below|above)\s+\$?\d{3,5})"
                  r"[^.?!]{0,40}?((?:bearish|bullish)\s+(?:below|above)\s+\$?\d{3,5}))",
                  text or "", re.I)
    if mv:
        return mv.group(1)
    m = re.search(r"((?:\w+\s+){0,8}?\b(?:re-?enter|enter|buy|sell|long|short)\w*\b"
                  r"[^.?!]{0,80}?\b(?:if|when|once)\b[^.?!]{0,80})", text or "", re.I)
    if not m:
        # REVERSED wording: 'If Price goes to the top, we can enter Bear calls'
        # (msg 3419) — condition first, command after.
        m = re.search(r"((?:if|when|once)\b[^.?!]{2,80}?[^.?!]{0,40}?"
                      r"\b(?:re-?enter|enter|buy|sell|long|short)\w*[^.?!]{0,40})",
                      text or "", re.I)
    if not m:
        # 'will wait for pull back to enter' — plan without an if-clause
        m = re.search(r"((?:\w+\s+){0,6}?wait(?:ing)?\s+for\s+[^.?!]{2,60}?"
                      r"\bto\s+(?:enter|buy|sell|long|short)\w*)", text or "", re.I)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------- v2 core integration
# The clean model (james_core) is wired in as a SAFETY LAYER over the legacy
# paths: a risk gate blocks dangerous trades before any broker call, and exits
# resolve through the ExitEngine. Adds policy without breaking the happy path.

# PHASE 2 (2026-11-03): lottos are LONG options = defined risk (max loss = the
# premium) and are traded at a forced 1-contract size. Blocking them while
# trading identical non-lotto singles (HD 350C vs O 57.5C) was inconsistent.
_RISK = RiskPolicy(phase=2)
_EXITS = ExitEngine()
_SCOPE = ScopeResolver()


def _merge_img_into_card(card, fields):
    """Fill card gaps from the screenshot (image is the source of truth for
    qty/expiry/fill/account/pct per the review). Text-derived structure stays."""
    if not isinstance(card, dict):
        return card
    if fields.get("qty") and not card.get("qty"):
        card["qty"] = fields["qty"]
    # expiry: BOTH paths need it — single options read option.expiry, SPREADS
    # read card-level expiry (msg 3562: text has no date, screenshot is 0DTE;
    # without this the multileg resolver falls back to next Friday).
    if fields.get("expiry") and not card.get("expiry"):
        card["expiry"] = fields["expiry"]
    if fields.get("expiry") and not card.get("option_expiry"):
        if isinstance(card.get("option"), dict):
            card["option"]["expiry"] = fields["expiry"]
    if fields.get("credit") and not card.get("entry"):
        card["entry"] = fields["credit"]
        card["_credit_from_image"] = True          # needs the chain check-off
    if fields.get("underlying") and not card.get("instrument"):
        card["instrument"] = fields["underlying"]
    if fields.get("message_on_image") and not card.get("message_on_image"):
        card["message_on_image"] = fields["message_on_image"]
    if fields.get("pct") is not None:
        card["pct_actual"] = fields["pct"]
    if fields.get("account"):
        card["account"] = fields["account"]
    if fields.get("risk") is not None:
        card["risk_stated"] = fields["risk"]
    if fields.get("reward") is not None:
        card["reward_stated"] = fields["reward"]
    return card


def to_record(norm, text, msg_id):
    """validated card + text -> a v2 Record (typed unit)."""
    sp = norm.get("spread") or {}
    # TEXT-FIRST legs: the brain collapses legs non-deterministically (an iron
    # fly came back 3 legs). The text is authoritative for structure.
    tl = text_legs(text)
    legs_raw = tl if tl else (sp.get("legs") or [])
    cls = MsgClass.OPTION_BRACKET if norm.get("option") else (
        MsgClass.NAKED if not norm.get("spread") and norm.get("option") is None
        and entry_language(text) else MsgClass.VERTICAL)
    if norm.get("spread"):
        n = len(legs_raw)
        cls = MsgClass.FLY if n >= 4 else (MsgClass.VERTICAL if n == 2 else MsgClass.NAKED)
    low = (text or "").lower()
    # TEXT-FIRST risk classification: a NAKED SHORT is undefined risk; buying a
    # call/put ("enter IWM Puts", "buy GLW 130C") is a LONG option = DEFINED risk
    # (max loss = the premium paid). The old logic tagged any command-with-no-
    # spread as NAKED and risk-blocked real long-option entries.
    naked = text_is_naked_short(text)
    if not norm.get("spread"):
        if norm.get("option"):
            cls = MsgClass.OPTION_BRACKET
        elif naked:
            cls = MsgClass.NAKED
        elif entry_language(text) and re.search(r"\b(call|put)s?\b|\d\s*[cp]\b", low):
            cls = MsgClass.OPTION_BRACKET      # single long option (buy/enter)
        elif entry_language(text):
            cls = MsgClass.NAKED
    if "lotto" in low or "high risk" in low:
        cls = MsgClass.LOTTO          # L30: lotto/high-risk is its own class
    r = Record(msg_id=msg_id, ts=now(), cls=cls,
               action=Action(norm["action"] if norm.get("action") in
                             ("entry", "exit", "hold", "trim", "ignore", "none") else "entry"),
               raw=(text or "")[:400], source="text",
               direction=("short" if naked else "long"),
               underlying=norm.get("instrument"),
               credit=norm.get("entry") or sp.get("net"),
               stop=norm.get("stop"), targets=list(norm.get("targets") or []),
               legs=[Leg(strike=float(l["strike"]), right=str(l.get("right") or "put"),
                         action=str(l.get("action") or "buy"))
                     for l in legs_raw if l.get("strike")],
               width=(max((l.get("strike", 0) for l in legs_raw), default=0)
                      - min((l.get("strike", 0) for l in legs_raw), default=0)
                      if legs_raw else None),
               risk_tier=RiskTier.LOTTO if "lotto" in (text or "").lower() else
                         (RiskTier.HIGH_RISK if "high risk" in (text or "").lower()
                          else RiskTier.NORMAL))
    # L38: derive width from risk/reward if legs didn't give it
    if r.width is None or r.width == 0:
        r.width = r.derive_width_from_risk_reward()
    return r


def risk_gate(norm, text, msg_id, st):
    """Run the policy BEFORE any broker call. Returns True to proceed.
    Blocks: naked (undefined risk), lotto (Phase 1), context records.
    NEVER blocks a defined-risk structure (spec §10 balance guard)."""
    rec = to_record(norm, text, msg_id)
    allowed, why = _RISK.allow(rec)
    if not allowed:
        ledger_row({"event": "RISK_BLOCK", "msg_id": msg_id,
                    "instrument": norm.get("instrument"),
                    "direction": norm["direction"], "entry": rec.credit,
                    "reason": f"risk gate: {why}",
                    "raw": (text or "")[:120]})
        log(f"msg {msg_id}: RISK BLOCK — {why}")
        push("RISK GATE (msg %s) - not traded | %s %s | %s" % (
            msg_id, norm.get("instrument"), rec.cls.value, why), st)
        save_state(st)
        return False
    return True


def is_map_message(text):
    """Level-map + view message: 'S1:$29780 ... R1:$30080, bullish above 30000,
    bearish below 29850'. Context, never a trade. Detected from TEXT (the brain
    calls these NO_TRADE and drops the levels)."""
    t = text or ""
    has_levels = bool(re.search(r"\b[SR][1-3]\b", t)) or bool(
        re.search(r"(bullish|bearish)\s+(above|below)", t, re.I))
    has_prices = len(re.findall(r"\d{4,5}(?:\.\d+)?", t)) >= 2
    return has_levels and has_prices


def map_record(norm, msg_id, text, st):
    """Persist a level map so later trades can be scored against his zones."""
    levels = {}
    for lbl, val in re.findall(r"\b([SR][1-3])\s*:?\s*\$?([\d,]+(?:\.\d+)?)",
                               text or ""):
        levels[lbl] = float(val.replace(",", ""))
    view = re.findall(r"(bullish\s+above|bearish\s+below)\s+\$?([\d,]+(?:\.\d+)?)",
                      text or "", re.I)
    row = {"ts": now(), "event": "MAP", "msg_id": msg_id,
           "instrument": norm.get("instrument"), "direction": "flat",
           "entry": None,
           "targets": "|".join(f"{k}={v:g}" for k, v in levels.items()),
           "reason": ("; ".join(f"{a} {b}" for a, b in view) or "level map"),
           "raw": (text or "")[:200]}
    ledger_row(row)
    log(f"map msg {msg_id}: {norm.get('instrument')} "
        f"{len(levels)} levels + {len(view)} view lines — stored, not traded")
    save_state(st)


def scanner_record(norm, msg_id, text, st):
    """A scanner / scoring signal source (e.g. 'Our Swing scanner gave numbers
    for the earnings... Score above 60 = Long, below -60 = Short'). This is a
    SIGNAL SOURCE with its own decision rule — captured as a typed record so we
    can later wire it as an input, and its scoring rule is preserved verbatim."""
    t = text or ""
    # capture explicit scoring thresholds: "Score above 60 good for Long and
    # score below -60 good for Short"
    rules = re.findall(
        r"(?:score|rating|grade)\s*(above|over|>|below|under|<)\s*(-?\d+)[^.,;]*?(long|short|buy|sell)",
        t, re.I)
    def _cmp(c):
        return ">=" if c.lower() in ("above", "over", ">") else "<="
    rule_txt = "; ".join(f"score {_cmp(cmp)} {int(n)} -> {d2.lower()}"
                         for cmp, n, d2 in rules) or "no explicit thresholds in text"
    # tickers: the SCANNER TABLE on the image is authoritative (30 scored rows);
    # the text only ever names a couple of them
    rows = norm.get("scanner_rows") or []
    tickers = [r["ticker"] for r in rows if r.get("ticker")]
    if not tickers:
        tickers = re.findall(r"\b([A-Z]{2,5})\b", t)
        tickers = [x for x in tickers if x not in ("THE", "AND", "FOR", "OUR", "ALL",
                   "SCORE", "LONG", "SHORT", "GOOD", "WITH", "THIS", "THAT", "BUT")]
    longs = [r for r in rows if (r.get("score") or 0) >= 60]
    shorts = [r for r in rows if (r.get("score") or 0) <= -60]
    row = {"ts": now(), "event": "SCANNER", "msg_id": msg_id,
           "instrument": norm.get("instrument") or (tickers[0] if tickers else None),
           "direction": "flat", "entry": None,
           "targets": rule_txt,
           "reason": "signal source (scanner) — decision rule captured",
           "raw": (text or "")[:200]}
    ledger_row(row)
    if rows:
        ledger_row({"ts": now(), "event": "SCANNER_TABLE", "msg_id": msg_id,
                    "targets": "|".join(
                        f"{r['ticker']}:{r.get('score')}:{r.get('verdict') or ''}"
                        for r in rows),
                    "reason": f"{len(rows)} scored rows; rule: {rule_txt}",
                    "raw": (t or "")[:80]})
    log(f"scanner msg {msg_id}: rule [{rule_txt}] tickers={tickers[:8]} — captured, not traded")
    _top = " ".join(f"{r['ticker']}:{r.get('score')}" for r in longs[:8]) or "—"
    _bot = " ".join(f"{r['ticker']}:{r.get('score')}" for r in shorts[:8]) or "—"
    push("📊 SCANNER SIGNAL (msg %s) — %s\nrule: %s\n"
         "longs: %s\nshorts: %s\nrecorded as a signal source (not auto-traded)."
         % (msg_id, (t or "")[:120], rule_txt, _top, _bot), st)
    save_state(st)


def inherit_rights_from_park(legs, text, when=None):
    """A bare correction ("Read this as Sell 7705 n buy 7715", msg 3903) names
    strikes but no rights, and with no card legs the structure was unparseable.
    Take the right KIND from the parked structure just before it (the correction
    shifts strikes by a few points, so exact matching is useless). Returns legs
    with rights, or None."""
    try:
        if len(legs or []) < 2 or not PKL.exists():
            return None
        want_t = float(when or time.time())
        best = None
        for row in csv.DictReader(PKL.open(encoding="utf-8")):
            raw = row.get("raw") or ""
            pairs = [(int(k), ch) for k, ch in re.findall(
                r"\b(\d{3,5})\s*([cpCP])\b", raw)]
            if not pairs:
                continue
            gap = min(abs(int(float(l["strike"])) - k)
                      for l in legs if l.get("strike") for k, _c in pairs)
            letters = [ch for _k, ch in pairs]
            try:
                t = calendar.timegm(time.strptime((row.get("ts") or "")[:19],
                                                  "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            if t <= want_t and want_t - t <= 86400 and gap <= 30:
                cand = (-gap, t, letters, row.get("msg_id"))
                if not best or cand[:2] > best[:2]:
                    best = cand
        if not best:
            return None
        letters = best[2]
        kind = "call" if sum(1 for x in letters if x.lower() == "c") >= sum(
            1 for x in letters if x.lower() == "p") else "put"
        log(f"rights inherited ({kind}) from parked msg {best[3]}")
        return [{**l, "right": kind} for l in legs]
    except Exception as e:
        log(f"rights inherit failed: {type(e).__name__}: {e}")
    return None


_PK_SEEN = None


def _pk_seen():
    """Dedupe index for parking_lot.csv: (msg_id, reason[:60], raw[:60]) ->
    present. Seeded from the existing register on first use."""
    global _PK_SEEN
    if _PK_SEEN is None:
        _PK_SEEN = set()
        if PKL.exists():
            with PKL.open(encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    _PK_SEEN.add((str(r.get("msg_id") or ""),
                                  (r.get("reason") or "")[:60],
                                  (r.get("raw") or "")[:60]))
    return _PK_SEEN


_TS = {}                              # msg_id -> message SEND epoch (link timestamps)


def _fmt_msg_ts(msg_id, msg_dt=None):
    """Message send-timestamp for relationship rows (2026-11-20 user note:
    'while established relationship please make to capture the timestamp as
    well'). Prefers the real send time (msg_dt), falls back to the recorded
    send epoch for the id, else empty — LINK rows show WHEN James sent the
    linking message, not just when we wrote the row."""
    e = msg_dt or _TS.get(str(msg_id))
    if not e:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(e)))
    except Exception:
        return ""


def park_signal(norm, msg_id, text, why):
    """Record a real-but-untradeable structure in the parking lot.
    Nothing is dropped: economics go to the CSV, reasoning to the MD."""
    sp = norm.get("spread") or {}
    legs = sp.get("legs") or []
    strikes = [float(l["strike"]) for l in legs if l.get("strike")]
    sold = [float(l["strike"]) for l in legs if l.get("action") == "sell" and l.get("strike")]
    net = norm.get("entry") or sp.get("net")
    low = (text or "").lower()
    tstrikes = text_strikes(text)
    says_fly = ("/" in low) or ("butterfly" in low)
    if says_fly and len(tstrikes) >= 3:
        # "Sell 7725C/P, Buy 7715P n 7735C": the TEXT is deterministic, the brain
        # is not (it randomly drops the body leg). body = middle strike.
        body = tstrikes[len(tstrikes) // 2]
        wings = [tstrikes[0], tstrikes[-1]]
        structure = "short_iron_butterfly" if sp.get("type") != "debit" else "iron_butterfly"
    else:
        body = max(set(sold), key=sold.count) if sold else (min(strikes) if strikes else None)
        wings = [s for s in strikes if s != body]
        structure = None
    width = round(max(abs(body - w) for w in wings), 2) if (body and wings) else None
    sides = {(l.get("action"), str(l.get("right", "")).lower()[:1]) for l in legs}
    body_sold_both_ways = (sold.count(body) >= 2 if body else False)
    if structure is None:
        if body_sold_both_ways and len(wings) >= 1:
            structure = "iron_condor"
        elif len(legs) >= 4:
            structure = "iron_condor"
        elif len(legs) == 2 or len(strikes) == 2:
            structure = "vertical_" + str(sp.get("type") or "credit")
        elif len(legs) == 1:
            structure = "single_option_" + str(sp.get("type") or "long")
        else:
            structure = "multi_leg_" + str(sp.get("type") or "unknown")
    # a butterfly sells the body twice: with only 3 legs reported, mark the gap
    missing = []
    qty = None
    if structure.endswith("butterfly") and not body_sold_both_ways:
        missing.append("body leg reported once (expected sold twice) — wings/body "
                       "recovered from text, verify against the ticket")
    if structure.endswith("butterfly") and len(wings) < 2:
        missing.append("only one wing leg reported")
    if len(legs) < 4 and structure.endswith("butterfly"):
        missing.append(f"parser reported {len(legs)} legs for a 4-leg structure")
    row = {"ts": now(), "event": "PARKED", "msg_id": msg_id,
           "structure": structure, "underlying": norm.get("instrument"),
           "body": body,
           "wing_low": min(wings) if wings else None,
           "wing_high": max(wings) if wings else None,
           "width": width, "legs": len(legs), "qty": qty, "credit": net,
           "prob": round(net / width, 3) if (net and width) else None,
           "profit": (net * 100) if net else None,
           "maxloss": (round((width - net) * 100, 2) if (net and width) else None),
           "reason": why, "missing": "; ".join(missing),
           # D46: FULL text — truncating here once corrupted a settle (the
           # legs live in the tail of narrative messages). Truncate at display
           # only (D36's rule, finally applied to the park register).
           "raw": (text or "")}
    PKL.parent.mkdir(exist_ok=True)
    new = not PKL.exists()
    # D42: one row per (msg, reason, raw) — the register must not thicken when
    # a message is reprocessed. A test spy forwarded here and re-parked fixture
    # messages on every suite run (msg 928 reached 312 copies); a re-park with
    # a DIFFERENT reason is kept, it is information.
    _key = (str(msg_id or ""), (why or "")[:60], (text or "")[:60])
    if _key in _pk_seen():
        return
    _pk_seen().add(_key)
    with PKL.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PARK_COLS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in PARK_COLS})
    ledger_row({"event": "PARKED", "msg_id": msg_id,
                "instrument": norm.get("instrument"),
                "direction": norm["direction"], "entry": net,
                "reason": why, "raw": (text or "")[:120]})
    # D119: lifecycle — parked structure recorded
    try:
        import trade_lifecycle as _tlc
        _lc = _tlc.comment_lifecycle(f"LC-{msg_id}", msg_id,
                                     norm.get("instrument") or "",
                                     f"PARKED: {why[:60]}", text or "")
        # D123: chain a parked structure onto the thread it belongs to
        _tlc.related_and_link(_lc, norm.get("instrument") or "", (text or "")[:50],
                              msg_id=str(msg_id), src_ts=_fmt_msg_ts(msg_id))
    except Exception:
        pass


BOOK_FILE = HERE / "book.json"
_BOOK_LOCK = threading.Lock()      # book.json load-modify-save is not atomic


def _load_book():
    try:
        return Book.from_dict(json.loads(BOOK_FILE.read_text(encoding="utf-8")))
    except Exception:
        return Book()


def _save_book(book):
    try:
        BOOK_FILE.write_text(json.dumps(book.to_dict(), indent=1), encoding="utf-8")
    except Exception:
        pass


def _names_structure(text):
    """The message names a tradeable structure: an underlying + (strikes or a
    call/put). Used to decide whether to synthesize a held position."""
    return bool(text_strikes(text)) or bool(re.search(r"\b(call|put)s?\b", text or "", re.I))


def _synth_position(book, norm, text, msg_id, st_ts):
    """Create a held position reconstructed from the message text. The channel
    assumes continuity — it references 'SMCI 32.5/37.5 Bear calls' without ever
    messaging the open. Synthesizing here makes the book SELF-HEALING so the
    management op in the same message has something to act on."""
    u = (norm.get("instrument") or "").upper()
    strikes = text_strikes(text)
    low = (text or "").lower()
    if "bear call" in low or ("bear" in low and "call" in low):
        structure = "bear_call_spread"
    elif "bull put" in low or ("bull" in low and "put" in low):
        structure = "bull_put_spread"
    elif "iron" in low or "butterfly" in low or "fly" in low:
        structure = "iron_fly"
    elif "condor" in low:
        structure = "iron_condor"
    elif len(strikes) >= 2:
        structure = "vertical"
    elif re.search(r"\b(call|put)s?\b", low):
        structure = "single_option"
    else:
        structure = "unknown"
    p = book.open(msg_id, st_ts, structure=structure, underlying=u,
                  body=(min(strikes) if strikes else None),
                  wing_high=(max(strikes) if len(strikes) > 1 else None),
                  credit=norm.get("entry"), qty=norm.get("qty"),
                  expiry=norm.get("expiry"), account=norm.get("account"))
    p.note = "held position synthesized from stream reference (entry basis in screenshots)"
    return p


def book_apply(norm, text, msg_id, st=None):
    """EVERY message is bookkeeping (design rule): update the position book so
    management messages (%, trim, take gains) resolve against a live watchlist.

    SELF-HEALING: if a message references a held structure with no open book
    position, synthesize it first — so 'trim 80%' on a never-messaged position
    still resolves. Unnamed exits never blind-close the whole book (a wrong
    trade is worse than a missed one): they surface candidates instead."""
    try:
        amb = None
        with _BOOK_LOCK:
            book = _load_book()
            norm = norm or {}
            op = message_to_op(text, "")
            # TEXT tickers beat a mislabelled brain instrument (3567) and may
            # be plural (3602 'QQQ Bear calls and SPX bear calls')
            uset = {_canon_underlying(x) for x in
                    (scope_instruments(text) |
                     ({scope_instrument(text)} if scope_instrument(text) else set()) |
                     ({norm.get("instrument")} if norm.get("instrument") else set()))}
            uset.discard(None)
            u = (scope_instrument(text) or norm.get("instrument") or "").upper()
            st_ts = now()
            fam = scope_family(text)              # family of the CLOSED object
            named = set(text_strikes(text))       # strike-scoped exits (3725)
            def _pfam_ok(p):
                if not fam:
                    return True
                s = (p.structure or "").lower()
                if "call" in s:
                    return fam == "C"
                if "put" in s:
                    return fam == "P"
                return True     # family-unlabelled legacy row: never miss it
            def match(p, u=u):
                same_u = (_canon_underlying(p.underlying) in uset) if uset else p.is_open
                return same_u and _pfam_ok(p)

            def _scoped():
                """Named strikes bind ONLY when they hit a real position — a
                mentioned index level ('close to 7714') must not empty the
                scope (msg 3553 regression)."""
                if not named:
                    return match
                f = lambda p: match(p) and _strikes_ok(p, named)
                return f if any(f(p) for p in book.positions.values() if p.is_open) \
                    else match
            # self-heal: reconstruct a referenced-but-unbooked position BEFORE the op
            already = next((p for p in book.positions.values()
                            if p.is_open and u
                            and _canon_underlying(p.underlying) == _canon_underlying(u)), None)
            if op != Op.OPEN and already is None and u and _names_structure(text):
                _synth_position(book, norm, text, msg_id, st_ts)
            resolved = []
            if op == Op.OPEN:
                sp = norm.get("spread") or {}
                legs = sp.get("legs") or []
                strikes = sorted({float(l["strike"]) for l in legs if l.get("strike")})
                book.open(msg_id, st_ts,
                          structure=("bear_call_spread" if fam == "C" else
                                     "bull_put_spread" if fam == "P" else
                                     ("vertical" if len(legs) == 2 else ("fly" if len(legs) >= 4 else "single"))),
                          underlying=u, body=strikes[0] if strikes else norm.get("entry"),
                          wing_high=strikes[-1] if len(strikes) > 1 else None,
                          credit=norm.get("entry") or sp.get("net"),
                          qty=norm.get("qty"), expiry=norm.get("expiry"),
                          account=norm.get("account"))
            elif op == Op.CLOSE:
                if norm.get("pct_actual") is not None:
                    book.mark(_scoped(), norm["pct_actual"], st_ts)   # keep the mark
                if u:
                    resolved = book.close(_scoped(), st_ts)
                else:
                    opens = [p for p in book.positions.values() if p.is_open]
                    if len(opens) == 1:
                        resolved = book.close(_scoped(), st_ts)
                    elif len(opens) > 1:
                        names = ", ".join(f"{p.underlying or '?'} {p.structure}" for p in opens[:6])
                        amb = (len(opens), names)
                        book.mark(_scoped(), 0, st_ts)
            elif op == Op.TRIM:
                frac = (norm.get("trim") or 0) / 100.0
                if not frac:
                    # anchor the % to a management verb ("trim 80%"), not a
                    # forecast ("will make nearly 100% ... trim 80%"). Verb-
                    # anchored RANGES ('take gains 25/35') trim at the lower
                    # bound; '2t/35' is the 25/35 typo form.
                    lt = (text or "").lower()   # 'Trim 80%' is capitalised
                    m = re.search(r"(?:trim|close|take|book|exit|cut)\w*\D{0,10}?(\d{1,3})\s*%", lt)
                    mr = re.search(r"(?:trim|close|take|book|exit|cut)\w*\D{0,24}?(\d{1,3})([a-z]?)\s*/\s*(\d{1,3})\b(?!\s*%)", lt)
                    if m:
                        frac = int(m.group(1)) / 100.0
                    elif mr:
                        a, b = int(mr.group(1)), int(mr.group(3))
                        if a < 10 and mr.group(2):
                            a = a * 10 + 5
                        frac = a / 100.0 if (1 <= a < 100 and 1 <= b <= 100 and a < b) else 0.5
                    else:
                        frac = 0.5
                frac = max(0.0, min(1.0, frac))
                resolved = book.trim(_scoped(), frac, st_ts)
            else:
                # a mark carries the painted/stated % when one exists (msg 3581
                # '40%' on the image) instead of hardcoding 0
                mk = book.mark(_scoped(), (norm.get("pct_actual") or 0), st_ts)
                # and a card's qty REFRESHES the row — the book must track his
                # latest screen. Accounts are untagged, so the same strikes
                # across two accounts collapse into one row (flagged for the
                # human; the executor book is unaffected).
                if norm.get("qty"):
                    qq = resolve_qty(norm.get("qty"), ceiling=999)
                    for p in mk:
                        if p.qty != qq:
                            p.qty = p.qty_remaining = qq
                            p.note = ((p.note + " | ") if p.note else "") +                                 "qty refreshed from screenshot — account unknown/possibly multiple"
            _save_book(book)
        if resolved:
            for _p in resolved:
                james_outcome_row(msg_id, _p, norm, text, st)
        if amb:
            push("⚠️ AMBIGUOUS EXIT (msg %s) — 'book gains' names no position; %d open: %s. Not auto-closing the whole book — act on the specific one." % (msg_id, amb[0], amb[1]), st)
        if resolved:
            for p in resolved:
                push("📒 BOOK %s (msg %s): %s %s %s -> %s (remaining qty %s)" % (
                    op.value.upper(), msg_id, p.underlying, p.structure,
                    f"{p.body:g}/{p.wing_high:g}" if p.body is not None and p.wing_high else "",
                    p.state.value, p.qty_remaining), st)
    except Exception as e:
        log(f"book_apply failed msg {msg_id}: {type(e).__name__}: {e}")


def _canon_underlying(u):
    """Canonical instrument id (2026-11-18: delegate to ticker_taxonomy so the
    ledger, the pattern table and the UW lookup all use ONE name per
    instrument. 110 raw strings collapsed to 80; SPX alone had 5 spellings).

    D114 (2026-11-19): NO fallback inline map — a hard dependency on
    ticker_taxonomy. The old except-branch carried its own SPXW/SPX500 map,
    the exact drift pattern that let names diverge across files."""
    import ticker_taxonomy as _tx
    canon, _bucket = _tx.normalise(u)
    if canon:
        return canon
    if _bucket in ("contract", "junk"):
        # an OCC code or prose noise is NOT an instrument - returning it
        # as one is how the ledger accumulated 190 fake "tickers"
        return None
    return None


def james_outcome_row(msg_id, p, norm, text, st=None):
    """Record JAMES'S side of a closed book position so his hit-rate can be
    compared with ours on the same calls. Never raises."""
    try:
        marks = [m.get("pnl") for m in (p.marks or []) if isinstance(m, dict)
                 and m.get("pnl") is not None]
        last = marks[-1] if marks else None
        pct = norm.get("pct_actual") if isinstance(norm, dict) else None
        if pct is not None:
            res = "win" if pct > 0 else ("loss" if pct < 0 else "scratch")
        elif last is not None:
            res = "win" if last > 0 else ("loss" if last < 0 else "scratch")
        else:
            res = "unknown"
        ours = ""
        if st:
            for tid, t in (st.get("open") or {}).items():
                if _canon_underlying(t.get("instrument")) == _canon_underlying(p.underlying):
                    ours = tid
                    break
        strikes = "/".join(str(x) for x in (p.body, p.wing_high) if x is not None)
        new = (not JOUT.exists()) or JOUT.stat().st_size == 0
        with JOUT.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=JOUT_COLS)
            if new:
                w.writeheader()
            w.writerow({"ts": now(), "msg_id": msg_id, "underlying": p.underlying,
                        "structure": p.structure, "strikes": strikes,
                        "qty": p.qty_remaining, "last_mark_pnl": last,
                        "claimed_pct": pct, "result": res, "our_trade": ours,
                        "raw": (text or "")[:100]})
    except Exception as e:
        log(f"outcome row failed msg {msg_id}: {type(e).__name__}: {e}")


def portfolio_capture(f, msg_id, st):
    """PORTFOLIO SCREENSHOT = BOOK SNAPSHOT (user: 'I need a way to capture all
    these'). Every row on a positions screen (structure, strikes, expiry, qty,
    P&L) updates James's watchlist book: a held position gains a dollar mark,
    an unknown one is synthesized. NEVER a trade — a snapshot is bookkeeping,
    and it keeps the %/mark story honest instead of trusting his stated %."""
    rows = f.get("positions") or []
    if not rows:
        return None
    # ---- normalise rows, then PAIR LEG ROWS into verticals ----
    # A broker leg sheet shows ONE ROW PER LEG (GOOG 310C qty -5 +9,455 /
    # 315C qty +5 −8,135). A short and a long leg at the same underlying,
    # right and expiry are ONE credit spread — pairing keeps leg sheets from
    # landing as two odd singles. Unpaired rows stay singles.
    norm_rows = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        strikes = sorted({float(x) for x in (r.get("strikes") or []) if x})
        if not strikes:
            continue
        under = _canon_underlying(r.get("underlying")) or \
            _canon_underlying(r.get("instrument"))
        if not under:
            continue
        lbl = (r.get("instrument") or "").lower()
        right = "call" if (re.search(r"\bc\b|call", lbl)) else \
            ("put" if re.search(r"\bp\b|put", lbl) else None)
        qty = int(r["qty"]) if isinstance(r.get("qty"), (int, float)) else None
        norm_rows.append({
            "under": under, "strikes": strikes, "right": right, "qty": qty,
            "pnl": r.get("pnl") if isinstance(r.get("pnl"), (int, float)) else None,
            "expiry": r.get("expiry"), "label": r.get("instrument") or "",
            "structure": r.get("structure"), "credit": r.get("credit"),
            "account": r.get("account")})
    entries, used = [], [False] * len(norm_rows)
    for i, a in enumerate(norm_rows):
        if used[i] or a["qty"] is None or a["qty"] >= 0:
            continue
        for j, b in enumerate(norm_rows):
            if i == j or used[j] or b["qty"] is None or b["qty"] <= 0:
                continue
            if b["under"] != a["under"] or b["right"] != a["right"] or \
                    (a["expiry"] or "") != (b["expiry"] or ""):
                continue
            used[i] = used[j] = True
            sk, lk = a["strikes"][0], b["strikes"][0]
            if a["right"] == "call":
                struct = "bear_call_spread" if sk < lk else "debit_call"
            else:
                struct = "bull_put_spread" if sk > lk else "debit_put"
            pnls = [x["pnl"] for x in (a, b) if x["pnl"] is not None]
            entries.append({
                "under": a["under"], "strikes": sorted([sk, lk]),
                "qty": max(1, int(min(abs(a["qty"]), abs(b["qty"])))),
                "pnl": (sum(pnls) if pnls else None), "expiry": a["expiry"],
                "struct": struct, "credit": None, "account": None,
                "label": a["label"]})
            break
    for i, a in enumerate(norm_rows):
        if used[i]:
            continue
        if len(a["strikes"]) >= 2:          # whole spread in one row
            struct = ("bear_call_spread" if a["right"] == "call" else
                      "bull_put_spread" if a["right"] == "put" else "vertical")
        else:
            struct = a["structure"] or "single_option"
        entries.append({**a, "qty": abs(a["qty"]) if a["qty"] else None,
                        "struct": struct})
    captured, total, skipped = [], 0.0, 0
    with _BOOK_LOCK:
        book = _load_book()
        st_ts = now()
        for e in entries:
            strikes = e["strikes"]
            under = e["under"]
            qty = resolve_qty(e.get("qty"), ceiling=999)
            pnl = e.get("pnl")
            total += pnl or 0
            match = next((p for p in book.positions.values()
                          if p.is_open
                          and _canon_underlying(p.underlying) == under
                          and p.body == strikes[0]
                          and p.wing_high == (strikes[-1] if len(strikes) > 1 else None)),
                         None)
            if match is None:
                p = book.open(msg_id, st_ts,
                              structure=e["struct"],
                              underlying=under, body=strikes[0],
                              wing_high=strikes[-1] if len(strikes) > 1 else None,
                              credit=e.get("credit"), qty=qty,
                              expiry=e.get("expiry"), account=e.get("account"))
                p.note = "held position captured from portfolio screenshot"
            else:
                p = match
                if e.get("qty"):
                    p.qty = p.qty_remaining = qty
            if pnl is not None:
                p.marks.append({"ts": st_ts, "pnl": pnl, "src": "portfolio"})
            captured.append("%s %g/%g x%s%s" % (
                under or "?", strikes[0], strikes[-1], qty,
                f" ${pnl:+,.0f}" if pnl is not None else ""))
            ledger_row({"event": "SNAPSHOT", "msg_id": msg_id,
                        "instrument": under, "qty": qty,
                        "targets": f"{strikes[0]:g}/{strikes[-1]:g}",
                        "entry": e.get("credit"),
                        "reason": "portfolio row %s exp %s pnl %s" % (
                            e.get("label") or "", e.get("expiry") or "?", pnl),
                        "raw": (f.get("message_on_image") or "")[:80]})
        _save_book(book)
    if captured:
        log(f"msg {msg_id}: portfolio snapshot -> {len(captured)} pos, "
            f"total ${total:+,.0f}" + (f", {skipped} junk row(s) skipped" if skipped else ""))
        push("📊 BOOK SNAPSHOT (msg %s) — %d position(s) captured from the "
             "screenshot\n%s\nshown P&L total: $%s%s"
             % (msg_id, len(captured), "\n".join("• " + c for c in captured),
                f"{total:+,.0f}",
                f"\n({skipped} junk row(s) skipped)" if skipped else ""), st)
    return {"rows": len(captured), "total_pnl": total, "captured": captured,
            "skipped": skipped}


LEVELS_F = HERE / "levels.json"


def store_levels(rows, msg_id, source="chart", symbol=None):
    """Persist captured levels so something can USE them later (the store was
    ledger-only before). Capped, newest kept. Never raises."""
    try:
        data = load(LEVELS_F, [])
        for lvl, role in rows:
            data.append({"ts": now(), "msg": str(msg_id), "symbol": symbol,
                         "level": float(lvl), "role": role, "source": source})
        LEVELS_F.write_text(json.dumps(data[-800:], indent=1), encoding="utf-8")
    except Exception as e:
        log(f"level store failed: {type(e).__name__}: {e}")


def recent_levels(hours=8, symbol=None):
    """Levels from the last N hours, newest first — the read side."""
    try:
        cut = time.time() - hours * 3600
        out = []
        for r in load(LEVELS_F, []):
            ts = r.get("ts") or ""
            try:
                # now() writes UTC — parse the same way, not as local
                t = calendar.timegm(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            if t < cut:
                continue
            if symbol and (r.get("symbol") or "").upper() != symbol.upper():
                continue
            out.append(r)
        return sorted(out, key=lambda r: r["ts"], reverse=True)
    except Exception:
        return []


def capture_chart_levels(text, msg_id, st=None, symbol=None):
    """A chart or dealer-level image carries LEVELS, not contracts. Keep the
    numbers when the structure has to park: 'Range H 7432.6 L 7368.6' -> an
    above and a below; 'Buy 7,378.0' -> a buy. Ledger only."""
    t = text or ""
    out = []
    mh = re.search(r"\brange\s*h\s*([\d.,]+)\s*l\s*([\d.,]+)", t, re.I)
    if mh:
        for val, role in ((mh.group(1), "above"), (mh.group(2), "below")):
            try:
                out.append((float(val.replace(",", "")), role))
            except ValueError:
                pass
    for m in re.finditer(r"\b(buy|sell)\s*(?:d\s*)?\$?([\d][\d,]*\.?\d*)", t, re.I):
        try:
            v = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        if 10 <= v <= 100000:
            out.append((v, "buy" if m.group(1).lower() == "buy" else "sell"))
    if not out:
        return []
    ledger_row({"event": "LEVELS", "msg_id": msg_id,
                "targets": "|".join(f"{k:g}={r}" for k, r in out),
                "reason": "levels pulled off a parked image", "raw": t[:80]})
    store_levels(out, msg_id, source="chart", symbol=symbol)
    log(f"msg {msg_id}: chart levels kept -> {[f'{k:g}:{r}' for k, r in out]}")
    return out


def text_levels(text):
    """Explicit level references in narrative text — '7711 is target', 'if it
    breaks 7711, much bigger drop', 'SPX bottom is 7695'. Captured so a later
    trade can be scored against his own zones (the level store)."""
    t = text or ""
    out = []
    for m in re.finditer(r"(\d{4,5}(?:\.\d+)?)\s+is\s+(?:a\s+)?"
                         r"(?:very\s+|strong\s+|major\s+|key\s+|important\s+)?"
                         r"(target|bottom|top|support|resistance)\b", t, re.I):
        out.append((float(m.group(1)), m.group(2).lower()))
    for m in re.finditer(r"\b(target|bottom|top|support|resistance)\s+is\s+"
                         r"(?:the\s+)?(\d{4,5}(?:\.\d+)?)", t, re.I):
        out.append((float(m.group(2)), m.group(1).lower()))
    for m in re.finditer(r"\b(?:breaks?|breaking|breakdown below)\s+"
                         r"(\d{4,5}(?:\.\d+)?)", t, re.I):
        out.append((float(m.group(1)), "break_trigger"))
    # 'N target again' / 'N resistance' — bare number directly before the role
    for m in re.finditer(r"(\d{4,5}(?:\.\d+)?)\s+(?:is\s+)?(?:the\s+)?"
                         r"(target|resistance|support)\b", t, re.I):
        out.append((float(m.group(1)), m.group(2).lower()))
    for m in re.finditer(r"(\d{4,5}(?:\.\d+)?)\s+next\s+"
                         r"(target|stop|support|resistance)", t, re.I):
        out.append((float(m.group(1)), "next_" + m.group(2).lower()))
    for m in re.finditer(r"\b(above|below)\s+(\d{4,5}(?:\..\d+)?)", t, re.I):
        out.append((float(m.group(2)), m.group(1).lower()))
    # futures ladder: 'S1:$28620' / 'R2: $29050' (dollar sign optional)
    for m in re.finditer(r"\b([SR][1-3])\s*:\s*\$?(\d{4,5}(?:\.\d+)?)", t, re.I):
        out.append((float(m.group(2)), m.group(1).upper()))
    for m in re.finditer(r"(\d{4,5})\s+to\s+(\d{4,5})", t, re.I):
        out.append((float(m.group(1)), "range"))
        out.append((float(m.group(2)), "range"))
    for m in re.finditer(r"\bnext\s+(target|stop|support|resistance)\s+"
                         r"(?:to|at|is)?\s*(\d{4,5}(?:\.\d+)?)", t, re.I):
        out.append((float(m.group(2)), "next_" + m.group(1).lower()))
    seen, out2 = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            out2.append(item)
    return out2


_TRADIER_INST = None


def _is_test_run():
    """D132: the suite must never touch live channels — the same argv probe
    trade_lifecycle uses (D127). Three suite runs placed six real sandbox
    orders on 2026-11-20 (all rejected, but they would fill in market hours)."""
    a = (sys.argv[0] or "").lower()
    return "test_" in a or "_test" in a or "pytest" in a


class _TestTradier:
    """D132: refusing stub — every live call fails loudly-but-safely."""

    def place(self, *a, **k):
        class R:
            ok, order_id, venue = False, None, "tradier"
            reason = "test context — live broker disabled (D132)"
            fill_price = None
        return R()

    close = place
    place_stock = place

    def cancel(self, *a, **k):
        return False, "test context"

    def order_fill(self, *a, **k):
        return None, None

    def order_quantity(self, *a, **k):
        return None


def _TRADIER():
    global _TRADIER_INST
    if _TRADIER_INST is None:
        if _is_test_run():          # D132: never build a live executor in tests
            _TRADIER_INST = _TestTradier()
            return _TRADIER_INST
        from james_exec import TradierExecutor, TradierConfig
        try:
            # venue from config (sandbox default); prod is additionally
            # gated inside place() by tradier_prod_gate (fail-closed)
            _TRADIER_INST = TradierExecutor(TradierConfig.load(tradier_env()))
        except Exception:
            _TRADIER_INST = TradierExecutor(None)
    return _TRADIER_INST


def text_legs(text):
    """Reconstruct multi-leg legs from the TEXT (deterministic). The brain
    collapses legs on a non-deterministic run (an iron fly came back 3 legs
    instead of 4), and a missing leg places an incomplete structure. The text
    is authoritative: "Sell 7725C/P, Buy 7715P n Buy 7735C" = 4 legs, where
    the C/P shorthand expands to a call AND a put at that strike.

    D47 — verb-less spread shorthand: "7420C/7435C bear calls" / "7400/7410
    bull puts" carry NO sell/buy verbs and sometimes no letters at all; the
    side word fixes both the right and the actions by convention:
      bear call spread = sell lower call, buy higher call  (credit)
      bull put  spread = buy  lower put,  sell higher put  (credit)
      bull call / bear put spreads invert (debit pairs)."""
    import re as _re
    legs = []
    pair_re = _re.compile(
        r"(\d{2,5}(?:\.\d{1,2})?)\s*([cp])?\s*/\s*(\d{2,5}(?:\.\d{1,2})?)\s*([cp])?",
        _re.I)
    for m in pair_re.finditer(text or ""):
        win = (text or "")[m.end():m.end() + 40]   # side words AFTER the pair
        sd = _re.search(r"\b(bull|bear)\s+(call|put)s?\b", win, _re.I)
        if not sd:
            continue
        # date guard: "Bear calls for 11/3" must not read 11/3 as a strike
        # pair — month/day shapes (min<=12, max<=31, whole numbers) skip.
        if (float(m.group(1)) <= 12 and float(m.group(3)) <= 31
                and float(m.group(1)).is_integer() and float(m.group(3)).is_integer()
                and min(float(m.group(1)), float(m.group(3))) <= 12):
            continue
        k1, r1 = float(m.group(1)), (m.group(2) or "").lower()
        k2, r2 = float(m.group(3)), (m.group(4) or "").lower()
        side, rt = sd.group(1).lower(), sd.group(2).lower()
        r1 = r1 or ("c" if rt == "call" else "p")
        r2 = r2 or r1
        (lo, rl), (hi, rh) = sorted([(k1, r1), (k2, r2)], key=lambda x: x[0])
        # bear = sell the lower / buy the higher (credit); bull = the inverse.
        a_lo, a_hi = ("sell", "buy") if side == "bear" else ("buy", "sell")
        l1 = {"action": a_lo, "strike": lo,
              "right": "call" if rl == "c" else "put"}
        l2 = {"action": a_hi, "strike": hi,
              "right": "call" if rh == "c" else "put"}
        legs = [l1, l2] if l1["action"] == "sell" else [l2, l1]   # sell first
        return legs          # the shorthand IS the message's spread
    cur = "buy"
    for m in _re.finditer(
            r"(sell|buy)|(\d{2,5})\s*([cp])\s*(?:/\s*([cp]))?", text or "", _re.I):
        if m.group(1):
            cur = "sell" if m.group(1).lower() == "sell" else "buy"
        elif m.group(2):
            strike = float(m.group(2))
            r1 = m.group(3).lower()
            r2 = (m.group(4) or "").lower()
            legs.append({"action": cur, "strike": strike,
                         "right": "call" if r1 == "c" else "put"})
            if r2:
                legs.append({"action": cur, "strike": strike,
                             "right": "call" if r2 == "c" else "put"})
    return legs


def text_credit(text):
    """James states the spread credit in DOLLARS on 1 contract ('collect $380'
    = $3.80 of premium). An integer with no decimal point is that dollar form
    at any magnitude; 'X to Y' ranges take the LOWER bound as the limit price
    (conservative, and matches what he has actually been filled at).
    Contract-count suffixes ('2KC', '2C') are stripped before parsing so they
    are never read as range bounds (3962: 'collect $500 - 2KC - 2C' -> 5.00).

    D114 (2026-11-19): the credit regex now lives in signal_intent.parse_credit
    (the ONE canonical lexicon). This function's old inline regex was D20's
    original fix site — the first of the drifted copies. Hard dependency, no
    shadow fallback."""
    import signal_intent as _si
    _t = str(text or "")
    # strip contract-count labels so "collect $500 - 2KC - 2C" -> "collect $500"
    _t = re.sub(r"\s*[-–]\s*\d+\s*[KC]{1,2}\b", " ", _t, flags=re.I)
    _t = re.sub(r"\s*[-–]\s*[KC]{1,2}\b", " ", _t, flags=re.I)
    return _si.parse_credit(_t)


SYMFIX = HERE / "symbol_fixes.csv"


def _sym_ok(c):
    return bool(re.fullmatch(r"[A-Z]{1,6}", c)
                or re.fullmatch(r"[A-Z]{1,5}[.\-][A-Z]{1,2}", c))


def repair_symbol(raw, text="", card=None):
    """Best-effort symbol repair: strip labels down to a symbol shape, trying
    (1) the current value, (2) a known root among the message, (3) any single
    caps token, (4) the option card's underlying. Returns (sym|None, raw)."""
    caps = [w for w in re.findall(r"\b[A-Z]{2,6}\b", text or "")
            if w not in _TICK_STOP]
    cands = [c for c in sorted(caps) if c in OPT_UNDERLYING_MAP or c in _UND_QUOTE]
    if isinstance(card, dict) and isinstance(card.get("option"), dict):
        cands.append(card["option"].get("underlying"))
    cands.append(raw)
    cands += caps
    cands.append(scope_instrument(text))
    # A VALID, KNOWN card value outranks a token found elsewhere in the text
    # (3653: 'RTY gave a Triple Chop ... enter IWM Puts' repaired IWM -> RTY
    # because RTY sorts first).
    _rawc = re.sub(r"[^A-Za-z0-9]", "",
                  (str(raw or "").strip().split() or [""])[0]).upper()
    _rawu = (str(raw or "").strip().split() or [""])[0].upper()
    # A valid, non-label raw value is the brain's own answer. The one thing that
    # beats it is a DIFFERENT ticker written against a structure ('GOLD Puts')
    # that the raw value never mentions — the card can name the wrong symbol.
    if _rawu and _rawu not in _TICK_STOP:
        _in_text = bool(re.search(r"\b" + re.escape(_rawu) + r"\b",
                                  (text or "").upper()))
        if not _in_text:
            _sc = scope_instrument(text)
            if _sc and _sc.upper() != _rawu:
                return _sc.upper(), raw
        if _rawu in OPT_UNDERLYING_MAP:      # SPX500, NDX100 ...
            return _rawu, raw
        if _sym_ok(_rawc):                   # IWM, BRK.B -> BRKB
            return _rawc, raw
    for c in cands:
        if not c:
            continue
        c = re.sub(r"[^A-Za-z0-9]", "", str(c).strip().split()[0]).upper()
        if c and _sym_ok(c):
            return c, raw
    return None, raw


def _log_symfix(msg_id, old, new):
    try:
        newf = not SYMFIX.exists()
        with SYMFIX.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if newf:
                w.writerow(["ts", "msg_id", "was", "fixed_to"])
            w.writerow([now(), msg_id, str(old), str(new)])
    except OSError:
        pass


TIER_BADGE = {"bankable": "🟢 BANKABLE", "middle": "🟡 MIDDLE", "high": "🔴 HIGH"}


def tier_of(*, defined, legs_n, short, lotto, quality, dte, itm):
    """Risk tier for the promotion ladder (user 2026-11-06): only the bankable
    class may reach real funds first. bankable = DEFINED-risk credit spread,
    both strikes OTM at entry, credit/width >= 55%, expiry >= 1 day out.
    high = lotto, naked short, or entered already ITM. middle = the rest."""
    if lotto or (short and not defined) or itm:
        return "high"
    if not defined:
        return "middle"          # long single option: capped, but full premium
    if legs_n >= 2 and quality and quality >= 0.55 and (dte or 0) >= 1:
        return "bankable"
    return "middle"


def tradier_env():
    """Active Tradier venue: 'sandbox' (default = paper) | 'prod' (real funds).
    This decides WHERE orders go; tradier_prod_gate decides WHETHER prod may."""
    return str(CFG.get("tradier_env") or "sandbox").lower()


def tradier_prod_gate(rec):
    """REAL-FUNDS gate (promotion-ladder doctrine) — fail-closed, returns
    (ok, why). Prod may place only when BOTH hold:
      1. the SPX milestone verdict is PASS and fresh (spx_gate.json <= 96h,
         written by ticker_report; refreshed daily by JamesSpxGate);
      2. the ORDER is bankable class: 2-leg same-right CREDIT vertical,
         quality >= 55%, dte >= 1, short strike OTM vs the live spot.
    An exception in here must never open the door — callers fail closed."""
    import json as _json
    import time as _time
    gf = HERE / "spx_gate.json"
    if not gf.exists():
        return False, "no gate verdict (run ticker_report)"
    try:
        g = _json.loads(gf.read_text(encoding="utf-8"))
    except Exception:
        return False, "gate verdict unreadable"
    if not str(g.get("verdict") or "").startswith("PASS"):
        return False, (f"milestone {g.get('verdict')} "
                       f"({g.get('w')}/{g.get('n')})")
    if _time.time() - float(g.get("ts") or 0) > 96 * 3600:
        return False, "gate verdict stale (>96h)"
    legs = [l for l in (getattr(rec, "legs", None) or [])
            if getattr(l, "strike", None)]
    if len(legs) != 2:
        return False, "bankable class = 2-leg vertical only"
    if str(getattr(rec, "direction", "")) != "short":
        return False, "bankable class = credit (short) spreads only"
    rights = {str(getattr(l, "right", "") or "")[:1].lower() for l in legs}
    if rights not in ({"c"}, {"p"}):
        return False, "same-right vertical only"
    ks = sorted(float(l.strike) for l in legs)
    width = ks[1] - ks[0]
    if width <= 0:
        return False, "zero width"
    try:
        quality = abs(float(getattr(rec, "credit", 0) or 0)) / width
    except (TypeError, ValueError):
        return False, "no credit to grade"
    if quality < 0.55:
        return False, f"quality {quality:.2f} < 0.55"
    try:
        dte = (_date.fromisoformat(str(getattr(rec, "expiry", ""))[:10])
               - us_today()).days
    except (ValueError, TypeError):
        return False, "bad expiry"
    if dte < 1:
        return False, "dte < 1 (0DTE not bankable)"
    spot = _under_quote(getattr(rec, "underlying", None) or "")
    if not spot:
        return False, "no spot to prove OTM"
    r = rights.pop()
    # credit geometry: we SELL the lower call / the higher put
    sell_k = ks[0] if r == "c" else ks[1]
    otm = (sell_k > float(spot)) if r == "c" else (sell_k < float(spot))
    if not otm:
        return False, f"short strike {sell_k:g} ITM vs spot {spot:g}"
    return True, "bankable + milestone PASS"


def credit_floor(text):
    """Explicit sell-limit floor: 'Put limit at $300' -> 3.00. Integer dollar
    form is per-contract dollars/100; a decimal is taken as-is. None if absent."""
    m = re.search(r"\blimit(?: price)?\s*(?:at|@|of)?\s*\$?\s*(\d+(?:\.\d+)?)", text or "", re.I)
    if not m:
        return None
    v = m.group(1)
    f = float(v)
    val = (f / 100.0) if ("." not in v and f >= 1) else f
    return round(val, 2) if 0.02 <= val <= 50 else None


def text_spread(text):
    """Deterministic multi-leg parser for James's leg-writing styles:
      a) explicit rights:   'Sell QQQ 697P n Buy 695P n collect $91'
      b) elided short right:'Sell SPX 7610/Buy 7600P n collect $380'
         (a leg with no written right inherits from the paired leg — valid
         only when EXACTLY ONE right kind appears in the whole span)
      c) double-right:      'Sell 7725C/P' = sell 7725C AND sell 7725P.
    Legs are read ONLY between the first sell/buy verb and the first
    end-marker (collect/net/credit/for/exp/stop/target/bottom/top/'at <n>'),
    so level mentions like 'SPX bottom is 7695' or premiums like 'at 3.2'
    never become phantom legs.
    Returns (legs, flags); a leg keeps right=None when no single explicit
    right exists anywhere to inherit from."""
    t = (text or "").lower()
    # NO-VERB form: "Spx lotto 50/60 bull puts" / "7420C/7435C bear calls" /
    # "the bear call spread 7415/7425" — a strike pair plus a side word, words
    # before or after. The side word is the direction (bear = sell lower / buy
    # higher; bull = the inverse) and supplies the right when letters are
    # omitted. Date-shaped pairs are refused ("Bear calls for 11/3" is a date,
    # not strikes). Verbs win when present — the explicit form parses below.
    _pair = re.compile(
        r"(\d{2,5}(?:\.\d{1,2})?)\s*([cp])?\s*/\s*(\d{2,5}(?:\.\d{1,2})?)\s*([cp])?",
        re.I)
    for pm in _pair.finditer(t):
        # side words bind AFTER the pair only ("7400/7410 bull puts"); a
        # words-before inventory ("Bear calls 7700/7710 from yesterday") is a
        # hold re-statement and must stay unparsed (3904).
        win = t[pm.end():pm.end() + 40]
        sd = re.search(r"\b(bull|bear)\s+(call|put)s?\b", win)
        if not sd or re.search(r"\b(sell|buy)", t):
            continue
        k1, k2 = float(pm.group(1)), float(pm.group(3))
        if (k1 <= 12 and k2 <= 31 and k1.is_integer() and k2.is_integer()
                and min(k1, k2) <= 12):
            continue                      # M/D date, not a strike pair
        side, rt = sd.group(1), sd.group(2)
        r1 = (pm.group(2) or "").lower() or rt
        r2 = (pm.group(4) or "").lower() or rt
        (lo, rl), (hi, rh) = sorted([(k1, r1), (k2, r2)], key=lambda x: x[0])
        a_lo, a_hi = ("sell", "buy") if side == "bear" else ("buy", "sell")
        l1 = {"action": a_lo, "strike": lo,
              "right": "call" if rl.startswith("c") else "put"}
        l2 = {"action": a_hi, "strike": hi,
              "right": "call" if rh.startswith("c") else "put"}
        return ([l1, l2] if l1["action"] == "sell" else [l2, l1], [])  # sell first
    m_start = re.search(r"\b(sell|buy)", t or "")
    if not m_start:
        return [], []
    m_end = re.search(
        r"\b(?:collect|net|credit|for\b|exp\b|expiry|stop|target|bottom|top\b)"
        r"|\bat\s+\d", t[m_start.end():])
    span = t[m_start.start():m_start.end() + m_end.start()] if m_end \
        else t[m_start.start():]
    # TICKET NOISE: "SELL TO OPEN 25 SPXW 7630.000 C" puts the QUANTITY where a
    # strike belongs, so 25 became a leg (3955/3956/3964 -> 4 legs, two bogus,
    # the venue answered Undefined symbol). Drop the qty; also drop thousands
    # separators so "7,420.60" stays one number.
    span = re.sub(r"\b(?:sell|buy)\s+to\s+(?:open|close)\s+\d+\b",
                  lambda m: m.group(0).split()[0], span)
    span = re.sub(r"(?<=\d),(?=\d{3}\b)", "", span)
    # a slash DATE is not a slash strike pair: "10/30/26" read as strikes
    # 10, 30, 26 and parked the trade on unlisted strikes
    span = re.sub(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", " ", span)
    span = re.sub(r"(?<=\bfor\s)\d{1,2}/\d{1,2}\b", " ", span)
    span = re.sub(r"(?<=\bexp\s)\d{1,2}/\d{1,2}\b", " ", span)
    span = re.sub(r"(?<=\bexpiry\s)\d{1,2}/\d{1,2}\b", " ", span)
    legs = []                                 # legs with an explicit right
    pending = []                              # (action, strike) awaiting a right
    cur = "buy"
    for m in re.finditer(
            r"(selling|buying|sell|buy)|(\d{2,5}(?:\.\d+)?)\s*"
            r"([cp])?(?:\s*/\s*([cp]))?(?![\d.])(?![mhd]\b)", span):
        if m.group(1):
            cur = "sell" if m.group(1).startswith("sell") else "buy"
        elif m.group(2):
            strike = float(m.group(2))
            r1 = (m.group(3) or "").lower()
            r2 = (m.group(4) or "").lower()
            if r1:
                legs.append({"action": cur, "strike": strike,
                             "right": "call" if r1 == "c" else "put"})
                if r2:
                    legs.append({"action": cur, "strike": strike,
                                 "right": "call" if r2 == "c" else "put"})
            else:
                pending.append({"action": cur, "strike": strike, "right": None})
    rights = {l["right"] for l in legs}
    if len(rights) == 1:                      # exactly one written right -> inherit
        inherited = rights.pop()
        for p in pending:
            p["right"] = inherited
    elif (not rights and len(pending) == 2
          and {p["action"] for p in pending} == {"sell", "buy"}
          and not re.search(r"\bcollect|credit\b", t)):
        # 'Read this as Sell 7705 n buy 7715 for today' — a DEBIT vertical;
        # sell-lower/buy-higher is coherent only as calls. ONLY a coherent
        # 2-leg sell/buy vertical gets this default (D48): prose like
        # 'we got Sell in 15m so 7600 and 7590 is target' must leave its
        # bare numbers unrighted (flagged unknown_right), never traded.
        for p in pending:
            p["right"] = "call"
    legs.extend(pending)
    return legs, []


def text_struct(text):
    """(legs, flags, credit) for ROUTING: text_spread legs plus consistency
    checks. A stated 'collect' contradicts the legs when the sold strike sits
    on the wrong side (credit puts must sell the HIGHER strike, credit calls
    the LOWER) — James's typo pattern (msg 3681: 'Sell SPX 7605/Buy 7695P n
    collect' is a debit by the legs). Flag it; never auto-trade it."""
    legs, _ = text_spread(text)
    flags = []
    cr = text_credit(text)
    if len(legs) < 2:
        flags.append("single")
    if any(l["right"] is None for l in legs):
        flags.append("unknown_right")
    if len(legs) == 2 and cr:
        s = [l for l in legs if l["action"] == "sell"]
        b = [l for l in legs if l["action"] == "buy"]
        if len(s) == 1 and len(b) == 1 and s[0]["right"] == b[0]["right"]:
            ok = (s[0]["strike"] > b[0]["strike"]) if s[0]["right"] == "put" \
                else (s[0]["strike"] < b[0]["strike"])
            if not ok:
                flags.append("inconsistent")
    return legs, flags, cr


def resolve_qty(*vals, ceiling=None):
    """Safest-qty rule: quantity is the ONLY lever that scales a right trade
    into a big loss, and James's text never carries it (credits are stated per
    contract). Ambiguity -> 1 contract (min lot, bounded defined-risk loss).
    When a source states qty it wins, clamped to [1, ceiling] (default
    max_qty) so a vision misread cannot inflate size.

    D72/D74: the ceiling applies to OUR ORDERS. Book-capture paths (recording
    HIS positions from a portfolio screenshot) pass ceiling=999 so the record
    reflects what he actually holds — capping his book would corrupt the
    position ledger the exits depend on."""
    lim = int(CFG.get("max_qty", 10)) if ceiling is None else int(ceiling)
    for v in vals:
        try:
            q = abs(int(float(v)))
        except (TypeError, ValueError):
            continue
        if q >= 1:
            return max(1, min(lim, q))
    return 1


def _legs_sig(legs):
    """Hashable identity of a structure's legs: (strike, right-initial,
    action-initial) per leg, order-insensitive. Accepts both parser dicts and
    book tuples [(strike, right, action), ...]. Used to recognise a re-send of
    a structure we already hold."""
    out = set()
    for l in legs or []:
        try:
            if isinstance(l, dict):
                strike, right, action = l.get("strike"), l.get("right", "c"), l.get("action", "buy")
            else:
                strike, right, action = l[0], l[1], l[2]
            out.add((float(strike), str(right).lower()[:1], str(action).lower()[:1]))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    return frozenset(out)


ADD_LANG = re.compile(
    r"\b(add(?:s|ed|ing)?|again|double(?:d|s)?|re-?enter(?:ed|ing)?|re-?load(?:ed|ing)?)\b",
    re.I)


def resolve_same_structure(text, st, msg_id):
    """D116 (2026-11-19): 'enter the SAME bull puts again' carries no fresh
    strike/premium — it points BACK at a structure we already hold or held.
    Resolve it strictly and return (card, reason) or None.

    Strictness is the whole point: a back-reference is only tradeable when it
    matches ONE recent structure on (canonical ticker, side, right), and that
    structure is a defined-risk 2-leg vertical. Ambiguity (multiple matches)
    or a non-vertical (naked single) resolves to None -> the caller falls back
    to OPTION_IDEA (never guesses).

    Search order: open positions first (an ADD to something we hold), then
    recently closed (a RE-ENTRY after taking gains)."""
    import signal_intent as _si
    br = _si.back_reference(text)
    if not br:
        return None
    side, right = br.get("side"), br.get("right")
    want_tk = br.get("ticker")
    if not want_tk:
        return None

    def _legs_of(t):
        legs = t.get("legs") or []
        if not legs:
            return []
        # book legs are [strike, right, action] tuples
        return [(float(l[0]), str(l[1])[:1].lower(), str(l[2]).lower())
                for l in legs if isinstance(l, (list, tuple)) and len(l) >= 3]

    def _side_of(legs):
        # same convention as park_direction: sold higher put / bought lower put = bull
        sold = [k for k, r, a in legs if a == "sell"]
        bought = [k for k, r, a in legs if a == "buy"]
        if sold and bought:
            return "bull" if sold[0] > bought[0] else "bear"
        return None

    def _matches(t):
        tk = _canon_underlying(t.get("instrument")) or t.get("instrument")
        if tk != want_tk:
            return None
        legs = _legs_of(t)
        if len(legs) != 2:
            return None                       # defined-risk only
        # rights must be the wanted right on both legs ('put' vs 'p' normalised)
        want_right = (right or "put")[:1]
        if not all(r == want_right for _, r, _ in legs):
            return None
        got_side = _side_of(legs)
        if got_side != side:
            return None
        desc = " / ".join(f"{a.upper()} {k:g}{r.upper()}" for k, r, a in legs)
        return desc

    # open first (an ADD), then recently closed (a RE-ENTRY)
    for pool in (st.get("open", {}), st.get("closed", {})):
        candidates = []
        for tid, t in pool.items():
            if tid.startswith(("SIM", "AD", "TB")):
                continue                      # shadow/adopted rows are not ours
            desc = _matches(t)
            if desc:
                candidates.append((t, desc))
        if not candidates:
            continue
        # D116 tie-break: the message often names a LEVEL ('entered at 7320').
        # A numeric anchor that matches one candidate's legs (a strike) breaks
        # the tie — it is the structure he actually means.
        _lvls = {int(m) for m in re.findall(r"\b(\d{4,5})\b", text or "")}
        if len(candidates) > 1 and _lvls:
            _anchored = [c for c in candidates
                         if _lvls & {int(k) for k, _, _ in _legs_of(c[0])}]
            if _anchored:
                candidates = _anchored       # narrow to the anchored set
        if len(candidates) == 1:
            t, desc = candidates[0]
            # build a re-trade card from the matched structure
            card = dict(t)
            for _k in ("id", "status", "order_id", "pnl", "exit_price",
                       "exit_ts", "fill_note", "bb_ticket", "opt_id",
                       "opened", "executor"):
                card.pop(_k, None)
            _rl = _legs_of(t)
            # D82-R/SSOT: the type comes from the SAME credit test the stop
            # policy uses — never a hardcoded 'credit' (a re-entered DEBIT
            # spread must stay debit, or the park/settle maths reads it wrong).
            _typ = "credit" if spread_is_credit(_rl) is not False else "debit"
            card["spread"] = {"type": _typ, "legs": [
                {"strike": k, "right": ("call" if r == "c" else "put"), "action": a}
                for k, r, a in _rl]}
            card["action"] = "entry"
            card["reason"] = desc
            return card, desc
        # still ambiguous after the anchor: multiple matching structures — never guess
        names = ", ".join(c[0].get("id", "?") for c in candidates[:4])
        push(f"⚠️ SAME-AGAIN AMBIGUOUS (msg {msg_id}) — {len(candidates)} "
             f"matching structures ({names}); not auto-re-entering.", st)
        return None
    return None


def repair_dropped_digit(legs, under):
    """D109 (msg 4459): 'Sell 7360P n Buy 345P' — the thousands digit fell off
    '7345P'. An index root (SPX/RUT/NDX) can never carry a 3-digit strike, so a
    2-leg spread with one index-scale leg (>=1000) and one 100-999 leg is a
    typo. Pad the short leg with the long leg's leading digits to the same
    count; adopt ONLY when the candidate lands within 100 pts of the big leg
    (a real James vertical is tight) and the resulting width is sane.
    Returns the REPAIRED legs list, or None when ambiguous (caller parks)."""
    if not legs or len(legs) != 2:
        return None
    try:
        _klist = sorted(float(l["strike"]) for l in legs
                        if l.get("strike") is not None)
    except (TypeError, ValueError, KeyError):
        return None
    if len(_klist) != 2:
        return None
    _big, _small = _klist[1], _klist[0]
    if not (_big >= 1000 and 100 <= _small < 1000):
        return None
    _big_s, _small_s = str(int(_big)), str(int(_small))
    _pad = _big_s[:len(_big_s) - len(_small_s)]
    if not _pad:
        return None
    _cand = float(_pad + _small_s)
    _width = _big - _cand
    if not (0 < _width <= 100):
        return None
    out = []
    for _l in legs:
        _l = dict(_l)
        if _l.get("strike") == _small:
            _l["strike"] = _cand
            _l["repaired_digit"] = True
        out.append(_l)
    return out


def _is_lotto(text):
    return bool(re.search(r"\blotto\b|high[ -]risk", text or "", re.I))


def _add_language(text):
    """Explicit intent to scale INTO an open structure: 'add more', 'again',
    'double size', 're-enter'. A silent re-send (same structure, no add words)
    is NOT an add — that one acks without trading."""
    return bool(ADD_LANG.search(text or ""))


def spread_is_credit(legs):
    """True when the SOLD leg is the more valuable one — a credit vertical.
    D82-R/SSOT: this is the ONE credit-vs-debit test (synth_spread_stop and
    resolve_same_structure both use it), so the two can never disagree about
    which side of a vertical we are on. None when the shape is not a simple
    2-leg same-right vertical."""
    if not legs or len(legs) != 2:
        return None
    sell = [l for l in legs if str(l[2]).lower().startswith("s")]
    buy = [l for l in legs if str(l[2]).lower().startswith("b")]
    if len(sell) != 1 or len(buy) != 1:
        return None
    try:
        sk, sr = float(sell[0][0]), str(sell[0][1])[:1].lower()
        bk, br = float(buy[0][0]), str(buy[0][1])[:1].lower()
    except (TypeError, ValueError, IndexError):
        return None
    if sr != br:
        return None
    return bool(sk > bk) if sr == "p" else bool(sk < bk)


def synth_spread_stop(legs, credit):
    """D82 (2026-11-20): a credit spread with NO stated stop has no exit
    protection at all — the manage loop's stop block is guarded by
    `stop or targets`, so with both None it never runs (TL4403 bled -670
    instead of stopping out). Synthesise one at the SAME convention the
    single-leg path already uses (D87: single_stop_pct = 40% -> stop at 1.4x
    the credit), so a credit spread closes when its mark has risen 40% above
    the credit received.

    CREDIT SPREADS ONLY: the manage loop tests `mark >= stop` as a LOSS for a
    credit spread (mark rises = you pay more to unwind), but for a DEBIT
    spread the same test would fire on PROFIT. A debit spread therefore gets
    no synthesised stop here (its bounded max loss is the debit paid).

    Returns the stop mark, or None when the structure is not a simple credit
    vertical or no credit is known."""
    try:
        c = float(credit)
    except (TypeError, ValueError):
        return None
    if c <= 0 or not legs or len(legs) != 2:
        return None
    # the credit/debit test lives in ONE place (spread_is_credit) so the stop
    # policy and the re-entry card's type can never disagree
    if spread_is_credit(legs) is not True:
        return None                          # debit / not a simple vertical
    pct = float(CFG.get("single_stop_pct", 40)) / 100.0
    return round(c * (1 + pct), 4)


def open_multileg_tradier(norm, text, msg_id, st):
    """Execute a 2-4 leg structure (vertical / fly / condor) as ONE atomic
    Tradier multileg order. Park only if placement fails.
    Verified live: 4-leg iron fly places atomically (Tradier: strategy=condor)."""
    from james_core import Record as _R, MsgClass as _M, Action as _A, Leg as _L
    sp = norm.get("spread") or {}
    legs_raw = sp.get("legs") or []
    # map the instrument to the Tradier OPTION ROOT (SPX500 -> SPXW, RUT -> RUTW)
    # — the OCC symbol needs the root, not the instrument label.
    _caps = {w for w in re.findall("[A-Z]{2,5}", text or "")
             if w not in _TICK_STOP}
    # prefer a REAL symbol among caps noise (SELL/BUY/TO from tickets)
    _tok = (scope_instrument(text)
            or next((c for c in sorted(_caps)
                     if c in OPT_UNDERLYING_MAP or c in _UND_QUOTE), None)
            or (next(iter(_caps)) if len(_caps) == 1 else None))
    _fix, _raw_sym = repair_symbol(_tok or (norm.get("instrument") or ""), text, norm)
    if _fix and _raw_sym and _fix != str(_raw_sym).upper().strip():
        _log_symfix(msg_id, _raw_sym, _fix)
        log(f"msg {msg_id}: symbol repaired {_raw_sym!r} -> {_fix}")
    under = opt_underlying(_fix or "") or (_fix or "")
    # ROOT CHECK: an OCC root must be a real option root. A chart label ("PMH",
    # 4047) otherwise becomes the root and the broker answers Undefined symbol.
    # Known roots pass; an equity passes if it quotes; anything else parks here
    # with a usable reason instead of at the venue.
    if under and under not in OPT_UNDERLYING_MAP and not _under_quote(under):
        # REPAIR, do not just refuse: strike magnitude is physical, so it names
        # the instrument class even when the root is a chart label ("PMH").
        # Index-scale strikes -> the index root; equity-scale -> a ticker that
        # actually quotes in the text. Park only when neither reads.
        _rks = [float(l["strike"]) for l in legs_raw if l.get("strike")]
        _rmx = max(_rks) if _rks else 0
        _new = None
        if _rmx >= 10000:
            _new = "NDX"
        elif 1000 <= _rmx < 10000:
            _new = "SPXW"
        if not _new:
            for _cand in re.findall(r"\b[A-Z]{2,5}\b", text or ""):
                if _cand in _TICK_STOP or _cand == under:
                    continue
                if _under_quote(_cand):
                    _new = _cand
                    break
        if _new and _new != under:
            log(f"multileg msg {msg_id}: root repaired {under!r} -> {_new} on "
                f"strike scale {_rmx:g}")
            push("🔧 ROOT REPAIRED (msg %s) — %r is not an option root; strikes "
                 "say %s, trading as %s." % (msg_id, under, "%g" % _rmx, _new), st)
            ledger_row({**norm, "event": "SYMFIX", "msg_id": msg_id,
                        "reason": f"root {under!r} -> {_new} (strike scale {_rmx:g})",
                        "raw": (text or "")[:80]})
            under = _new
            norm["instrument"] = _new
        else:
            push("❓ UNKNOWN OPTION ROOT (msg %s) — %r is not a root and does not "
                 "quote. Parked; the image probably carried a chart label."
                 % (msg_id, under), st)
            capture_chart_levels(text, msg_id, st, under)
            park_signal(norm, msg_id, text,
                        f"root {under!r} is not an option root and has no quote — "
                        f"unresolvable, parked")
            log(f"multileg msg {msg_id}: unresolved root {under!r}")
            save_state(st)
            return
    # TEXT-FIRST LEGS: the text is deterministic, the brain is not (it flips
    # leg counts and elides legs across runs). If the text parses to >=2
    # fully-resolved legs they override the brain's spread.
    tl, tflags, tcredit = text_struct(text)
    if (len(tl) >= 2 and any(l["right"] is None for l in tl)
            and re.search(r"read\s+(?:this|it)\s+as", text or "", re.I)):
        _fixed = inherit_rights_from_park(tl, text, _RCV.get(str(msg_id)))
        if _fixed:
            tl = _fixed
            tflags = [f for f in tflags if f != "unknown_right"]
            ledger_row({**norm, "event": "FIXED", "msg_id": msg_id,
                        "reason": "correction message: rights inherited from the "
                                  "parked original",
                        "raw": (text or "")[:80]})
    tl, tflags, tcredit = text_struct(text)
    if len(tl) >= 2 and not any(l["right"] is None for l in tl):
        legs_raw = tl
        norm["spread"] = {"type": "credit", "legs": tl, "source": "text"}
    # 2-DIGIT STRIKES ('Spx lotto 50/60 bull puts' = 7550/7560): anchor to the
    # current index hundreds. INDEX underlyings only — an equity 62C must stay
    # 62. PARK (never guess) when no quote exists.
    if under in ("SPXW", "RUTW", "NDX") and \
            any((l.get("strike") or 0) < 100 for l in legs_raw):
        try:
            q_now = bbc().get_quote("SPX500")
        except Exception:
            q_now = None
        if not q_now:
            park_signal(norm, msg_id, text,
                        "2-digit strikes but no quote to anchor the hundreds")
            save_state(st)
            return
        base = int(float(q_now) // 100) * 100
        for l in legs_raw:
            if l.get("strike") is not None and l["strike"] < 100:
                l["strike"] = base + l["strike"]
    # DROPPED-LEADING-DIGIT REPAIR (msg 4459): 'Sell 7360P n Buy 345P' — the
    # thousands digit fell off '7345P'. A credit spread whose short leg is at
    # index scale (>=1000) but whose long leg is a 3-digit number (100-999) is
    # a typo, not a chart: an SPX/RUT/NDX option can never carry a 345 strike.
    # Pad the short leg with the long leg's leading digits to the same count
    # (7360 + 345 -> 7345); adopt ONLY when the result lands within 100 pts of
    # the short leg (a real James bull/bear put sits tight around it) AND the
    # width is not absurd for the credit. Anything ambiguous still parks.
    if under in ("SPXW", "RUTW", "NDX") and len(legs_raw) == 2:
        _before = sorted(float(l["strike"]) for l in legs_raw
                         if l.get("strike") is not None)
        _repaired = repair_dropped_digit(legs_raw, under)
        if _repaired:
            legs_raw = _repaired
            norm["spread"]["legs"] = legs_raw
            _small = _before[0] if len(_before) == 2 else 0
            _after = sorted(float(l["strike"]) for l in legs_raw)
            _cand = _after[0] if len(_after) == 2 else 0
            _big = _after[1] if len(_after) == 2 else 0
            push("🔧 DROPPED-DIGIT REPAIR (msg %s) — %g -> %g beside "
                 "%g. A %g strike cannot exist on %s; repaired to the "
                 "paired leg." % (msg_id, _small, _cand, _big, _small, under), st)
            ledger_row({**norm, "event": "FIXED", "msg_id": msg_id,
                        "reason": f"dropped-leading-digit {_small:g} -> {_cand:g}",
                        "raw": (text or "")[:80]})
            log(f"multileg msg {msg_id}: dropped-digit repair {_small:g} -> {_cand:g}")
    # CHART-IMAGE GUARD (msg 4047): chart annotations (7378, then 57.7% /
    # 1.16x / 7424.4 / 64.0) survive the 2-digit anchor as 7457.7 and 7412.44.
    # A real strike sits on a 0.5 tick; prose does not.
    _offg = [float(l["strike"]) for l in legs_raw
             if l.get("strike") is not None
             and abs(float(l["strike"]) * 2 - round(float(l["strike"]) * 2)) > 1e-9]
    if _offg:
        _sp = ", ".join("%g" % k2 for k2 in _offg)
        push("🖼️ STRIKES OFF THE TICK GRID (msg %s) — %s. Parked; the "
             "image carries chart numbers, not contracts." % (msg_id, _sp), st)
        capture_chart_levels(text, msg_id, st, under)
        park_signal(norm, msg_id, text,
                    "strikes %s are off the 0.5 tick — chart annotations read as "
                    "strikes" % _sp)
        log(f"multileg msg {msg_id}: off-grid strikes {_offg}")
        save_state(st)
        return
    # TEXT vs IMAGE (msgs 3738, 3843): the filled-order TICKET / position shot
    # is what the broker actually holds. When the text and the image disagree,
    # the text is a mistype and we would trade a DIFFERENT trade — 3738 traded
    # 7590/7600 while the ticket was 7600/7610, booked -525. DESIGN RULE: any
    # ambiguity parks rather than risks (a wrong trade is worse than a missed
    # one). Check EVERY field the ticket states: strikes, credit, size.
    _imgs = norm.get("img_strikes") or []
    _want = sorted({float(l["strike"]) for l in legs_raw if l.get("strike")})
    _reasons = []
    if _imgs and _want:
        # genuine strike conflict = neither set is a subset of the other. A
        # partial ticket (one leg) or an iron-fly ticket (more legs than the text
        # named) is NOT a conflict — only a disagreement is.
        _wset, _iset = set(_want), set(_imgs)
        if not (_wset <= _iset or _iset <= _wset):
            _reasons.append(f"strikes {_want} vs ticket {_imgs}")
    # LEG-COUNT GUARD (2026-11-18): the subset rule above deliberately allows a
    # partial ticket, but it also let a 2-leg TICKET collapse to a 1-leg trade
    # when the message was IMAGE-ONLY (msg 3883: ticket said "SELL 10 7700C /
    # BUY 10 7715C", the brain returned one long call, the subset check passed
    # and we bought a naked long that expired worthless). With NO text there is
    # nothing to cross-check, so an image-only message whose ticket names more
    # strikes than we parsed must PARK - we cannot tell which leg is missing.
    _img_legs = int(norm.get("img_legs") or 0)
    _txt_legs = len(legs_raw)
    if (_img_legs and _txt_legs and _img_legs > _txt_legs
            and len((text or "").strip()) < 20):
        _reasons.append(
            f"image shows {_img_legs} leg(s) but only {_txt_legs} parsed, and "
            f"there is no text to cross-check")
    _icr = norm.get("img_credit")
    if _icr is not None and tcredit:
        if abs(float(_icr) - float(tcredit)) > 0.15 * max(float(tcredit), 0.01):
            _reasons.append(f"credit {tcredit:g} vs ticket {_icr:g}")
    if _reasons:
        push(f"\u26a0\ufe0f TEXT/IMAGE DISAGREE (msg {msg_id}): {'; '.join(_reasons)}. "
             f"Parked — the image is what the broker holds.", st)
        park_signal(norm, msg_id, text,
                    "text disagrees with the ticket (%s) — not trading a "
                    "structure nobody ordered" % "; ".join(_reasons))
        save_state(st)
        return
    # STRIKE-SCALE CROSS-CHECK (no-ticker spreads, msg 3579): with no ticker
    # token in the text the brain's instrument is a guess; strike magnitude is
    # physical — 5-digit = NDX scale, 4-digit = SPXW scale. Equity-scale
    # strikes under an INDEX root park instead of trading a nonexistent
    # contract. A text ticker always wins over this.
    if not re.search(r"\b[A-Z]{2,5}\b", text or ""):
        ks = [float(l["strike"]) for l in legs_raw if l.get("strike")]
        if ks:
            mx = max(ks)
            if mx >= 10000 and under != "NDX":
                log(f"multileg msg {msg_id}: no ticker, 5-digit strikes — "
                    f"re-anchored {under} -> NDX")
                under = "NDX"
            elif 1000 <= mx < 10000:
                if under != "SPXW":
                    log(f"multileg msg {msg_id}: no ticker, 4-digit strikes — "
                        f"re-anchored {under} -> SPXW")
                under = "SPXW"
            elif under in ("SPXW", "RUTW", "NDX"):
                park_signal(norm, msg_id, text,
                            f"no ticker in text and strikes {min(ks):g}-{mx:g} are "
                            f"equity-scale against index root {under} — ambiguous")
                save_state(st)
                return
    # CHART-IMAGE GUARD (msg 4047): an image-only "structure" whose strikes are
    # really chart annotations (7378, then 57.7%, 1.16x, 7424.4, 64.0) reaches
    # the broker as five nonsense contracts. Against an INDEX root every strike
    # must sit inside 0.5-1.5x spot; money that far off is prose, not a strike.
    # Against a ticker-less card, compare strikes to their OWN median: a real
    # structure is tight around it, chart annotations (57.7%, 64.0) are orders of
    # magnitude adrift. Median-based so a mis-resolved instrument cannot park a
    # good trade.
    _gk = sorted(float(l["strike"]) for l in legs_raw if l.get("strike"))
    if len(_gk) >= 2:
        _med = _gk[len(_gk) // 2]
        _off = [k for k in _gk if not (0.5 * _med <= k <= 1.5 * _med)]
        if _off and _med > 0:
            _sp = ", ".join("%g" % k for k in _off)
            push("🖼️ STRIKES OFF THEIR OWN SCALE (msg %s) — %s beside a "
                 "median of %g. Parked; the image is a chart, not an order."
                 % (msg_id, _sp, _med), st)
            capture_chart_levels(text, msg_id, st, under)
            park_signal(norm, msg_id, text,
                        "strikes %s sit outside 0.5-1.5x the structure median %g — "
                        "chart annotations read as strikes" % (_sp, _med))
            log(f"multileg msg {msg_id}: strikes off median {_med} — {_gk}")
            save_state(st)
            return
    # LEG COUNT: the Tradier multileg builder sends legs[:4], so a fifth leg
    # is silently DROPPED (order books 4, the trade stores 5, the close then
    # leaves the fifth live). Park >4 rather than trade a half-structure.
    if len(legs_raw) > 4:
        push("✋ %d LEGS (msg %s) — Tradier takes 4 per order; parked rather "
             "than dropping the fifth." % (len(legs_raw), msg_id), st)
        park_signal(norm, msg_id, text,
                    f"{len(legs_raw)} legs but the venue takes 4 per multileg "
                    f"order — parked, not truncated")
        log(f"multileg msg {msg_id}: {len(legs_raw)} legs > 4 — parked")
        save_state(st)
        return
    if "inconsistent" in tflags:
        desc = " / ".join(
            f"{l['action'].upper()} {l['strike']:g}{(l['right'] or '?')[0].upper()}" for l in tl)
        push(f"🚩 SPREAD INCONSISTENT (msg {msg_id}) — parked, not traded\n"
             f"{under}: {desc}\nText says collect but the strike order implies a "
             f"debit (likely typo) — verify manually.", st)
        park_signal(norm, msg_id, text,
                    "text legs contradict the stated credit — sell/buy strikes "
                    "inverted (likely typo), not auto-traded")
        log(f"multileg msg {msg_id}: inconsistent text legs — parked")
        save_state(st)
        return
    # SPREAD-BOOK MINIMUM: a re-send of a structure we already hold is an
    # ack / possible add, NEVER a silent second position (a wrong trade is
    # worse than a missed one). James re-sends the same spread at new credits
    # (msg 3482 $425 -> msg 3548 $345). EXPLICIT ADD language ("add more",
    # "again", "double size") is a real instruction to scale in: place it and
    # merge into the open position (weighted average credit).
    add_target = None
    sig = _legs_sig(legs_raw)
    if sig:
        for tid, pos in (st.get("open") or {}).items():
            if not isinstance(pos, dict):
                continue
            if pos.get("instrument") != under:
                continue
            pst = str(pos.get("status", ""))
            if not (pst.startswith("open") or pst == "pending_entry"):
                continue
            if _legs_sig(pos.get("legs")) != sig:
                continue
            if _add_language(text):
                add_target = tid
                break
            pos.setdefault("re_sends", []).append(
                {"msg_id": msg_id, "ts": now(),
                 "credit": tcredit or norm.get("entry")})
            ledger_row({"event": "RESEND", "msg_id": msg_id, "trade_id": tid,
                        "instrument": under,
                        "reason": f"same structure already open as {tid} — "
                                  f"no duplicate opened (possible add/re-send)",
                        "raw": (text or "")[:80]})
            # D119: lifecycle — re-send joined existing trade
            try:
                import trade_lifecycle as _tlc
                _tlc.add_to_lifecycle(tid, msg_id,
                                      f"re-send of {under}", text or "")
            except Exception:
                pass
            # SHADOW-TEST (user request): paper-track what auto-trading this
            # re-send would have done, under its own SIM alias in a SEPARATE
            # namespace — never ordered, never exited, never counts toward
            # max_open. Shadow PnL > 0 at expiry = the ack policy missed a trade.
            sh_id = f"SIM{msg_id}"
            if all(l.get("strike") and l.get("right") for l in legs_raw):
                sh_exp = _signal_expiry(norm.get("expiry"), text,
                                       asof=_asof_for_msg(msg_id, st))
                sh_qty = resolve_qty(norm.get("qty"))
                st.setdefault("shadow", {})[sh_id] = {
                    "id": sh_id, "msg_id": msg_id, "instrument": under,
                    "legs": [(l.get("strike"), str(l.get("right")).lower(),
                              str(l.get("action")).lower())
                             for l in legs_raw if l.get("strike")],
                    # D51: the re-send text may carry no credit (4043: 'are
                    # nearly 100%') — borrow the matched position's entry so
                    # the expiry verdict is not a meaningless 0.00.
                    "credit": tcredit or norm.get("entry") or pos.get("entry"),
                    "qty": sh_qty,
                    "expiry": sh_exp, "pos_expiry": pos.get("expiry"),
                    "expiry_match": str(pos.get("expiry") or "") == str(sh_exp),
                    "status": "sh_open", "opened": now(),
                    "raw": (text or "")[:80]}
                ledger_row({"event": "SHADOW", "trade_id": sh_id, "msg_id": msg_id,
                            "instrument": under, "qty": sh_qty,
                            "entry": tcredit or norm.get("entry"),
                            "reason": f"SIM alias: shadow entry for acked re-send "
                                      f"(expires {sh_exp}) — tests the ack policy",
                            "raw": (text or "")[:80]})
                log(f"multileg msg {msg_id}: shadow {sh_id} entry (exp {sh_exp}) "
                    f"for acked re-send")
            log(f"multileg msg {msg_id}: duplicate of open {tid} — acked, not traded")
            push("🔁 RE-SEND/ADD? (msg %s) — same %s structure already OPEN as %s "
                 "(credit now %s). No duplicate opened — shadow-tracked as %s. "
                 "If this is an ADD, say so explicitly (e.g. 'add more')."
                 % (msg_id, under, tid, tcredit or norm.get("entry") or "?", sh_id), st)
            save_state(st)
            return
    # Tradier REJECTS a past expiry: resolve text-first ("for today"/"for 11/6")
    # D69: a QUEUED message must date from when it was SENT, not when it is
    # finally parsed — Monday's 0DTE parsed on Tuesday would become Tuesday's
    # expiry (a different, wrong trade). The queue record carries the send time.
    _asof = None
    _rec = (st.get("parse_retry") or {}).get(str(msg_id))
    if _rec:
        _at = _rec.get("seen_at") or _rec.get("at") or 0
        _asof = us_session_at(time.strftime("%Y-%m-%d %H:%M:%S",
                                            time.localtime(_at)))
    expiry = _signal_expiry(norm.get("expiry"), text, asof=_asof)
    # advisory: does this underlying report inside the trade's life?
    _earnings_advisory(under, expiry, msg_id)
    # DESIGN (2026-11-18): re-tag with the RESOLVED underlying so the arrival row
    # gains its flow verdict (arrival classified with instrument=None).
    _message_tag_on_arrival(text, msg_id, under)
    # advisory: is the tape buying downside protection against this short put?
    if legs_raw and all(str(l.get("right", "")).startswith("p")
                        for l in legs_raw)             and any(str(l.get("action")) == "sell" for l in legs_raw):
        _flow_spike_advisory(under, msg_id, expiry)
    if not expiry:
        park_signal(norm, msg_id, text, "no parseable expiry")
        save_state(st)
        return
    try:
        exp_d = _date.fromisoformat(str(expiry))
    except ValueError:
        exp_d = None
    if exp_d is None or exp_d < us_today():
        _reason = f"expiry {expiry} already past (US date)"
        park_signal(norm, msg_id, text, _reason)
        # the tape at the moment we refused it — the park is information
        _uw_snapshot_on_park(norm.get("instrument") or norm.get("_underlying")
                             or "", msg_id, _reason)
        save_state(st)
        return
    _floor = credit_floor(text)
    if _floor:
        tcredit = _floor          # sell limit is the conservative basis
    # PREMIUM GUARD: a screenshot-read credit anchors BOTH the size and the
    # P&L, and vision misread one (3.45 against a true 5.70, msg 3956).
    # Reconcile with the chain mid before anything sizes; with no quote, use
    # structural bounds and trade one contract only.
    _ks_g = [float(l["strike"]) for l in legs_raw if l.get("strike")]
    _w_g = (max(_ks_g) - min(_ks_g)) if len(_ks_g) >= 2 else 0
    if tcredit is not None and _w_g > 0:
        _mid_g = spread_credit_mid(under, expiry, legs_raw)
        if _mid_g and _mid_g > 0:
            if abs(tcredit - _mid_g) / _mid_g > 0.25:
                log(f"multileg msg {msg_id}: credit repaired {tcredit:.2f} -> {_mid_g:.2f} (chain mid)")
                push("CREDIT REPAIRED (msg %s) read %.2f, chain mid %.2f - sizing and P&L anchor off the mid." % (msg_id, tcredit, _mid_g), st)
                ledger_row({**norm, "event": "CREDITFIX", "msg_id": msg_id,
                            "reason": f"credit {tcredit:.2f} -> {_mid_g:.2f} on chain mid",
                            "raw": (text or "")[:80]})
                tcredit = _mid_g
                norm["credit_fixed"] = True
        elif tcredit >= _w_g * 0.95 or tcredit <= 0.01:
            push("CREDIT IMPLAUSIBLE (msg %s) %.2f on %g wide, no quote to verify - parked." % (msg_id, tcredit, _w_g), st)
            park_signal(norm, msg_id, text,
                        f"credit {tcredit:.2f} impossible on a {_w_g:g}-wide structure "
                        f"and no chain quote to check it")
            save_state(st)
            return
        elif norm.get("credit_from_image"):
            norm["credit_unverified"] = True
            log(f"multileg msg {msg_id}: image credit {tcredit:.2f} unverified (no chain)")
    # D147 (2026-11-22): a TEXT-stated entry the spot already contradicts is a
    # stale post-move paste, not a typo. Msg 4524 said "buy 7200/7220 for
    # $13.8" while SPX sat 7,242.95 — the package's own floor was 20.00 — so
    # the buy limit rested 6 points under the market for 5h and expired
    # (Tradier: avg_fill 0.0). Reference = the price the order will actually
    # go out at (tcredit, else norm.entry — the live 4524 path). CREDIT
    # packages are exempt by SHAPE: a sell limit fills when the market rises
    # (at a better price), and 3956's vision-misread repair is a credit case.
    _ref_g = float(tcredit or norm.get("entry") or 0)
    if _ref_g > 0 and not norm.get("credit_fixed"):
        try:
            _b = next(l for l in legs_raw
                      if str(l.get("action", "")).lower() == "buy")
            _s = next(l for l in legs_raw
                      if str(l.get("action", "")).lower() == "sell")
            _is_debit_buy = (
                (str(_b.get("right") or "c")[0].lower() == "c"
                 and float(_b["strike"]) < float(_s["strike"]))
                or (str(_b.get("right") or "c")[0].lower() == "p"
                    and float(_b["strike"]) > float(_s["strike"])))
        except (StopIteration, KeyError, TypeError, ValueError):
            _is_debit_buy = False
        _spot_g = _under_quote(under)
        if _is_debit_buy and _spot_g and _w_g > 0:
            _fl_g = spread_intrinsic(legs_raw, _spot_g)
            if _fl_g is not None and _fl_g > _ref_g * 1.15:
                _pct_g = (_fl_g / _ref_g - 1) * 100
                push("🛑 STALE ENTRY (msg %s) — his %.2f vs the package's own "
                     "floor %.2f at spot %g (+%.0f%%): the market already ran, "
                     "not entering." % (msg_id, _ref_g, _fl_g,
                                        _spot_g, _pct_g), st)
                park_signal(norm, msg_id, text,
                            f"stale entry: stated {_ref_g:.2f} sits "
                            f"{_pct_g:.0f}% below the spread's intrinsic floor "
                            f"{_fl_g:.2f} at spot {_spot_g:g} — post-move paste")
                save_state(st)
                return
    # D77: compare the REQUESTED size against the ticket BEFORE our ceiling is
    # applied. Comparing post-cap made the "exceeds" branch unreachable (both
    # sides were 1) — the guard could never fire. The semantics are now exact:
    #   requested > ticket  -> the TEXT asks for more than the ticket holds: park
    #   requested <= ticket -> safe; apply our ceiling on top
    _requested = None
    try:
        _requested = int(float(norm.get("qty"))) if norm.get("qty") else None
    except (TypeError, ValueError):
        _requested = None
    qty = resolve_qty(norm.get("qty"))
    _iq = norm.get("img_qty")
    _cmp = _requested if _requested is not None else qty
    if _iq and _cmp and int(_iq) != int(_cmp):
        if int(_cmp) < int(_iq):
            log(f"msg {msg_id}: requested qty {_cmp:g} below ticket {_iq:g} — "
                f"allowed (risks less); trading {qty:g} at the ceiling")
        else:
            push(f"⚠️ TEXT/IMAGE DISAGREE (msg {msg_id}): qty {qty:g} vs "
                 f"ticket {_iq:g}. Parked.", st)
            park_signal(norm, msg_id, text,
                        f"qty {qty:g} exceeds the ticket {_iq:g} — not "
                        f"trading a size nobody ordered")
            save_state(st)
            return
    is_lotto = _is_lotto(text)
    if is_lotto:
        qty = 1          # a lotto is a 1-contract flyer by definition
    if add_target and not norm.get("qty") and re.search(r"\bdouble", text or "", re.I):
        # "double size" is explicit: scale in by the size already held
        qty = max(1, st["open"][add_target].get("qty") or 1)
    _tq2 = re.search(r"TO OPEN\s+(\d+)", text or "", re.I)
    if _tq2:
        qty = max(1, min(int(CFG.get("max_qty", 10)), int(_tq2.group(1))))
    # QUALITY SIZING (L38): low credit/width halves the size — QQQ 716/718
    # @0.61 on 2 wide = 30.5% quality, where max loss exceeds max profit.
    try:
        ks = [float(l["strike"]) for l in legs_raw if l.get("strike")]
        width = (max(ks) - min(ks)) if len(ks) >= 2 else 0
        if (width > 0 and tcredit and (tcredit / width) <= 0.55
                and not is_lotto and not add_target):
            qty = max(1, qty // 2)
    except (TypeError, ValueError):
        pass
    if re.search(r"\bsmall size\b", text or "", re.I):
        qty = min(qty, 2)
    if norm.get("credit_unverified"):
        qty = 1    # an unchecked premium never justifies size
    legs = [_L(strike=float(l["strike"]), right=str(l.get("right", "call")).lower(),
               action=str(l.get("action", "buy")).lower(), qty=qty,
               underlying=under, expiry=expiry) for l in legs_raw if l.get("strike")]
    if not legs:
        park_signal(norm, msg_id, text, "no parseable legs")
        save_state(st)
        return
    # DIRECTION FROM ECONOMICS: a "collect" message is a CREDIT spread = short
    # premium (PnL at close = (entry - fill) x 100 x qty). The brain's direction
    # label flip-flops and booked "long" credit spreads with an inverted P&L sign.
    is_debit = bool(re.search(r"\bdebit\b", text or "", re.I))
    _econ_legs = legs_raw
    _econ_text = bool(re.search(r"\bcollect|credit\b", text or "", re.I))
    if (not _econ_text and not is_debit and len(_econ_legs) == 2
            and len({str(l.get("right")) for l in _econ_legs}) == 1):
        # a card-only spread (no credit word, no strikes in the text — msg 3284)
        # still has physical geometry: calls sell<buy and puts sell>buy are
        # CREDITS. Decide on that, never on the brain's label.
        try:
            _s = [float(l["strike"]) for l in _econ_legs if str(l.get("action")) == "sell"]
            _b = [float(l["strike"]) for l in _econ_legs if str(l.get("action")) == "buy"]
            if _s and _b:
                _call = str(_econ_legs[0].get("right")).startswith("c")
                is_debit = (_s[0] > _b[0]) if _call else (_s[0] < _b[0])
        except (KeyError, TypeError, ValueError):
            pass
    dirn = ("long" if is_debit else
            "short" if (tcredit or re.search(r"\bcollect\w*\b", text or "", re.I)
                        or any(getattr(l, "action", None) == "sell" for l in legs))
            else norm.get("direction", "long"))
    rec = _R(msg_id=msg_id, ts=now(), cls=_M.FLY if len(legs) >= 4 else _M.VERTICAL,
             action=_A.ENTRY, raw=(text or "")[:200], underlying=under,
             legs=legs, credit=tcredit or norm.get("entry") or sp.get("net"), qty=qty,
             expiry=expiry, direction=dirn)
    # ENTRY-TIME ITM GUARD (policy, 2026-11-04): a CREDIT vertical whose short
    # strike is already ITM at fill behaves like being long the index — 44%% of
    # width of credit against the rest in risk, and it looks fine on the
    # quality metric because intrinsic inflates the credit. TL3732/3738 expired
    # at max loss this way. Park + alert (re-send can re-qualify). Long-term
    # plan: auto-shift the ladder to the nearest OTM strikes.
    _g_ks = [float(l["strike"]) for l in legs_raw if l.get("strike")]
    _g_w = (max(_g_ks) - min(_g_ks)) if len(_g_ks) >= 2 else 0
    _g_q = (tcredit / _g_w) if (_g_w and tcredit) else 0.0
    try:
        _g_dte = (_date.fromisoformat(str(expiry)) - us_today()).days
    except ValueError:
        _g_dte = 0
    # only SHORT-DATED or LOW-QUALITY ITM spreads park — a 70%%-credit spread
    # with weeks to run has room to recover (GD 390/380 11/20 at 69.8%%)
    if (CFG.get("itm_guard", True) and tcredit and len(legs) == 2
            and len({str(l.get("right")) for l in legs_raw}) == 1
            and (_g_dte <= 2 or _g_q < 0.55)):
        _sh = [l for l in legs_raw if str(l.get("action")) == "sell"]
        if _sh:
            _S = _under_quote(under)
            if not _S:
                time.sleep(1.5)          # one retry; quotes blip, trades don't
                _S = _under_quote(under)
            if not _S:
                # no spot = no drift check. 4055 (short 17 pts ITM, 0DTE) rode
                # out unguarded and lost 390: park instead of guessing.
                push("🟠 ITM GUARD UNAVAILABLE (msg %s) — no spot for %s, so the "
                     "short strike was not checked for drift. Parked, not sent; "
                     "re-send once quotes are back." % (msg_id, under), st)
                park_signal(norm, msg_id, text,
                            f"ITM guard could not get a spot for {under} — "
                            f"unchecked, parked")
                save_state(st)
                return
            if _S:
                _strike = float(_sh[0]["strike"])
                _buf = _S * (CFG.get("itm_buffer_pct", 0.05) / 100.0)
                _right = str(_sh[0].get("right"))
                _itm = (_strike > _S + _buf) if _right.startswith("p") \
                    else (_strike < _S - _buf)
                # CUSHION: OTM alone is not an edge — a short one point from
                # the money is still a coin flip at 0DTE. Measure the distance
                # from spot to BREAK-EVEN (strike +/- credit) and require at
                # least one credit of room (CFG itm_cushion_credits).
                _be = (_strike + tcredit) if _right.startswith("c")                     else (_strike - tcredit)
                _cushion = (_be - _S) if _right.startswith("c") else (_S - _be)
                _need = float(CFG.get("itm_cushion_credits", 1.0)) * tcredit
                if _cushion + 1e-9 < _need:   # exact-boundary safe
                    if _right.startswith("c"):
                        # D44 SHIRNK-NOT-PARK (user doctrine: risk rules shrink
                        # size/move exits, NEVER park flow). A short CALL vertical
                        # is defined-risk bearish, and the parked outcomes show
                        # guard-refused bearish defined-risk went 28W/5L in this
                        # window — take it at 1 lot instead of missing it. The
                        # bullish side (short put vertical) lost money as a band
                        # and keeps the park (watch-only, user 2026-11-13).
                        # Real size beyond 1 lot stays gated on band graduation.
                        qty = 1
                        rec.qty = 1
                        for _l in legs:
                            try:
                                _l.qty = 1
                            except Exception:
                                pass
                        push("🟡 THIN CUSHION (msg %s): break-even %.2f is only "
                             "%.1f pt from spot %.2f — bearish band, SHRUNK to 1 "
                             "lot and sent instead of parked (shrink-not-park, D44)."
                             % (msg_id, _be, _cushion, _S), st)
                        log("itm_guard shrink-not-park %s: thin cushion bearish "
                            "be=%.2f spot=%.2f -> 1 lot" % (msg_id, _be, _S))
                    else:
                        push("🟡 THIN CUSHION (msg %s): break-even %.2f is only %.1f pt "
                             "from spot %.2f, under one credit (%.2f) — parked, not "
                             "sent." % (msg_id, _be, _cushion, _S, tcredit), st)
                        park_signal(norm, msg_id, text,
                                    f"break-even {_be:,.2f} only {_cushion:.1f} pt from "
                                    f"spot {_S:,.2f} (need %.1f) — thin cushion, parked"
                                    % _need)
                        save_state(st)
                        return
                # drifting INTO the money is fine while the premium covers it —
                # 3900 was 4.1 ITM on 4.75 credit and won; 4055 was 17 ITM on
                # 4.55 and lost. Park only when intrinsic outruns the credit.
                _ipts = (_strike - _S) if _right.startswith("p") else (_S - _strike)
                if _itm and _ipts > tcredit:
                    if _right.startswith("c"):
                        # D44 (see the thin-cushion branch): bearish defined-risk
                        # shrinks to 1 lot instead of parking.
                        qty = 1
                        rec.qty = 1
                        for _l in legs:
                            try:
                                _l.qty = 1
                            except Exception:
                                pass
                        push("🟡 STRIKE DRIFT (msg %s): short %s %g is already ITM "
                             "(spot %.2f) — bearish band, SHRUNK to 1 lot and sent "
                             "instead of parked (shrink-not-park, D44)."
                             % (msg_id, _right, _strike, _S), st)
                        log("itm_guard shrink-not-park %s: %s %g ITM vs %.2f -> 1 lot"
                            % (msg_id, _right, _strike, _S))
                    else:
                        push("🟡 STRIKE DRIFT (msg %s): short %s %g is already ITM "
                             "(spot %.2f) — parked, not sent. Re-send if the desk "
                             "returns; auto-shift is on the long-term plan."
                             % (msg_id, _right, _strike, _S), st)
                        park_signal(norm, msg_id, text,
                                    f"short strike {_strike:g} ITM vs spot {_S:,.2f} "
                                    f"— strike drift, parked (itm_guard)")
                        save_state(st)
                        return
    # PRE-PLACE VALIDATION: catch the 400 causes before the broker sees them
    if not under or not _sym_ok(str(under)):
        park_signal(norm, msg_id, text, f"symbol unresolved ({under!r}) — not sent")
        save_state(st)
        return
    if not legs or (qty or 0) < 1:
        park_signal(norm, msg_id, text, "no legs or qty < 1 — not sent")
        save_state(st)
        return
    # FAR-STRIKE GUARD (short premium only, lottos exempt): IWM 270P against
    # a 245 spot is a different bet from the one the message reads as
    if CFG.get("strike_far_guard", True) and not is_lotto and dirn == "short":
        _sh = [float(l["strike"]) for l in legs_raw
               if str(l.get("action")) == "sell" and l.get("strike")]
        _sq = _under_quote(under)
        # D133 (2026-11-21): BlackBull's RUT print came back as an SPX-family
        # level — msg 4540's 2665P was called '62.7% from spot 7,151.30'
        # (7151 = SPX's own price that morning). On an INDEX chain a spot so
        # far from EVERY strike is a bad print, not a far strike: refuse the
        # comparison (the ITM/thin-cushion/no-spot paths still decide) instead
        # of running arithmetic against a number from the wrong market. Equity
        # roots keep the guard as-is — there a 2x gap can be a real lotto.
        if _sq and str(under).upper() in _INDEX_ROOTS and _sh:
            try:
                _ks_all = [float(l.get("strike")) for l in legs_raw
                           if l.get("strike")]
                if _ks_all and not (0.4 * min(_ks_all) <= float(_sq)
                                    <= 2.5 * max(_ks_all)):
                    log(f"msg {msg_id}: far-strike guard skipped — spot "
                        f"{float(_sq):,.2f} implausible vs strikes {_ks_all} "
                        f"(bad print, D133)")
                    _sq = None
            except (TypeError, ValueError):
                pass
        if _sh and not _sq:
            log(f"msg {msg_id}: far-strike guard skipped — no spot for {under}")
        if _sh and _sq:
            _far, _fpct = strike_far(
                _sq, _sh[0],
                [l.get("right") for l in legs_raw
                 if str(l.get("action")) == "sell"][0])
            if _far:
                park_signal(norm, msg_id, text,
                            f"short strike {_sh[0]:g} is {_fpct:.1f}% from spot "
                            f"{float(_sq):,.2f} (>{CFG.get('strike_far_pct')}%)")
                save_state(st)
                return
    # every leg, not just the first: one unlisted leg must not ride through
    shifted, bad = [], False
    for l in legs:
        _o, _c2, _n = _listed(under, expiry, str(l.right), l.strike)
        if _c2 and not _o:
            if _n and abs(_n - float(l.strike)) / float(l.strike) <= 0.02:
                shifted.append(f"{l.strike:g}->{_n:g}")
                _set_leg_strike(l, _n)
            else:
                bad = True
    if bad:
        park_signal(norm, msg_id, text,
                    f"strike/expiry not listed even near: {under} {expiry}")
        save_state(st)
        return
    if shifted:
        log(f"msg {msg_id}: strikes snapped to listed: {shifted}")
    try:
        if exp_d and exp_d > us_session_for_today():   # entries for a later session
            rec.duration = "gtc"
            rec.close_date = str(expiry)
    except TypeError:
        pass
    ex = _TRADIER().place(rec)
    if ex.ok:
        if add_target and add_target in st.get("open", {}):
            # EXPLICIT ADD: merge into the open position, weighted avg credit
            p = st["open"][add_target]
            old_q, old_e = p.get("qty") or 1, p.get("entry")
            new_q = old_q + qty
            if rec.credit is not None and old_e is not None:
                p["entry"] = round((old_e * old_q + rec.credit * qty) / new_q, 4)
            p["qty"] = new_q
            p.setdefault("adds", []).append(
                {"msg_id": msg_id, "qty": qty, "credit": rec.credit,
                 "order_id": ex.order_id, "expiry": expiry, "ts": now()})
            p["fill_note"] = (f"add msg {msg_id}: +{qty} @ {rec.credit} "
                              f"(order {ex.order_id}) — total {new_q}")
            ledger_row({"event": "ORDER", "trade_id": add_target, "msg_id": msg_id,
                        "instrument": under, "order_id": ex.order_id,
                        "qty": qty, "entry": rec.credit,
                        "reason": f"ADD +{qty} to {add_target} — total {new_q}",
                        "raw": (text or "")[:80]})
            log(f"multileg ADD msg {msg_id}: +{qty} to {add_target} -> {new_q} total")
            push("➕ ADDED (msg %s): %s %s — +%s @ %s (order %s)\n"
                 "position %s now %s contracts (avg credit %s)"
                 % (msg_id, under, "fly" if len(legs) >= 4 else "vertical",
                    qty, rec.credit, ex.order_id, add_target, new_q,
                    p.get("entry")), st)
        else:
            tid = f"TL{msg_id}"
            _ml_legs = [(l.strike, l.right, l.action) for l in legs]
            # D82: a credit spread with no stated stop gets one at the same
            # convention as the single-leg path (1.4x credit), so the manage
            # loop's stop block actually runs instead of being skipped.
            _ml_stop = synth_spread_stop(_ml_legs, rec.credit)
            st.setdefault("open", {})[tid] = {
                "id": tid, "msg_id": msg_id, "instrument": under,
                "direction": dirn, "entry": rec.credit, "qty": qty,
                "expiry": expiry, "status": "open_tradier", "executor": "tradier",
                "order_id": ex.order_id, "k": 1.0,
                "legs": _ml_legs,
                "structure": "fly" if len(legs) >= 4 else "vertical",
                "opened": now(), "fill_note": ex.reason, "pnl": None,
                "exit_price": None, "exit_ts": None, "targets": [],
                "stop": _ml_stop,
                "confidence": norm.get("confidence"), "horizon": norm.get("horizon"),
                "rationale": norm.get("rationale"), "symbol": under, "bb_ticket": None}
            if _ml_stop:
                log(f"multileg {tid}: synthesised stop {_ml_stop:g} "
                    f"({CFG.get('single_stop_pct', 40):g}% of credit {rec.credit})")
            t = st["open"][tid]
            _itm_hit = False
            try:
                _Sq = _under_quote(under)
                _sst = [float(l["strike"]) for l in legs_raw
                        if str(l.get("action")) == "sell"]
                if _Sq and _sst:
                    _itm_hit = (_sst[0] > float(_Sq)) if str(legs_raw[0].get("right")).startswith("p") \
                        else (_sst[0] < float(_Sq))
            except (TypeError, ValueError):
                pass
            t["tier"] = tier_of(defined=True, legs_n=len(legs), short=dirn == "short",
                                lotto=is_lotto, quality=_g_q, dte=_g_dte, itm=_itm_hit)
            # FILL VERIFICATION: acceptance is not a fill — never book a
            # position on the order acknowledgement alone (resting entries
            # used to show as open and could ride an expired session).
            time.sleep(3)
            st_o, fill = _tradier_close_status(ex.order_id)
            st_u = (st_o or "").upper()
            if "FILL" in st_u:
                rebase_levels(t, fill)
                # FILL GATE: a credit spread fill far off the CARD credit means
                # the card is misattributed or the credit was mis-read (3811
                # card 5.30 filled 8.20; 4057 card 4.55 filled 6.60). Carrying it
                # poisons stop/target/PnL. Alert only; the rebase already shifts
                # the levels, but a review is needed.
                _g_fs = [float(l["strike"]) for l in legs_raw if l.get("strike")]
                _g_cw = (max(_g_fs) - min(_g_fs)) if len(_g_fs) >= 2 else 0
                if t.get("card_entry_far") and _g_cw > 0:
                    push(f"⚠️ FILL FAR FROM CARD (msg %s): %s filled %.2f vs "
                         f"card %.2f on %.0f-wide. Stop/target/quality are "
                         f"rebased but may be mis-anchored."
                         % (msg_id, under, fill, rec.credit, _g_cw), st)
                t["fill_note"] = ex.reason + (f" | filled @ {fill}" if fill else " | filled")
                ledger_row({**norm, "event": "ORDER", "msg_id": msg_id,
                            "instrument": under, "order_id": ex.order_id,
                            "reason": t["fill_note"], "raw": (text or "")[:80]})
                log(f"multileg TRADIER placed+filled: {t['fill_note']}")
                push(("%sMULTI-LEG FILLED (msg %s): %s @ %s | order %s"
                      + chr(10) + "risk tier: %s")
                     % ("🎲 LOTTO " if is_lotto else "", msg_id, under,
                        fill or rec.credit, ex.order_id,
                        TIER_BADGE.get(t.get("tier"), t.get("tier"))), st)
                # D119: lifecycle — trade opened
                try:
                    import trade_lifecycle as _tlc
                    _tlc.open_lifecycle(tid, msg_id, under,
                                        f"filled @ {fill or rec.credit}", text or "")
                except Exception:
                    pass
            elif any(x in st_u for x in ("REJECT", "CANCEL", "EXPIRED", "FAILED")):
                t["status"], t["pnl"] = "closed", 0.0
                t["fill_note"] = f"entry order {st_u} — no position"
                st.setdefault("closed", {})[tid] = t
                st["open"].pop(tid, None)
                ledger_row({**norm, "event": "EXIT", "msg_id": msg_id,
                            "instrument": under, "order_id": ex.order_id,
                            "reason": t["fill_note"], "raw": (text or "")[:80]})
                log(f"multileg entry terminal at placement: {st_u}")
                push("⚠️ MULTI-LEG %s (msg %s) — no position taken" % (st_u, msg_id), st)
            else:
                t["status"] = "pending_entry"
                t["fill_note"] = (f"entry resting ({st_u or 'no status'}) — cancels if "
                                  f"unfilled in {CFG['max_open_retry_hours']}h")
                ledger_row({**norm, "event": "PENDING", "msg_id": msg_id,
                            "instrument": under, "order_id": ex.order_id,
                            "reason": t["fill_note"], "raw": (text or "")[:80]})
                log(f"multileg entry resting {ex.order_id} — fill check armed")
                push("%s⏳ MULTI-LEG RESTING (msg %s): %s | order %s — not filled; "
                     "cancels in %sh if still resting"
                     % ("🎲 LOTTO " if is_lotto else "", msg_id, under, ex.order_id,
                        CFG["max_open_retry_hours"]), st)
                # D119: lifecycle — trade pending
                try:
                    import trade_lifecycle as _tlc
                    _tlc.open_lifecycle(tid, msg_id, under,
                                        f"pending @ {rec.credit}, order {ex.order_id}", text or "")
                except Exception:
                    pass
    else:
        _occs = " ".join(l.occ() for l in legs)
        log(f"multileg Tradier failed -> parked: {ex.reason} | legs: {_occs}")
        park_signal(norm, msg_id, text,
                    f"Tradier placement failed: {ex.reason} (legs: {_occs})")
    save_state(st)


def ledger_row(row):
    # D41: the ts column went out blank on 3,078 of 3,087 rows — callers omit
    # it and row.get fell through to "", so every ts-window query silently
    # returned nothing. Stamp it when missing; caller-supplied ts still wins.
    row = dict(row)
    row.setdefault("ts", now())
    _led = LEDGER
    if _is_test_run() and _led.name == "signals.csv":
        _led = _led.parent / "signals.test.csv"   # D132: suite writes stay out
    new = not _led.exists()
    with _led.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(SIG_COLS)
        w.writerow([row.get(c, "") for c in SIG_COLS])


def _push_send(text):
    """Runs in its own thread: telethon.sync needs a loop-free thread."""
    if _is_test_run():                     # D132: never hit the live session
        log(f"D132 push suppressed (test): {text[:60]!r}")
        return
    from telethon.sync import TelegramClient
    env = load_env()
    with TelegramClient(str(PUSH_SESSION), int(env["TELEGRAM_API_ID"]),
                        env["TELEGRAM_API_HASH"]) as c:
        c.send_message("me", text)


# Push discipline: Saved Messages is a decision log, not a firehose. A stuck
# retry used to re-push identical text every cycle (BMRN alone: 572 copies).
# Repeats inside PUSH_REPEAT_SEC collapse, and record-only classes stay in the
# ledger/digest instead of the chat.
PUSH_REPEAT_SEC = 1800
# PREFLIGHT repeats identically on every boot; a fix session restarts the pipe
# 3-6x and Saved Messages fills with health noise. One per class window is the
# signal (user 2026-11-14: batch them). Key = the _push_key headline.
PUSH_CLASS_WINDOW = {"🩺 PREFLIGHT OK": 4 * 3600}
PUSH_QUIET = ("🫥 ", "🎯 EXIT TARGET (", "📌 OPTION IDEA",
              "🖼️ IMAGE CONTENT")


_REG_CACHE = {"rows": None, "mtime": 0.0}


def register_hit(text):
    """Match an alert/park reason against DEFECT_REGISTER.md and return the row id.
    Wired into push() so every live alert carries its defect row — the moment a
    defect is identified, the register is consulted, never re-learned."""
    rows = _REG_CACHE["rows"]
    try:
        mt = (HERE / "DEFECT_REGISTER.md").stat().st_mtime
    except OSError:
        return None
    if rows is None or mt != _REG_CACHE["mtime"]:
        rows = []
        try:
            for line in (HERE / "DEFECT_REGISTER.md").read_text(encoding="utf-8").splitlines():
                if not line.startswith("| D"):
                    continue
                c = [x.strip() for x in line.strip().strip("|").split("|")]
                if len(c) >= 6:
                    rows.append({"id": c[0], "defect": c[2].lower(),
                                 "symptom": c[3].lower(), "fix": c[4].lower()})
        except Exception:
            rows = []
        _REG_CACHE["rows"], _REG_CACHE["mtime"] = rows, mt
    if not rows:
        return None
    # Weighted word overlap against defect/symptom/fix text. Long words count
    # double and the bar is high: a WRONG row is worse than no row, because it
    # sends the post-mortem down the wrong path.
    import re as _re
    words = {w for w in _re.findall(r"[a-z]{3,}", (text or "").lower())}
    if not words:
        return None
    best, score = None, 0.0
    for r in rows:
        _t = set(_re.findall(r"[a-z]{3,}", r["defect"] + " " + r["symptom"] + " " + r["fix"]))
        hit = sum(2.0 if len(w) >= 5 else 1.0 for w in (words & _t))
        if hit > score:
            best, score = r["id"], hit
    return best if score >= 3.0 else None


def _push_key(text):
    """Identity of the EVENT: the headline before the em-dash. A different
    HTTP code for the same trade collapses; a different trade does not."""
    head = text.splitlines()[0].split(" — ")[0]
    return re.sub(r"\s+", " ", head)[:70]


def push(text, st):
    if st.get("alerts_date") != today():
        st["alerts_date"], st["alerts_today"] = today(), 0
    if st["alerts_today"] >= CFG["alert_cap_per_day"]:
        log(f"alert cap reached — logged only: {text[:60]!r}")
        return
    quiet = text.startswith(PUSH_QUIET)
    now_t = time.time()
    recent = st.get("push_recent") or {}
    recent = {k: v for k, v in recent.items()
              if now_t - v < PUSH_CLASS_WINDOW.get(k, PUSH_REPEAT_SEC)}
    key = _push_key(text)
    # the moment a defect is named, say which register row it is — so the fix
    # is looked up, not re-derived
    _hit = register_hit(text)
    if _hit and f"D{_hit[1:]}" not in text:
        text = text + f"{chr(10)}--> known defect {_hit}: see DEFECT_REGISTER.md"
    if key in recent:
        # same thing again inside its window: record it, do not ping
        st["push_recent"] = recent
        st["push_suppressed_today"] = st.get("push_suppressed_today", 0) + 1
        log(f"push suppressed (repeat): {key}")
        return
    recent[key] = now_t
    st["push_recent"] = recent
    if quiet and not CFG.get("push_quiet_off"):
        st["push_suppressed_today"] = st.get("push_suppressed_today", 0) + 1
        log(f"push quiet (record-only): {text.splitlines()[0][:60]}")
        return
    st["alerts_today"] += 1
    try:
        import threading
        t = threading.Thread(target=_push_send, args=(text,), daemon=True)
        t.start()
        t.join(90)
        log(f"pushed: {text.splitlines()[0][:70]}")
    except Exception as e:
        log(f"push failed: {type(e).__name__}: {e}")


def load_env():
    return dict(
        (k.strip(), v.strip())
        for k, _, v in (l.partition("=") for l in ENV_PATH.read_text(encoding="utf-8").splitlines())
        if k.strip() and not k.strip().startswith("#")
    )


# ---------------------------------------------------------------- hermes brain

CARD_KEYS = ("instrument", "direction", "action", "entry", "stop", "targets",
             "confidence", "horizon", "option", "trim", "spread", "rationale", "qty",
             "expiry", "pct_actual", "message_on_image")
ACTIONS = ("entry", "exit", "update", "none")

LINKY = re.compile(r"zoom\.us|youtu\.be|youtube\.com|t\.me/|https?://", re.I)
KEYWORD = re.compile(r"spx|ndx|nasdaq|spy|qqq|vix|tsla|meta|nvda|aapl|msft|amzn|"
                     r"googl|amd|nflx|coin|pltr|bear|bull|calls?|puts?|book|target", re.I)

PARSE_PREAMBLE = (
    "Parse this raw Telegram trading signal into the JSON trade card defined by "
    "your charter (AGENTS.md in this directory). Respond with ONLY the JSON object.\n\n"
    "RAW SIGNAL:\n"
)


def extract_json(text):
    """Largest balanced {...} block; prefers ```json fences when present."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1))
        except ValueError:
            pass
    best, depth_start, depth = None, None, 0
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                depth_start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and (best is None or i - depth_start > len(best)):
                best = text[depth_start:i + 1]
    if best is None:
        return None
    try:
        return json.loads(best)
    except ValueError:
        return None


def _provider_for(model):
    """D59/D70/D71: route each model through the provider that OWNS it — this
    is what makes the fallback chain genuinely independent (two separate credit
    pools, so one running dry cannot starve the other).
      qwen*/deepseek*/glm*  -> custom (Alibaba Token Plan, primary)
      google/gemini-*       -> gemini (Google key pool)
      anthropic/*, claude*  -> nous   (Nous Portal, Plus plan this month)
    An explicit `model_providers` map in config.json overrides any guess."""
    m = (model or "").lower()
    explicit = CFG.get("model_providers") or {}
    for key, prov in explicit.items():
        if key.lower() in m:
            return prov
    if "gemini" in m:
        return "gemini"
    if any(k in m for k in ("claude", "anthropic")):
        return "nous"
    if any(k in m for k in ("qwen", "deepseek", "glm")):
        return "custom"
    return CFG["hermes_provider"]


def _retry_batch(parse_retry, now=None):
    """D62: what to parse-retry this cycle. LIVE (non-stale) messages jump the
    queue — one per cycle, oldest attempt first — then up to 20 stale items in
    bulk: they skip the brain (D58) and are cheap, so the backlog drains fast
    instead of sitting in front of live signals. Legacy records (no 'stale'
    key) are storm-era and count as stale."""
    now = time.time() if now is None else now
    due = [(mid, r) for mid, r in parse_retry.items()
           if now - r.get("at", 0) >= min(300 * r.get("tries", 1), 900)]
    live = sorted((d for d in due if not d[1].get("stale", True)),
                  key=lambda x: x[1].get("at", 0))
    stale = sorted((d for d in due if d[1].get("stale", True)),
                   key=lambda x: x[1].get("at", 0))
    # up to 5 live per cycle: the paid plan absorbs the burst and a batch of
    # live arrivals must clear in minutes, not an hour (1/cycle starved 4387).
    return live[:5] + stale[:20]


def hermes_parse(text, photo=None):
    """Raw signal -> trade card dict, or None (dropped). Runs the hermes CLI oneshot
    against the configured Nous portal model, falling back down the model list.
    NO LOCAL MODELS (user directive 2026-11-16): on-box model loading crashed
    this machine once — the cloud chain only, and the queue-for-open absorbs
    any cloud outage as latency, never as a lost signal."""
    QUERY_FILE.write_text(PARSE_PREAMBLE + text, encoding="utf-8")
    models = [CFG["hermes_model"]] + list(CFG.get("hermes_fallback_models") or [])
    _routes = [(CFG["hermes_provider"], models[0], None)]
    for m in models[1:]:
        _routes.append((_provider_for(m), m, None))
    # FAST PATH: if the TEXT already carries the trade (a real command + structure),
    # parse WITHOUT the image — that avoids a vision call (~38s) and trades fast.
    # Image-only / short-text messages still get --image (the info is in the pic).
    use_img = bool(photo and Path(photo).exists()) and len((text or "").strip()) < 50
    _dead_providers = set()
    for _prov, model, _base in _routes:
        # D64: a provider-level failure (402/429) sinks its whole family —
        # stepfun rides nous and used to burn a 180s timeout per parse after
        # nous was already known dead. One dead provider, one attempt.
        if _prov in _dead_providers and _base is None:
            continue
        cmd = ["cmd", "/c", CFG["hermes_bin"], "chat",
               "--query-file", str(QUERY_FILE), "-Q", "--in", str(HERE),
               "--provider", _prov, "-m", model]
        if use_img:
            cmd += ["--image", str(photo)]
        _to = CFG["hermes_timeout"]
        # D57: stdout/stderr go to TEMP FILES, never pipes. `cmd /c hermes`
        # spawns grandchildren that inherit pipe handles; on timeout the kill
        # cannot reap them and the WHOLE pipeline hangs forever (both 11-16
        # startups froze here with no heartbeats). File handles never block.
        import tempfile
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8",
                                    errors="replace") as _of, \
             tempfile.TemporaryFile(mode="w+", encoding="utf-8",
                                    errors="replace") as _ef:
            try:
                r = subprocess.run(cmd, stdout=_of, stderr=_ef,
                                   timeout=_to, cwd=str(HERE))
            except subprocess.TimeoutExpired:
                log(f"hermes timeout after {_to}s on {model}")
                _dead_providers.add(_prov)
                continue
            _of.seek(0)
            out = _of.read().strip()
            _ef.seek(0)
            err_txt = _ef.read()
        card = None
        if r.returncode == 0 and out:
            card = extract_json(out)
        if card is not None:
            return card
        log(f"hermes model {model} failed rc={r.returncode}: "
            f"{(err_txt or out or '')[-160:]}")
        if _base is None:
            _dead_providers.add(_prov)
    return None


def validate_card(card, text=""):
    """(ok, reason, normalized card). Strict on direction; lenient on levels."""
    if not isinstance(card, dict):
        return False, "not a dict", None
    direction = (card.get("direction") or "flat").lower()
    if direction not in ("long", "short", "flat"):
        return False, f"bad direction {direction!r}", None
    action = (card.get("action") or "entry").lower()
    if action not in ACTIONS:
        action = "entry"
    instrument = card.get("instrument")
    # salvage: a card that names only an option leg still has an underlying
    if not instrument and isinstance(card.get("option"), dict):
        instrument = card["option"].get("underlying") or None
    _ts = [int(float(x)) for x in (text_strikes(text) or [])]
    if action == "entry" and direction != "flat" and not instrument and (
            entry_language(text) or has_tradeable_content(text)):
        if any(len(str(x)) >= 4 for x in _ts):
            instrument = "SPX"      # bare 4-5 digit strikes are an index signal
        else:
            # no command+content path: a REAL entry must name an instrument
            return False, "entry signal but no instrument", None
    num = lambda v: float(v) if isinstance(v, (int, float)) and v == v else None
    norm = {k: card.get(k) for k in CARD_KEYS}
    norm.update(direction=direction, action=action, instrument=instrument,
                _raw_text=(text or "")[:400],
                expiry=(str(card["expiry"]) if card.get("expiry") else None),
                entry=num(card.get("entry")), stop=num(card.get("stop")),
                credit_from_image=bool(card.get("_credit_from_image")),
                targets=[t for t in (num(x) for x in card.get("targets") or []) if t],
                confidence=card.get("confidence", ""),
                horizon=(card.get("horizon") or "unclear"),
                rationale=(card.get("rationale") or "")[:120])
    # option contract (premium-level signal) — executed on Tiger paper
    opt = card.get("option")
    norm["option"] = None
    if isinstance(opt, dict) and num(opt.get("strike")):
        # CROSSED CARD (msg 4004: a CHTR card arrived with WPM's whole text):
        # the ticker on the card never appears in the text. No deterministic
        # repair without the image — flag it so the review sees it.
        right = "call" if str(opt.get("right", "")).lower().startswith("c") else "put"
        norm["option"] = {"underlying": str(opt.get("underlying") or instrument or "").upper(),
                          "strike": num(opt.get("strike")), "right": right,
                          "expiry": opt.get("expiry") or None}
    if norm["option"]:
        _toks = {w for w in re.findall(r"\b[A-Z]{2,5}\b", text or "")
                 if w not in _TICK_STOP}
        if _toks and str(norm["option"]["underlying"] or "").upper() not in _toks:
            norm["crossed_card"] = True
    try:
        trim = int(card.get("trim"))
        norm["trim"] = trim if 0 < trim < 100 else None
    except (TypeError, ValueError):
        norm["trim"] = None
    # multi-leg structures are NOT single-contract trades: report, never execute
    sp = card.get("spread")
    norm["spread"] = sp if isinstance(sp, dict) and sp.get("legs") is not None else None
    # TEXT-FIRST GUARD: the brain is non-deterministic and will FABRICATE legs
    # (msg 3458 "we are holding Bear calls" came back as 7735C/7745C, borrowed
    # from the previous message). If the text names no strike, there is no
    # structure — discard the invented legs and fall through to narrative.
    # EXCEPTION (msg 3284): a message that DOES carry trade intent (a buy/sell
    # or a family word) and whose card supplies a complete coherent 2-leg
    # structure is trusted, flagged _unverified. The 3458 status message has
    # neither intent nor geometry, so it still falls through.
    if norm["spread"]:
        ts = text_strikes(text)
        _legs_c = norm["spread"].get("legs") or []
        _coherent = (len(_legs_c) == 2
                     and len({str(l.get("action")) for l in _legs_c}) == 2
                     and len({str(l.get("right")) for l in _legs_c}) == 1
                     and all(l.get("strike") for l in _legs_c))
        # intent = an entry verb, a family word, or simply NOT a status/hold
        # message. 3458 is "we are holding Bear calls ... we will see a big
        # drop" — pure status, its fabricated legs must still die. 3284 is
        # "SPX moved over 7665+ ... we should make gains with bot" — no verb,
        # no family, but not a status either, and its card was correct.
        _status = bool(re.search(
            r"\b(?:are\s+)?holding\b|\bstill\s+holding\b|\bwe\s+will\s+see\b"
            r"|\bholding\s+all\b", text or "", re.I))
        # D134 (2026-11-21): a bare '<n>% up' mark is a STATUS even when it
        # names a family — '10% up' (4535) and '30% up on bull puts' (4522)
        # report on positions JAMES already holds; the image beside them is
        # HIS position screenshot (qty -8, avg credit 6.60). Mirroring them
        # entered AFTER the move: TL4535 -100, TL4522 -50 on the same
        # 7315/7300 legs as TL4520. An entry names a verb (4534 'buy WMT')
        # or carries a ticket; a % mark without one never carries intent —
        # D121's bare-% rule, now enforced on the ENTRY side too.
        _pct_status = (bool(re.search(r"\b\d{1,3}\s*%\s*(?:up|down|on\b)",
                                      text or "", re.I))
                       and not entry_language(text)
                       and not ACTION_VERB.search(text or ""))
        _status = _status or _pct_status
        _intent = bool(entry_language(text)
                       or (re.search(r"\b(bull\s+puts?|bear\s+calls?)\b", text or "", re.I)
                           and not _pct_status)
                       or not _status)
        if _pct_status:
            norm["_pct_status"] = True
        if not ts:
            if _coherent and _intent:
                norm["spread"]["_unverified"] = True
            else:
                norm["spread"] = None
        else:
            real = [l for l in _legs_c
                    if l.get("strike") and float(l["strike"]) in ts]
            if not real:
                # text names numbers, but none are leg strikes (7665 is a LEVEL
                # in 3284, not a strike) — same treatment as no-strikes: a
                # coherent card survives as unverified, junk does not.
                if _coherent and _intent:
                    norm["spread"]["_unverified"] = True
                else:
                    norm["spread"] = None
            elif len(real) != len(_legs_c):
                norm["spread"]["legs"] = real
                norm["spread"]["_inferred"] = True
    # TEXT % MARK: '20% up' / '35% on' is the profit mark — carry it into the
    # book mark when no image percentage exists.
    if norm.get("pct_actual") is None:
        mp = re.search(r"\b(\d{1,3})\s*%\s*(?:up|on|profit|gain)", text or "", re.I)
        if mp:
            norm["pct_actual"] = int(mp.group(1))
        elif re.fullmatch(r"\s*(\d{1,3})\s*%\s*", text or ""):
            norm["pct_actual"] = int(re.fullmatch(r"\s*(\d{1,3})\s*%\s*", text or "").group(1))
    # INSTRUMENT RECONCILE (2026-11-03): the brain hallucinates tickers on
    # management messages (3568 said SLV for a GOOG line). James writes tickers
    # in caps; when a no-legs message contains exactly ONE all-caps token, the
    # text beats the brain label.
    if not text_struct(text)[0]:
        caps = {w for w in re.findall(r"\b[A-Z]{2,5}\b", text or "")
                if w not in _TICK_STOP}
        if len(caps) == 1:
            cand = next(iter(caps))
            if cand != (norm.get("instrument") or "").upper():
                norm["instrument"] = cand
    return True, "ok", norm


def map_symbol(instrument):
    im = CFG["instrument_map"]
    for k, v in im.items():
        if instrument and instrument.strip().upper() == k.upper():
            return v
    return None


# ---------------------------------------------------------------- execution

def open_trade(card, msg_id, st):
    """One validated card -> paper position (Tiger long / simulated short).

    Index-level cards (SPX500 7758.8 etc.) are converted to ETF terms: entry is
    re-anchored to the live ETF quote and stop/targets scaled by quote/index_entry.
    """
    sym = map_symbol(card["instrument"])
    why = "" if sym else f"{card['instrument']} not in instrument_map — ledger-only"
    trade_id = f"T{int(time.time())}"
    entry, stop, targets = card["entry"], card["stop"], list(card["targets"])
    if CFG.get("executor") == "blackbull":
        open_trade_bb(card, msg_id, st)
        return
    conv_k = 1.0
    q = get_quote(sym) if sym else None
    div = CFG["index_divisors"].get((card["instrument"] or "").strip().upper())
    if div and not q:
        # index levels without a live quote can't be converted — never open blind
        why = f"no quote for {sym}; index-level card skipped"
        entry, stop, targets = None, None, []
    elif div and q:
        anchor = q * div                       # ~index scale for sanity checks
        in_scale = lambda v: v is not None and 0.2 * anchor <= v <= 5 * anchor
        if entry and not in_scale(entry):
            entry = None                       # option premium etc., not an index level
        if entry:
            conv_k = q / entry
            entry = q                          # trade in ETF terms, anchored to market
        else:
            conv_k = 1.0 / div                 # ~ETF/index ratio (SPY≈SPX/10)
        if not in_scale(stop):
            stop = None
        targets = [x for x in targets if in_scale(x)]
        if stop:
            stop *= conv_k
        targets = [x * conv_k for x in targets]
        why = (why + "; " if why else "") + f"index->ETF k={conv_k:.4f}"
    if q:
        entry = entry or q
    elif not entry:
        why = f"no quote and no entry level for {sym}"

    if stop and entry and abs(stop - entry) <= 0.001 * entry:
        stop = None          # model set stop == entry; fall back to default distance
    # default stop when the signal gave none (already in ETF terms)
    if stop is None and entry:
        pct = CFG["default_stop_pct"] / 100
        stop = entry * (1 - pct) if card["direction"] == "long" else entry * (1 + pct)

    t = {"id": trade_id, "msg_id": msg_id, "instrument": card["instrument"],
         "symbol": sym or "-", "direction": card["direction"], "entry": entry,
         "stop": stop, "targets": targets, "confidence": card["confidence"],
         "horizon": card["horizon"], "rationale": card["rationale"],
         "opened": now(), "status": "pending" if sym else "logged_only",
         "order_id": None, "qty": None, "exit_price": None, "exit_ts": None,
         "pnl": None, "fill_note": why, "k": round(conv_k, 6)}

    if not sym or not entry:
        # nothing tradeable (unmapped instrument, no quote, skipped index card)
        ledger_row({**t, "event": "LOGGED", "targets": "|".join(f"{x:.2f}" for x in targets),
                    "reason": why or "not tradeable"})
        log(f"logged-only {card['instrument']} msg {msg_id}: {why}")
        save_state(st)
        return

    if entry:
        qty = max(1, int(CFG["notional_usd"] / entry))
        t["qty"] = qty
        if card["direction"] == "long":
            place_tiger_long(t, st)
        else:
            # Tiger paper accounts cannot short — simulated short, marked each cycle
            t["status"] = "open_sim"
            log(f"sim short {sym} qty {qty} @ {entry:.2f} stop {stop} targets {t['targets']}")
    st.setdefault("open", {})[trade_id] = t
    # D128: lifecycle for placed/simulated Tiger stock trades (not logged-only)
    if t.get("order_id") or t["status"] == "open_sim":
        _lc_start(trade_id, msg_id, ticker,
                  f"{card['direction']} {t.get('qty')} @ {entry} [{t['status']}]",
                  (card.get("rationale") or "")[:120])
    ledger_row({**t, "event": "ORDER" if t["order_id"] else ("SIM" if t["status"] == "open_sim" else "LOGGED"),
                "targets": "|".join(f"{x:.2f}" for x in t["targets"]),
                "reason": why or "ok"})
    push(trade_card_text(t), st)
    save_state(st)


def place_tiger_long(t, st):
    _, trade, _ = tiger()
    dry = CFG["dry_run"] or trade is None
    label = "DRY" if CFG["dry_run"] else ("TIGER" if trade else "SIM(no-tiger)")
    if dry:
        t["status"] = "open_sim" if trade is None else "open_dry"
        t["fill_note"] = (t["fill_note"] + " " if t["fill_note"] else "") + label
        log(f"{label} long {t['symbol']} qty {t['qty']} @ ~{t['entry']:.2f} "
            f"stop {t['stop']:.2f} targets {t['targets']}")
        return
    try:
        from tigeropen.common.util.contract_utils import stock_contract
        from tigeropen.common.util.order_utils import Order
        cfg, trade, _ = tiger()
        contract = stock_contract(symbol=t["symbol"], currency="USD")
        # LMT + outside_rth: MKT is rejected outside 09:30-16:00 ET
        order = Order(account=cfg.account, contract=contract, action="BUY",
                      order_type="LMT", quantity=t["qty"],
                      limit_price=round(t["entry"] * 1.002, 2), outside_rth=True)
        oid = trade.place_order(order)
        t["order_id"], t["status"] = oid, "pending"
        log(f"tiger BUY {t['symbol']} qty {t['qty']} LMT {round(t['entry'] * 1.002, 2)} order {oid}")
        time.sleep(3)
        check_fill(t)
    except Exception as e:
        t["status"], t["fill_note"] = "order_failed", f"{type(e).__name__}: {e}"
        log(f"tiger order failed: {e}")


def check_fill(t):
    """Pending Tiger order -> filled? update entry to avg fill price."""
    if not t.get("order_id"):
        return
    _, trade, _ = tiger()
    if trade is None:
        return
    try:
        o = trade.get_order(id=t["order_id"])
        status = str(getattr(o, "status", "")).upper()
        if "FILLED" in status or "EXECUTED" in status:
            fill = getattr(o, "avg_fill_price", None) or getattr(o, "fill_price", None)
            if fill:
                t["entry"] = float(fill)
            t["status"] = "open_opt" if t.get("opt_id") else "open_tiger"
            log(f"fill confirmed {t['symbol']} order {t['order_id']} @ {t['entry']}")
        elif any(s in status for s in ("CANCEL", "REJECT", "EXPIRED", "FAILED")):
            t["status"], t["fill_note"] = "order_failed", f"tiger status {status}"
            log(f"order {t['order_id']} terminal: {status}")
    except Exception as e:
        log(f"fill check failed order {t['order_id']}: {type(e).__name__}: {e}")


def close_trade(t, exit_price, reason, st):
    if t.get("executor") == "bb":
        close_trade_bb(t, reason, st)
        return
    if t.get("executor") == "tradier_stock":
        if t["status"] in ("closed", "close_pending"):
            return
        side = "sell" if t["direction"] == "long" else "buy"
        ex = _TRADIER().place_stock(t["symbol"], side, t["qty"] or 1)
        if not ex.ok:
            t["fill_note"] = f"close FAILED: {ex.reason}"
            ledger_row({**t, "event": "ALERT", "reason": t["fill_note"]})
            push(f"⚠️ CLOSE FAILED {t['symbol']} — {ex.reason}", st)
            save_state(st)
            return
        t["close_order_id"], t["close_reason"], t["status"] = ex.order_id, reason, "close_pending"
        t["fill_note"] = f"close order {ex.order_id} placed ({reason})"
        ledger_row({**t, "event": "ORDER", "reason": t["fill_note"]})
        save_state(st)
        return
    if t.get("executor") == "tradier":
        # Book only on a REAL fill: send the close order and stay close_pending
        # until manage_once sees the fill (same rule as the Tiger option path).
        if t["status"] in ("closed", "close_pending"):
            return
        if t["status"] == "pending_entry":
            # never filled: cancel the resting entry, book zero — but if the
            # cancel FAILS the order may still fill (it did: TL3765) -> keep it
            # pending and retry, never book a position that may become real
            try:
                ok_c, why_c = _TRADIER().cancel(t.get("order_id"))
            except Exception as e:
                ok_c, why_c = False, str(e)
            if not ok_c:
                t["fill_note"] = (f"exit requested but cancel FAILED ({why_c}) — "
                                  f"will retry; order may fill")
                t["close_reason"] = reason
                ledger_row({**t, "event": "ALERT", "reason": t["fill_note"]})
                push(f"⚠️ {t['id']}: exit requested but entry cancel failed — retrying", st)
                save_state(st)
                return
            t["status"], t["pnl"] = "closed", 0.0
            t["fill_note"] = "exit on unfilled entry — entry cancelled, PnL 0"
            st.setdefault("closed", {})[t["id"]] = t
            st.get("open", {}).pop(t["id"], None)
            ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
            _lc_finish(t, "EXIT", t["fill_note"])   # D128
            push(f"🚪 {t['id']} closed before fill — entry cancelled", st)
            log(f"{t['id']} exit on unfilled entry ({ok_c})")
            save_state(st)
            return
        _cq = t.pop("close_qty", None) or t.get("qty") or 1
        # D135+D137 (2026-11-21): a MARKET multileg close legged in Friday's
        # crash open — the QQQ pair filled 9.31 NET on a 2-wide spread (legs a
        # beat apart while the tape moved), booking -32,072 against the
        # position's true -7,941 defined-risk bound. Every close now carries a
        # LIMIT: credit spreads = debit min(width, mark x 1.30) — the width is
        # the mathematical ceiling (impossible packages refused), the 1.30
        # band refuses fills far from our own mark (MRNA's 1.41-vs-6.3 class);
        # single options = band around opt_mark (short: pay <= 1.30x mark,
        # long: accept >= 0.70x mark). A resting unfilled close is re-priced
        # with fresh marks by manage_once (max 2), then escapes to market —
        # exits can be delayed a cycle, never stranded (TL3731: cancels
        # bounded). No mark available -> D135 behaviour: width cap for
        # spreads, market for singles (never block an exit on a dead feed).
        _lim = None
        _lgs = t.get("legs") or []
        _is_reprice = bool(t.pop("_reprice", None))   # D137 manage-side refresh
        if not t.get("_lim_escape"):
            try:
                _mk = spread_mark(t) if len(_lgs) > 1 else opt_mark(t)
            except Exception:
                _mk = None
            if len(_lgs) > 1 and str(t.get("direction") or "").lower() != "long":
                try:
                    _ks = [float(l[0]) for l in _lgs if l and l[0] is not None]
                except (TypeError, ValueError):
                    _ks = []
                if len(_ks) >= 2:
                    _w = round(max(_ks) - min(_ks), 2)
                    _lim = min(_w, round(float(_mk) * 1.30, 2)) if _mk else _w
                    # D140: real-spot anchor — a close above what the REAL
                    # print implies the package can be worth is a bad fill
                    # (Friday's legging), whatever our own mark says. Spot
                    # unavailable/implausible -> anchor off, band stands.
                    _S = _anchor_spot(t.get("instrument"), _lgs)
                    if _S:
                        _ac = anchor_close_cap(_lgs, _S)
                        if _ac is not None:
                            _lim = min(_lim, _ac) if _lim else _ac
            elif len(_lgs) <= 1 and _mk:
                _b = round(float(_mk) * (1.30
                        if str(t.get("direction") or "").lower() != "long" else 0.70), 2)
                _lim = _b if _b > 0 else None
        ex = _TRADIER().close(t, limit_price=_lim, qty=_cq)
        if not ex.ok:
            t["fill_note"] = f"close FAILED: {ex.reason}"
            ledger_row({**t, "event": "ALERT", "reason": t["fill_note"]})
            log(f"tradier close FAILED {t['id']}: {ex.reason} — stays open")
            push(f"⚠️ CLOSE FAILED {t['instrument']} {t['id']} — {ex.reason}\n"
                 f"position stays open, will retry", st)
            save_state(st)
            return
        t["close_order_id"], t["close_reason"] = ex.order_id, reason
        t["status"] = "close_pending"
        # D137: refresh bookkeeping — when the resting close is unfilled a
        # minute on, manage_once re-prices it against fresh marks (bounded).
        # A reprice keeps the count/escape; a fresh close event resets them.
        t["lim_at"] = time.time()
        t["lim_qty"] = _cq
        if not _is_reprice:
            t["lim_refresh"] = 0
            t.pop("_lim_escape", None)
        _lat = round(time.time() - _RCV.pop(str(t.get("msg_id")), time.time()), 1)
        t["exit_latency_s"] = _lat
        t["fill_note"] = f"close order {ex.order_id} placed ({reason}, {_lat}s)"
        ledger_row({**t, "event": "ORDER", "reason": t["fill_note"]})
        log(f"tradier close SENT {t['id']} order {ex.order_id} ({reason})")
        push(f"🧾 CLOSE SENT {t['instrument']} {t['direction']} — {reason}\n"
             f"order {ex.order_id} — PnL books at the real fill, not a guess", st)
        save_state(st)
        return
    _, trade, _ = tiger()
    if t["status"] == "open_tiger" and trade and not CFG["dry_run"]:
        try:
            from tigeropen.common.util.contract_utils import stock_contract
            from tigeropen.common.util.order_utils import Order
            cfg, trade, _ = tiger()
            contract = stock_contract(symbol=t["symbol"], currency="USD")
            order = Order(account=cfg.account, contract=contract, action="SELL",
                          order_type="LMT", quantity=t["qty"],
                          limit_price=round(exit_price * 0.998, 2), outside_rth=True)
            oid = trade.place_order(order)
            log(f"tiger SELL close {t['symbol']} qty {t['qty']} LMT {round(exit_price * 0.998, 2)} order {oid} ({reason})")
        except Exception as e:
            log(f"tiger close FAILED {t['symbol']}: {e} — position stays open")
            return
    was_tiger = t["status"] == "open_tiger"
    if t["status"] in ("pending", "pending_open"):
        # closed before any fill: cancel any resting order, book PnL 0
        if t.get("order_id"):
            _, trade, _ = tiger()
            if trade:
                try:
                    trade.cancel_order(id=t["order_id"])
                    log(f"cancelled resting order {t['order_id']} ({t['id']})")
                except Exception as e:
                    log(f"cancel failed order {t['order_id']}: {e}")
        t["pnl"], reason = 0.0, reason + " (before fill, PnL 0)"
    else:
        mult = 1 if t["direction"] == "long" else -1
        if t.get("entry") is None:
            t["pnl"] = None          # credit never parsed (typo): PnL not computable
        else:
            t["pnl"] = round(mult * (exit_price - t["entry"]) * (t["qty"] or 0), 2)
    t["exit_price"], t["exit_ts"], t["status"], t["fill_note"] = exit_price, now(), "closed", reason
    st.setdefault("closed", {})[t["id"]] = t
    st.get("open", {}).pop(t["id"], None)
    ledger_row({**t, "event": "EXIT", "targets": "|".join(f"{x:.2f}" for x in t["targets"]),
                "reason": reason})
    sim_tag = "" if was_tiger else " (sim)"
    arrow = "🎯" if t["pnl"] and t["pnl"] > 0 else "🚪"
    entry_s = f"{t['entry']:.2f}" if t.get("entry") is not None else "?"
    pnl_s = f"${t['pnl']:+.2f}" if t.get("pnl") is not None else "n/a (entry unparsed)"
    exit_s = f"{exit_price:.2f}" if exit_price else "market"
    push(f"{arrow} CLOSED {t['symbol']} {t['direction']} — {reason}\n"
         f"entry {entry_s} → exit {exit_s} | PnL {pnl_s}{sim_tag}", st)
    # D119: lifecycle — trade closed
    try:
        import trade_lifecycle as _tlc
        _tlc.exit_lifecycle(t["id"], t.get("msg_id", ""),
                            f"PnL {pnl_s}, {reason[:50]}", "")
    except Exception:
        pass
    save_state(st)


def trade_card_text(t):
    if t["status"] in ("unsupported", "order_failed"):
        return (f"⚠️ SKIPPED {t['instrument']} [OPT] — {t.get('fill_note') or 'order failed'}\n"
                f"no position taken; see ledger")
    if t["status"] == "pending_open":
        return (f"⏳ DEFERRED {t['instrument']} {t['direction']} [BLACKBULL]\n"
                f"{t['symbol']} — {t.get('fill_note') or 'waiting for market'}\n"
                f"Will retry; expires unfilled in {CFG['max_open_retry_hours']}h.")
    if t.get("executor") == "opt" and t["status"] == "pending":
        src = "OPT-TIGER-PENDING"
    else:
        src = {"open_tiger": "TIGER", "open_dry": "DRY", "open_bb": "BLACKBULL",
               "open_opt": "OPT-TIGER", "pending": "TIGER-PENDING"}.get(t["status"], "SIM")
    lines = [f"📈 JAMES {t['direction'].upper()} {t['instrument']} → {t['symbol']} [{src}]",
             f"entry {t['entry'] and round(t['entry'], 2)} | stop {t['stop'] and round(t['stop'], 2)} "
             f"| targets {[round(x, 2) for x in t['targets']] or '—'}",
             f"qty {t['qty']} (~${CFG['notional_usd']}) | {t['horizon']} | conf {t['confidence']}",
             f"\"{t['rationale']}\""]
    if t.get("tier"):
        lines.append(f"risk tier: {TIER_BADGE.get(t['tier'], t['tier'])}")
    if t["fill_note"]:
        lines.append(f"note: {t['fill_note']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- manage

def _legs_are_debit(legs):
    """True when a 2-leg same-right vertical is a DEBIT (we PAID its premium):
    calls — bought the lower strike; puts — bought the higher strike. Used to
    sign premium-as-cost (D136: shadows stored a debit as a positive 'credit'
    and SIM4527 read -1,350 as +1,350). Mixed rights, 1 leg, or 2 same-action
    legs fall back to credit (previous behaviour)."""
    if not legs or len(legs) != 2:
        return False
    try:
        (k1, r1, a1), (k2, r2, a2) = [tuple(l)[:3] for l in legs]
        if str(r1)[0].lower() != str(r2)[0].lower():
            return False
        _b1 = str(a1).lower().startswith("b")
        _b2 = str(a2).lower().startswith("b")
        if _b1 == _b2:                 # not one buy + one sell → not a vertical
            return False
        buy_k, sell_k = (float(k1), float(k2)) if _b1 else (float(k2), float(k1))
    except (TypeError, ValueError):
        return False
    return buy_k < sell_k if str(r1)[0].lower() == "c" else buy_k > sell_k


def _spread_expiry_pnl(legs, credit, qty, S):
    """What a spread WOULD have made at expiry: net cost to close the legs at
    expiry (sell legs cost their intrinsic, buy legs pay theirs) against the
    credit collected. legs = [(strike, right, action), ...]."""
    close_cost = 0.0
    for strike, right, action in legs or []:
        k = float(strike)
        intr = max(0.0, S - k) if str(right).lower().startswith("c") \
            else max(0.0, k - S)
        close_cost += intr if str(action).lower().startswith("s") else -intr
    return round(((credit or 0) - close_cost) * 100 * (qty or 1), 2)


def manage_manual_settle(st, after_hour=16):
    """Settle records parked in st['manual_settle'] at the expiry-day close
    print (phantoms / mis-captures). Each entry: id, opt_id, entry, qty, expiry."""
    n = us_now()
    if n.hour < after_hour:
        return
    try:
        S = bbc().get_quote("SPX500")
    except Exception:
        S = None
    if not S:
        return
    changed = False
    for tid, m in list(st.get("manual_settle", {}).items()):
        rest = (m.get("opt_id") or "").split(" ")[-1]
        if len(rest) < 92 and " " not in (m.get("opt_id") or ""):
            rest = (m.get("opt_id") or "")
        try:
            side = rest[6]
            strike = int(rest[7:]) / 1000
        except (IndexError, ValueError):
            continue
        intr = max(0.0, float(S) - strike) if side == "C" else max(0.0, strike - float(S))
        pnl = round((intr - (m.get("entry") or 0)) * 100 * (m.get("qty") or 1), 2)
        t = st.get("closed", {}).get(tid)
        if t is not None:
            t["pnl"] = pnl
            t["fill_note"] = (f"manual settle at SPX {float(S):,.2f} — intrinsic "
                              f"{intr:.2f} (suspected mis-capture record)")
            t["exit_ts"] = t.get("exit_ts") or now()
            ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
            log(f"manual settle {tid}: {pnl:+.2f}")
            st["manual_settle"].pop(tid, None)
            changed = True
    if changed:
        save_state(st)


def manage_shadow(st):
    """Settle SIM-alias shadow entries (acked re-sends paper-tracked to TEST the
    no-duplicate policy). Settles after the expiry session closes on the US
    clock, at the live quote for ITS OWN underlying (D52: QQQ legs once settled
    at the SPX print). Shadow PnL > 0 = the ack policy missed a real trade;
    <= 0 = acking was right. Shadows NEVER trade — separate namespace from
    open/closed; nothing here touches a broker."""
    changed = False
    for sh in list(st.get("shadow", {}).values()):
        if sh.get("status") != "sh_open":
            continue
        exp = str(sh.get("expiry") or "")
        n = us_now()
        today_iso = n.date().isoformat()
        if not exp or not (today_iso > exp or (today_iso == exp and n.hour >= 16)):
            continue
        # D120 (2026-11-19): settle at the OFFICIAL expiry close, not the live
        # quote. At 16:00:32 UTC the live CFD read 7313.50 while the official
        # print (verified via SPY 730.2) was 7323.63 — a 10-point lag that FLIPPED
        # SIM4495's verdict from +$303 (ack missed a winner) to -$560 (ack was
        # right). The real-position path (settle_expired) already uses
        # _expiry_close; shadows must match it or the no-duplicate policy is
        # graded on a wrong tape.
        try:
            S = _expiry_close(_canon_underlying(sh.get("instrument")) or
                              str(sh.get("instrument") or "").split()[0], exp)
        except Exception:
            S = None
        if not S:
            try:
                S = _under_quote(sh.get("instrument") or "SPX500")
            except Exception:
                S = None
        if not S:
            continue            # weekend / no quote — retry on a later cycle
        # D136 (2026-11-21): a DEBIT structure stores what we PAID as its
        # 'credit' — passing it positive flips the verdict. SIM4527 (buy
        # 7200C / sell 7220C, paid 13.5) expired worthless at SPX 7,175.89
        # and the shadow booked +1,350 'MISSED TRADE (ack cost us)' when the
        # truth is -1,350 — acking SAVED the debit. settle_parked already
        # negates debits (its is_debit rule); shadows now sign from leg
        # geometry the same way.
        _c = sh.get("credit")
        if _c is not None and _legs_are_debit(sh.get("legs")):
            _c = -abs(_c)
        pnl = _spread_expiry_pnl(sh.get("legs"), _c,
                                 sh.get("qty"), float(S))
        sh.update(status="sh_settled", settle_px=float(S), pnl=pnl, settled=now())
        if sh.get("credit") is None:
            # D51: credit never captured on the ack — a 0.00 PnL would read
            # 'ack was right' even when the legs expired worthless (a would-have
            # WINNER). Say which way they finished instead.
            _close = -_spread_expiry_pnl(sh.get("legs"), 0, 1, float(S)) / 100.0
            verdict = ("credit unrecorded — legs expired worthless "
                       "(would have kept the premium)") if _close <= 0 else \
                      (f"credit unrecorded — legs finished ITM "
                       f"({_close:.2f}/unit against)")
        else:
            verdict = "MISSED TRADE (ack cost us)" if pnl > 0 else "ack was right"
        sh["verdict"] = verdict
        ledger_row({"event": "SHADOW", "trade_id": sh["id"],
                    "msg_id": sh.get("msg_id"), "instrument": sh.get("instrument"),
                    "qty": sh.get("qty"), "entry": sh.get("credit"),
                    "reason": f"shadow settled @ {sh.get('instrument') or 'SPX'} "
                              f"{float(S):,.2f} -> "
                              f"${pnl:+.2f} ({verdict})",
                    "raw": (sh.get("raw") or "")[:80]})
        log(f"shadow {sh['id']} settled @ {float(S):,.2f} -> {pnl:+.2f} ({verdict})")
        push(f"🧪 SHADOW RESULT {sh['id']} (msg {sh.get('msg_id')}) — the re-sent "
             f"{sh.get('instrument')} structure we ACKED (not traded):\n"
             f"if traded at {sh.get('credit')} x{sh.get('qty')} it would settle "
             f"${pnl:+.2f} @ {sh.get('instrument') or 'SPX'} {float(S):,.2f} → {verdict}", st)
        # D119: lifecycle — shadow settled
        try:
            import trade_lifecycle as _tlc
            _tlc.shadow_lifecycle(sh["id"], sh.get("msg_id", ""),
                                  f"settled @ {float(S):,.2f} -> ${pnl:+.2f} ({verdict})", "")
        except Exception:
            pass
        changed = True
    if changed:
        save_state(st)


# ------------------------------------------------- parked-trade outcomes
# Parking is fine; losing the outcome is not. A parked structure is paid-for
# information, so every park settles at its expiry close in park_outcomes.csv
# — the would-have P&L per lot — and park_report.py reads the register back to
# say which way these trades are going. Settle is expiry-based (the manage_shadow
# convention), not path-dependent: no intraday stop/target is simulated.

PARK_OUT = HERE / "park_outcomes.csv"
UW_PARK = HERE / "uw_park_links.csv"
SPLIT_SUSPECTS = HERE / "split_suspects.csv"
PARK_OUT_COLS = ["ts", "msg_id", "park_ts", "underlying", "structure", "direction",
                 "body", "wing_low", "wing_high", "width", "credit", "qty",
                 "expiry", "settle_px", "would_pnl", "verdict", "reason", "raw"]

PARK_LEG = re.compile(
    r"(sell|buy)|(\d{2,5}(?:\.\d{1,2})?)\s*([cp])(?![a-z])"
    r"\s*(?:/\s*([cp])(?![a-z]))?", re.I)

_PX_CACHE = {}


def us_session_at(ts):
    """The US session a UTC ledger timestamp names — us_session_for_today's
    roll rule applied to a PAST instant, so a retro settle reads 'for today'
    as the session the row was written for, never the wall clock."""
    from datetime import timedelta as _td
    u = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            u = datetime.strptime(str(ts)[:19], fmt).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue
    if u is None:
        return None
    try:
        from zoneinfo import ZoneInfo
        n = u.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        y = u.year

        def _nth_sun(month, k):
            d0 = _date(y, month, 1)
            return d0 + _td(days=(6 - d0.weekday()) % 7 + (k - 1) * 7)
        dst = _nth_sun(3, 2) <= u.date() < _nth_sun(11, 1)
        n = u.astimezone(timezone(_td(hours=-4 if dst else -5)))
    d = n.date()
    if n.hour >= 16 or n.weekday() >= 5:
        d += _td(days=1)
    while d.weekday() >= 5:
        d += _td(days=1)
    return d


def park_legs(raw, reason=""):
    """(strike, right, action) for a parked row: the text first, then the park
    reason (which carries 'SELL 7735.0C / BUY 7745.0C' when the raw is
    narrative). Decimal strikes count — equity options are half-point (177.5C)
    and an integer-only pattern silently drops them."""
    for src in (raw or "", reason or ""):
        cur, legs = "buy", []
        for m in PARK_LEG.finditer(src):
            if m.group(1):
                cur = "sell" if m.group(1).lower() == "sell" else "buy"
            elif m.group(2):
                r1, r2 = m.group(3).lower(), (m.group(4) or "").lower()
                legs.append((float(m.group(2)), "call" if r1 == "c" else "put", cur))
                if r2:
                    legs.append((float(m.group(2)), "call" if r2 == "c" else "put", cur))
        if legs:
            return legs
    return []


def park_direction(legs):
    """Market view of a parked structure from its legs alone. A short at the
    higher strike of a vertical is the bullish posture (credit put spread
    wins when price holds) for either right; a fly/condor/straddle is neutral."""
    if not legs:
        return ""
    rights = {r for _, r, _ in legs}
    if len(legs) >= 3 or len(rights) > 1:
        return "neutral"                     # fly, condor, straddle/strangle
    if len(legs) == 1:
        _, r, a = legs[0]
        return ("bullish" if (a == "sell" and r == "put")
                or (a == "buy" and r == "call") else "bearish")
    sold = [k for k, _, a in legs if a == "sell"]
    bought = [k for k, _, a in legs if a == "buy"]
    if sold and bought:
        return "bullish" if sold[0] > bought[0] else "bearish"
    return ""


def park_outcome(r):
    """Would-have economics of one parking_lot row at its expiry close.
    Returns a park_outcomes.csv row dict, or None while the structure has not
    expired yet (a later settle pass picks it up). Per-lot: a park never
    carries qty."""
    raw = r.get("raw") or ""
    legs = park_legs(raw, r.get("reason") or "")
    expiry = _signal_expiry(None, raw, asof=us_session_at(r.get("ts")))
    try:
        width = float(r.get("width"))
    except (TypeError, ValueError):
        width = None
    if width is None and len(legs) >= 2:
        ks = [k for k, _, _ in legs]
        width = round(max(ks) - min(ks), 2)
    out = {"ts": now(), "msg_id": r.get("msg_id") or "",
           "park_ts": r.get("ts") or "",
           "underlying": r.get("underlying") or "",
           "structure": r.get("structure") or "",
           "direction": park_direction(legs),
           "body": r.get("body") or "", "wing_low": r.get("wing_low") or "",
           "wing_high": r.get("wing_high") or "",
           "width": width if width is not None else "",
           "credit": "", "qty": 1, "expiry": expiry or "",
           "settle_px": "", "would_pnl": "", "verdict": "",
           "reason": (r.get("reason") or ""), "raw": raw}   # D46: full fidelity, truncate at display only
    if not expiry:
        out["verdict"] = "unsettleable: no expiry"
        return out
    if not (r.get("underlying") or "").strip():
        out["verdict"] = "unsettleable: no underlying"
        return out
    sym = _canon_underlying(r.get("underlying")) or str(r.get("underlying")).split()[0]
    # NO expiry-weekday gate here, deliberately (probed live 2026-11-12):
    # a Mon/Wed/Fri-vs-Friday rule looked right until the chain said otherwise —
    # QQQ/IWM list Mon-Fri DAILY expiries, msg 3738's Tuesday SPX order filled
    # and booked -525, and past-dated chains return 0 contracts whether or not
    # the expiry was ever listed, so validity is unprovable for a retro row.
    # Settle at the session the message named — that close answers the question
    # the register exists for (which direction was the signal pointing).
    if expiry >= us_today().isoformat():
        return None                      # not expired yet — settle in a later pass
    if not legs:
        out["verdict"] = "unsettleable: no leg rights"
        return out
    key = (sym, expiry)
    # D142: a cached None must NOT poison the retry — the old `key not in`
    # check stored the first failure forever, so "print not up yet — retry
    # later" could never actually retry (the RUT park sat on this). Re-fetch
    # while unresolved; a real value caches for good.
    if _PX_CACHE.get(key) is None:
        _PX_CACHE[key] = _expiry_close(sym, expiry)
    S = _PX_CACHE[key]
    if S is None:
        if (us_today() - _date.fromisoformat(expiry)).days > 3:
            out["verdict"] = "unsettleable: no close print"
            return out
        return None                      # print not up yet — retry later
    _src_sfx = (" (LSE tape close)"
                if _CLOSE_SRC.get(key) == "LSE tape" else "")
    credit = text_credit(raw)
    if credit is None:
        try:
            credit = float(r.get("credit"))
        except (TypeError, ValueError):
            credit = None
        if credit is not None and width and credit > width:
            credit = round(credit / 100.0, 2)    # 'collect $475' parked as dollars
    s = (r.get("structure") or "")
    is_debit = ("debit" in s or s.endswith("_long") or s == "iron_butterfly"
                or all(a == "buy" for _, _, a in legs))
    if credit is not None:
        pnl = _spread_expiry_pnl(legs, -abs(credit) if is_debit else abs(credit),
                                 1, S)
        out["credit"] = round(abs(credit), 2)
        out["settle_px"] = S
        out["would_pnl"] = pnl
        out["verdict"] = (("WOULD HAVE WON" if pnl > 0 else
                           "would have lost" if pnl < 0 else "flat")
                          + _src_sfx)
        return out
    # no premium recorded: the close still says which way the legs went
    close_cost = -_spread_expiry_pnl(legs, 0, 1, S) / 100.0
    out["settle_px"] = S
    if close_cost <= 0:
        out["verdict"] = "WOULD HAVE WON (expired worthless, no credit recorded)"
    elif width and close_cost >= width:
        out["verdict"] = "would have lost (max loss, no credit recorded)"
    else:
        out["verdict"] = "ambiguous (no credit recorded)"
    out["verdict"] += _src_sfx
    return out


def settle_parked(st=None):
    """Settle every parked structure that has expired into park_outcomes.csv.
    Idempotent (keyed by msg_id — the same message is re-parked on every retry
    and must yield ONE outcome, or the register inflates: msg 924 alone was
    parked 223 times), retry-safe (an unresolved close print waits for a later
    pass), and read-only against the brokers — it books what the park WOULD
    have done and never orders anything. Rows append as they settle so a
    mid-run timeout loses nothing."""
    if not PKL.exists():
        return 0

    def _key(mid, ts):
        return mid or ts          # blank msg_id rows (early backlog) key by ts

    done = set()
    if PARK_OUT.exists():
        with PARK_OUT.open(encoding="utf-8") as f:
            for o in csv.DictReader(f):
                done.add(_key(o.get("msg_id") or "", o.get("park_ts") or ""))
    with PKL.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    n = won = lost = 0
    net = 0.0
    detail = []                      # D122: per-park outcome lines for the push
    fresh = not PARK_OUT.exists()
    with PARK_OUT.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PARK_OUT_COLS)
        if fresh:
            w.writeheader()
        for r in rows:
            if _key(r.get("msg_id") or "", r.get("ts") or "") in done:
                continue
            o = park_outcome(r)
            if o is None:
                continue
            w.writerow({k: o.get(k, "") for k in PARK_OUT_COLS})
            done.add(_key(o.get("msg_id") or "", o.get("park_ts") or ""))
            n += 1
            v = str(o["verdict"])
            if v.startswith("WOULD HAVE WON"):
                won += 1
            elif v.startswith("would have lost"):
                lost += 1
            if o["would_pnl"] != "":
                net += float(o["would_pnl"])
            # D122: build a per-park line so each outcome is published, not
            # just the count. The user should see WHICH trade settled and how,
            # without asking.
            try:
                _p = f"${float(o['would_pnl']):+.0f}" if o.get("would_pnl") not in ("", None) else "n/a"
            except (TypeError, ValueError):
                _p = "n/a"
            _s = o.get("settle_px")
            detail.append(
                f"• msg{o.get('msg_id') or '?'} {o.get('underlying') or '?'} "
                f"{o.get('structure') or ''} credit {o.get('credit') or '?'} "
                f"-> close {_s if _s not in ('', None) else '?'} = {_p} "
                f"({v})")
    if n:
        log(f"park outcomes: {n} settled ({won} would have won / {lost} lost)")
        if st is not None:
            # D122: publish each settled park's would-have outcome proactively —
            # the register is paid-for information; losing the outcome is not.
            _lines = detail[:8]
            _more = f"\n… and {len(detail) - 8} more (see park_outcomes.csv)" \
                if len(detail) > 8 else ""
            push(f"🧪 PARK OUTCOMES — {n} settled at expiry: {won} would have won, "
                 f"{lost} would have lost (net ${net:+,.2f}/lot).\n"
                 + "\n".join(_lines) + _more, st)
    return n


def strike_far(spot, strike, right=None):
    """(too_far, pct) for a strike beyond `strike_far_pct` — but only on the
    OUT-of-the-money side. Deep ITM strikes are deliberate for him (earnings
    pops: RDDT 120C against 94), while a far OTM strike is usually a scale slip
    (IWM 270P against 245)."""
    try:
        spot, strike = float(spot), float(strike)
    except (TypeError, ValueError):
        return False, None
    if spot <= 0 or strike <= 0:
        return False, None
    pct = abs(strike - spot) / spot * 100
    return pct > float(CFG.get("strike_far_pct", 15.0)), pct


def text_bracket(text):
    """A conditional ENTRY bracket from a levels view:
    'bearish below 28710 / bullish above 28880' + an S/R ladder.
    Returns {"up": x, "down": y, "sr": {..}} or None."""
    t = text or ""
    up = re.search(r"bullish\s+above\s*:?\s*\$?(\d{3,5}(?:\.\d+)?)", t, re.I)
    dn = re.search(r"bearish\s+below\s*:?\s*\$?(\d{3,5}(?:\.\d+)?)", t, re.I)
    if not (up or dn):
        # SUPPORT/RESISTANCE pair is the same plan in different words:
        # buy the resistance break, sell the support break
        res = re.search(r"(\d{3,5}(?:\.\d+)?)\s+is\s+(?:a\s+)?(?:very\s+|strong\s+|"
                        r"major\s+|key\s+|important\s+)?resistance\b", t, re.I)
        sup = re.search(r"(\d{3,5}(?:\.\d+)?)\s+is\s+(?:a\s+)?(?:very\s+|strong\s+|"
                        r"major\s+|key\s+|important\s+)?support\b", t, re.I)
        if res and sup:
            return {"up": float(res.group(1)), "down": float(sup.group(1)), "sr": {}}
        return None
    sr = {}
    for m in re.finditer(r"\b([SR][1-3])\s*:\s*\$?(\d{3,5}(?:\.\d+)?)", t, re.I):
        sr[m.group(1).upper()] = float(m.group(2))
    return {"up": float(up.group(1)) if up else None,
            "down": float(dn.group(1)) if dn else None, "sr": sr}


def arm_bracket(norm, msg_id, text, st):
    """Store the plan; a crossing in manage_armed() opens the trade."""
    bp = text_bracket(text)
    if not bp or not (bp["up"] or bp["down"]):
        return None
    inst = norm.get("instrument") or ""
    if not inst:
        return None
    aid = f"AR{msg_id}"
    plan = {"id": aid, "msg_id": msg_id, "instrument": inst,
            "up": bp["up"], "down": bp["down"], "sr": bp["sr"],
            "session": us_session_for_today().isoformat(), "opened": now()}
    st.setdefault("armed", {})[aid] = plan
    ledger_row({"event": "ARMED_BRACKET", "msg_id": msg_id, "instrument": inst,
                "targets": "|".join(f"{k}={v:g}" for k, v in bp["sr"].items()),
                "reason": f"conditional: long >= {bp['up']}, short <= {bp['down']}",
                "raw": (text or "")[:80]})
    push("🎯 BRACKET ARMED (msg %s): %s — long above %s / short below %s "
         "(levels %s). Fires once on a cross this session."
         % (msg_id, inst, bp["up"], bp["down"],
            ", ".join(f"{k}:{v:g}" for k, v in bp["sr"].items()) or "—"), st)
    return plan


def manage_armed(st):
    """Fire armed brackets on a level cross, expire them at the session end."""
    armed = st.get("armed") or {}
    if not armed:
        return
    sess = us_session_for_today().isoformat()
    for aid, pl in list(armed.items()):
        if pl.get("session") != sess or open_hours_between(pl.get("opened")) > 6:
            armed.pop(aid, None)
            ledger_row({"event": "BRACKET_EXPIRED", "msg_id": pl.get("msg_id"),
                        "instrument": pl.get("instrument"),
                        "reason": "session ended / 6 market hours passed"})
            continue
        try:
            px = _under_quote(_canon_underlying(pl["instrument"]) or pl["instrument"])
        except Exception:
            px = None
        if not px:
            continue
        px = float(px)
        side = None
        if pl.get("up") and px >= pl["up"]:
            side = "long"
        elif pl.get("down") and px <= pl["down"]:
            side = "short"
        if not side:
            continue
        ups = sorted(v for v in pl["sr"].values() if v > px)
        dns = sorted((v for v in pl["sr"].values() if v < px), reverse=True)
        # the bracket's own opposite trigger is the stop: short below 28710
        # stops above 28710, long above 28880 stops below it
        stop = (pl.get("down") if side == "long" else pl.get("up"))
        if not stop:
            stop = (dns[0] if dns else None) if side == "long"                 else (ups[0] if ups else None)
        targets = (ups[:2] if side == "long" else dns[:2])
        card = {"instrument": pl["instrument"], "direction": side,
                "entry": px, "stop": stop, "targets": targets,
                "confidence": 55, "horizon": "intraday",
                "rationale": f"armed bracket from msg {pl.get('msg_id')}",
                "spread": None, "option": None, "action": "entry"}
        armed.pop(aid, None)                       # one shot per plan
        try:
            open_trade_bb(card, str(pl.get("msg_id")), st)
        except Exception as e:
            log(f"bracket {aid} fire failed: {type(e).__name__}: {e}")
        save_state(st)


def _positions_now():
    """{occ: qty} from the broker; {} on any failure."""
    try:
        import urllib.request as _u
        from james_exec import TradierConfig
        cfg = TradierConfig.load("sandbox")
        req = _u.Request(f"{cfg.base_url}/accounts/{cfg.account_id}/positions",
                         headers={"Authorization": f"Bearer {cfg.access_token}",
                                  "Accept": "application/json"})
        j = json.loads(_u.urlopen(req, timeout=15).read())
        out = {}
        for p in ((j.get("positions") or {}).get("position") or []):
            out[str(p.get("symbol") or "").upper().replace(" ", "")] = int(
                float(p.get("quantity") or 0))
        return out
    except Exception:
        return None


_CLOSE_SRC = {}            # (sym, day) -> "official" | "LSE tape" (D142 provenance)
_LSE_TRIED = {}            # (sym, day) -> attempts, bounded at 5 (D142)


def _tradier_daily_close(sym, day):
    """Official daily close from Tradier prod history; None if unavailable.
    SPX/QQQ carry history; RUT does NOT (probed EMPTY 2026-11-21)."""
    try:
        import urllib.request as _u
        from james_exec import TradierConfig
        cfg = TradierConfig.load("prod")
        _d0 = _date.fromisoformat(day)
        req = _u.Request(f"{cfg.base_url}/markets/history?symbol={sym}"
                         f"&interval=daily&start={(_d0 - timedelta(days=1)).isoformat()}"
                         f"&end={(_d0 + timedelta(days=1)).isoformat()}",
                         headers={"Authorization": f"Bearer {cfg.access_token}",
                                  "Accept": "application/json"})
        d = (json.loads(_u.urlopen(req, timeout=15).read()).get("history")
             or {}).get("day") or []
        for row in d:
            if str(row.get("date")) == day:
                return float(row["close"])
        return None
    except Exception:
        return None


def _lse_index_close(sym, day):
    """D142: the closing index level for `day` from LSE's REAL option prints
    — every print carries the underlying's level and the last print of the
    session sits at the frozen close. The RUT fallback: Tradier serves no RUT
    history (EMPTY probe) and BlackBull's RUT print is the D133 garbage (an
    SPX level). A real print beats any proxy ratio: the tape closed RUT at
    2,581.79 on 11-20 while IWM x 10.2 guessed 2,617 — 35 points off (the
    true ratio is ~10.06, which is exactly what the tape and IWM agree on).
    Bounded to 5 attempts per (sym, day): a permanently-missing date must
    never become a call storm. None = retry later, then unsettleable."""
    ck = (sym, day)
    if _LSE_TRIED.get(ck, 0) >= 5:
        return None
    _LSE_TRIED[ck] = _LSE_TRIED.get(ck, 0) + 1
    try:
        import lse as _lm
        from lse_flow import _key as _lk
        _d0 = _date.fromisoformat(day)
        rows = _lm.LSE(_lk()).options_flow(
            underlying=sym, start=day,
            end=(_d0 + timedelta(days=1)).isoformat(),
            min_premium=200, limit=500)
        px = sorted((str(r.get("ts") or ""), float(r["underlying_price"]))
                    for r in (rows or []) if r.get("underlying_price"))
        if not px:
            return None
        val = px[-1][1]
        _CLOSE_SRC[ck] = "LSE tape"
        log(f"expiry close {sym} {day}: LSE tape {val:,.2f} "
            f"(last print {px[-1][0][:19]}, attempt {_LSE_TRIED[ck]})")
        return val
    except Exception as e:
        log(f"expiry close {sym} {day}: LSE fallback failed {type(e).__name__}")
        return None


def _expiry_close(sym, day):
    """Official close on the expiry date; None if unavailable. D142: for the
    RUT family (no Tradier history, garbage BlackBull print) falls back to
    the LSE tape's real prints, and records which source answered."""
    v = _tradier_daily_close(sym, day)
    if v is not None:
        _CLOSE_SRC[(sym, day)] = "official"
        return v
    if _canon_underlying(sym) == "RUT":
        return _lse_index_close(sym, day)     # sets _CLOSE_SRC on success
    return None


def settle_expired(st):
    """An EXPIRED option cannot fill a resting close order. Once the broker no
    longer holds the legs, book it at intrinsic from the official expiry close
    instead of retrying a dead order forever (BLK/PLTR/AD 7700C)."""
    pos = _positions_now()
    if pos is None:
        return
    for t in list(st.get("open", {}).values()):
        exp = str(t.get("expiry") or "")
        if not exp or exp >= us_today().isoformat():
            continue
        if t.get("status") not in ("open_tradier", "close_pending"):
            continue
        legs = t.get("legs") or []
        if any(_occ_key(t, l) in pos for l in legs):
            continue                       # still at the broker: keep closing
        if t.get("close_order_id"):
            try:
                _TRADIER().cancel(t["close_order_id"])
            except Exception:
                pass
        _ost, _ = (None, None)
        if t.get("order_id"):
            try:
                _ost, _ = _tradier_close_status(t["order_id"])
            except Exception:
                _ost = None
        if _ost and any(x in str(_ost).upper() for x in
                        ("EXPIRE", "REJECT", "CANCEL")) and not legs:
            t.update(pnl=0.0, status="closed", exit_price=t.get("entry"),
                     fill_note=f"entry order {_ost}, never held — voided")
        elif _ost and any(x in str(_ost).upper() for x in
                          ("EXPIRE", "REJECT", "CANCEL")) and legs:
            # entry never filled even though a structure was written: the
            # resting close is a phantom, not an intrinsic settlement
            t.update(pnl=0.0, status="closed", exit_price=t.get("entry"),
                     fill_note=f"entry order {_ost}, never held — phantom voided")
        elif "expired" in str(t.get("fill_note") or "").lower() and not legs:
            t.update(pnl=0.0, status="closed", exit_price=t.get("entry"),
                     fill_note="expired unfilled — phantom voided")
        else:
            S = _expiry_close(_canon_underlying(t.get("instrument")) or
                              str(t.get("instrument") or "").split()[0], exp)
            if S is None:
                continue                   # no print yet; try next cycle
            if legs:
                pnl = _spread_expiry_pnl(legs, t.get("entry"), t.get("qty") or 0, S)
            else:
                occ = _occ_key(t, None)
                tmult = 1 if t.get("direction") == "long" else -1
                q = None
                try:
                    strike = float(occ[-8:]) / 1000
                    right = "call" if occ[-9] == "C" else "put"
                    q = max(S - strike, 0) if right == "call" else max(strike - S, 0)
                except Exception:
                    q = None
                pnl = round(tmult * ((q or 0) - (t.get("entry") or 0)) * 100 *
                            (t.get("qty") or 0), 2)
            t.update(pnl=pnl, exit_price=S, status="closed",
                     fill_note=f"expired {exp} — settled intrinsic vs "
                               f"{_canon_underlying(t.get('instrument')) or '?'} {S:g}")
        t["exit_ts"] = now()
        st.setdefault("closed", {})[t["id"]] = t
        st.get("open", {}).pop(t["id"], None)
        ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
        _lc_finish(t, "EXIT", f"settled at expiry: {t['fill_note']}")   # D128
        push(f"⌛ SETTLED AT EXPIRY {t['id']} {t['instrument']} — "
             f"{t['fill_note']} | PnL ${(t.get('pnl') or 0):+.2f}", st)
        log(f"expired settlement {t['id']}: {t['fill_note']}")


def _occ_key(t, leg):
    """OCC string for a stored leg (or the single-leg opt_id)."""
    if leg is None:
        return str(t.get("opt_id") or "").upper().replace(" ", "")
    try:
        strike, right, _ = leg
        return opt_identifier(str(t.get("symbol") or t.get("instrument") or
                                  t.get("opt_underlying") or "").split()[0],
                              str(t.get("expiry") or ""), right,
                              float(strike)).upper().replace(" ", "")
    except Exception:
        return ""


def _warn_entry_fill(t, fill, sig, st):
    """D139: an ENTRY fill far from the SIGNAL price is booked but said out
    loud — D129 only watched exits, so MRNA filled +29% over signal and WMT
    -48% under it with no word either way. Alert only: the fill is real and
    never undone; the point is that the divergence is visible the moment it
    books. Once per trade. Threshold 25% matches rebase_levels' own card_far
    rule — 30% would have missed MRNA's +29.3%, the case that motivated this."""
    if t.get("_div_warned"):
        return
    try:
        s, f = float(sig), float(fill)
    except (TypeError, ValueError):
        return
    if s <= 0 or f <= 0:
        return
    d = (f - s) / s
    if abs(d) <= 0.25:
        return
    t["_div_warned"] = True
    _why = (f"entry fill diverged from signal: signal {s:g} -> fill {f:g} "
            f"({d:+.0%}) — booked at the fill (D139)")
    ledger_row({**t, "event": "ALERT", "reason": _why})
    log(_why)
    push(f"⚠️ ENTRY FILL DIVERGED {t.get('instrument') or ''} {t.get('id')}: "
         f"signal {s:g} → fill {f:g} ({d:+.0%}) — booked at the real fill (D139).", st)


def manage_once(st):
    """Mark open trades to market; exits on stop/target; pending fill checks."""
    try:
        manage_armed(st)
    except Exception as e:
        log(f"armed pass failed: {type(e).__name__}: {e}")
    try:
        settle_expired(st)
    except Exception as e:
        log(f"expiry settle failed: {type(e).__name__}: {e}")
    for t in list(st.get("open", {}).values()):
        if t["status"] == "pending" and t.get("opt_id"):
            # resting BUY: check the fill; cancel if it goes stale
            if t.get("executor") == "tradier":
                st_o, fill = _tradier_close_status(t.get("order_id"))
                su = (st_o or "").upper()
                if "FILL" in su:
                    t["status"] = "open_tradier"
                    _sig_e = t.get("entry")  # D139: signal, before rebase overwrites
                    rebase_levels(t, fill)
                    t["fill_note"] = f"entry filled on recheck @ {fill}"
                    _warn_entry_fill(t, fill, _sig_e, st)
                    ledger_row({**t, "event": "ORDER", "reason": t["fill_note"]})
                    save_state(st)
                    continue
                if any(x in su for x in ("REJECT", "CANCEL", "EXPIRED", "FAILED")):
                    t["status"], t["pnl"] = "closed", 0.0
                    t["fill_note"] = f"entry order {su} — no position taken"
                    st.setdefault("closed", {})[t["id"]] = t
                    st.get("open", {}).pop(t["id"], None)
                    ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
                    _lc_finish(t, "EXIT", t["fill_note"])   # D128
                    push(f"⚠️ option entry {su} {t['id']} — no position, retired", st)
                    save_state(st)
                    continue
            else:
                check_fill(t)
            if t["status"] == "pending":
                if not market_open_now():
                    if t.get("status") != "queued":
                        # keep it resting across the shut hours; the timer does
                        # not move (open_hours_between already returns 0 here)
                        t["fill_note"] = ("entry queued — market shut, will fill "
                                          "on the open" if t.get("order_id")
                                          else "entry queued — market shut")
                        log(f"opt entry queued (market shut) {t['id']}")
                    continue
                age_h = open_hours_between(t.get("opened", now()))
                if age_h > CFG["max_open_retry_hours"]:
                    if t.get("executor") == "tradier":
                        try:
                            _TRADIER().cancel(t.get("order_id"))
                        except Exception as e:
                            log(f"opt stale cancel failed {t['order_id']}: {e}")
                    else:
                        cfg_t, trade, _ = tiger()
                        if trade and t.get("order_id"):
                            try:
                                trade.cancel_order(id=t["order_id"])
                            except Exception as e:
                                log(f"opt stale cancel failed {t['order_id']}: {e}")
                    t["status"], t["pnl"] = "closed", 0.0
                    t["fill_note"] = (f"entry unfilled after {age_h:.1f}h — "
                                      f"cancelled, no position")
                    st.setdefault("closed", {})[t["id"]] = t
                    st.get("open", {}).pop(t["id"], None)
                    ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
                    _lc_finish(t, "EXIT", t["fill_note"])   # D128
                    log(f"opt entry expired {age_h:.1f}h: {t['opt_id']}")
                    push(f"⌛ EXPIRED {t['instrument']} [OPT] — never filled\n"
                         f"{t['fill_note']}", st)
                    save_state(st)
            continue
        if t["status"] == "close_pending" and t.get("cancel_close"):
            # a close we know is WRONG: retry the cancel until it sticks — but
            # BACK OFF. TL3731 hammered this every ~65s for days, and every
            # listener hole we found (3734/3735, 3854-3857, 3806, 3908-3910)
            # sits inside one of those retry storms.
            _tries = t.get("cancel_tries", 0)
            _wait = min(60 * (2 ** (_tries // 5)), 900)
            if time.time() - t.get("cancel_at", 0) < _wait:
                continue
            t["cancel_at"] = time.time()
            try:
                ok_c, why_c = _TRADIER().cancel(t.get("close_order_id"))
            except Exception as e:
                ok_c, why_c = False, str(e)
            if ok_c:
                t.pop("close_order_id", None)
                t["close_reason"], t["cancel_close"] = None, False
                t["status"] = ("open_tradier_stock"
                               if t.get("executor") == "tradier_stock"
                               else "open_tradier")
                t["fill_note"] = "wrong close cancelled — position back to open"
                ledger_row({**t, "event": "ALERT", "reason": t["fill_note"]})
                push(f"↩️ WRONG CLOSE CANCELLED {t['id']} — position back to open", st)
                save_state(st)
            else:
                st_o, _ = _tradier_close_status(t.get("close_order_id"))
                su = (st_o or "").upper()
                if "FILL" in su:
                    t["cancel_close"] = False
                    t["fill_note"] = ("⚠️ WRONG CLOSE FILLED despite cancel retry — "
                                      "re-open manually if needed")
                    push(t["fill_note"] + f" ({t['id']})", st)
                else:
                    t["cancel_tries"] = t.get("cancel_tries", 0) + 1
                    log(f"cancel retry {t['id']}: {why_c} ({su or 'no status'})")
                    if t["cancel_tries"] >= 20:
                        # give up retrying: 20 attempts is days of a close that
                        # cannot cancel. Leave it resting; open_reconcile owns it.
                        t["cancel_close"], t["cancel_stuck"] = False, True
                        t["fill_note"] = (f"cancel stuck after 20 tries ({why_c}) — "
                                          f"not retried, close left resting")
                        ledger_row({**t, "event": "ALERT", "reason": t["fill_note"]})
                        push(f"🟠 CANCEL STUCK {t['id']} after 20 tries — stop "
                             f"retrying, the resting close stays; resolve at the "
                             f"open", st)
                    if t["cancel_tries"] == 3:
                        push(f"🟠 CANCEL STUCK on {t['id']} ({su or 'PendingNew'}) — "
                             f"broker won't cancel pre-route; the close MAY fill at "
                             f"the open. Check this position manually.", st)
            continue
        if (t["status"] == "close_pending" and not t.get("cancel_close")
                and t.get("close_order_id") and t.get("lim_at")
                and int(t.get("lim_refresh") or 0) < 3
                and time.time() - float(t.get("lim_at") or 0) >= 60):
            # D137 REFRESH: a resting close unfilled a minute on was refused
            # by its band (or the tape moved) — cancel ONCE and re-price with
            # fresh marks so the limit chases the market. Two re-prices then
            # market-escape (3rd placement has _lim_escape): an exit may wait
            # a cycle, never strand. Bounded at 3 cancels per close event —
            # the TL3731 lesson is that uncancellable retry storms are how
            # every listener hole we found got starved.
            _st_c, _f_c = _tradier_close_status(t.get("close_order_id"))
            if _st_c and "FILL" in _st_c.upper():
                check_tradier_close(t, st)       # it DID fill — book it
                continue
            _r = int(t.get("lim_refresh") or 0)
            try:
                ok_c, why_c = _TRADIER().cancel(t.get("close_order_id"))
            except Exception as e:
                ok_c, why_c = False, str(e)
            if not ok_c:
                t["lim_at"] = time.time()        # back off another cycle
                log(f"D137 refresh {t['id']}: cancel failed ({why_c}) — stays resting")
                check_tradier_close(t, st)
                continue
            t.pop("close_order_id", None)
            t["lim_refresh"] = _r + 1
            t.pop("lim_at", None)
            if _r + 1 >= 3:
                t["_lim_escape"] = True          # final placement: market
            if t.get("lim_qty"):
                t["close_qty"] = t["lim_qty"]    # honour the original (partial) size
            t["status"] = ("open_tradier_stock" if t.get("executor") == "tradier_stock"
                           else "open_tradier")
            _rs = t.get("close_reason") or "stop/target"
            t["_reprice"] = True
            close_trade(t, 0.0, f"{_rs} (D137 reprice {_r + 1}"
                                f"{' market-escape' if t.get('_lim_escape') else ''})", st)
            log(f"D137 refresh {t['id']}: reprice {_r + 1}"
                f"{' (market escape)' if t.get('_lim_escape') else ''}")
            continue
        if t["status"] == "close_pending":
            # resting Tradier close: book only on the real fill
            check_tradier_close(t, st)
            continue
        if t.get("green_when") and str(t["status"]).startswith("open"):
            try:
                _S = bbc().get_quote("SPX500") \
                    if _canon_underlying(t.get("instrument")) == "SPX" else quote_for(t)
            except Exception:
                _S = None
            if _S and float(_S) >= t["green_when"]["level"]:
                close_trade(t, 0.0,
                            f"green-when triggered (SPX {float(_S):,.2f} >= "
                            f"{t['green_when']['level']:,.2f}, msg "
                            f"{t['green_when']['msg']})", st)
                continue
        if t.get("exit_target") and str(t["status"]).startswith("open"):
            # EXIT LADDER: close when the armed level is reached
            hit, px = _exit_target_hit(t)
            if hit:
                # D129: stash only for UNDERLYING trades — px is the underlying
                # quote while an option/spread fill is a premium (unit mismatch
                # would false-warn). Option fills get their premium mark from
                # the bracket paths above.
                if px and not (t.get("opt_id") or t.get("legs")):
                    t["_exit_mark"] = px
                close_trade(t, 0.0,
                            f"level target {t['exit_target']['level']:g} hit "
                            f"(msg {t['exit_target']['msg']})", st)
                continue
        if t["status"] == "open_tradier" and t.get("legs") and (
                t.get("stop") or t.get("targets")):
            # spread stop: the mark is the DEBIT TO CLOSE (buy back the short leg,
            # sell the long one), so a loss shows as a bigger debit
            mk = spread_mark(t)
            if mk is not None:
                t["_exit_mark"] = mk      # D129: decision mark for fill check
            if mk is not None and mk >= t["stop"]:
                close_trade(t, t["stop"],
                            f"stop hit ({mk:.2f} >= {t['stop']:.2f})", st)
                continue
            if t.get("targets"):
                _tg = float(t["targets"][0])
                if mk is not None and mk <= _tg:
                    close_trade(t, _tg,
                                f"target hit ({mk:.2f} <= {_tg:.2f})", st)
                    continue
        if t["status"] == "pending_entry":
            # resting multileg entry: book only on a real fill
            st_o, fill = _tradier_close_status(t.get("order_id"))
            st_u = (st_o or "").upper()
            if "FILL" in st_u:
                # D104: re-gate at the open. The entry was judged at parse
                # time; the open may have moved against it.
                if _fill_regate(t, st):
                    # D130 (2026-12-20→2026-11-20): this branch only runs on
                    # FILL status — a cancel cannot un-fill it. The old code
                    # booked "cancelled, no position" WITHOUT checking the
                    # cancel result: TL4500/4503 filled at the broker, the book
                    # said flat, reconcile flagged UNBOOKED all evening.
                    # Three-way outcome now, each verified against the broker:
                    #   filled    -> keep the REAL position (stop protects)
                    #   canceled  -> genuinely no position, book zero + EXIT
                    #   unconfirmed -> leave resting, retry next cycle
                    ok_c = False
                    try:
                        ok_c, _ = _TRADIER().cancel(t.get("order_id"))
                    except Exception:
                        ok_c = False
                    st_after, fill_after = _tradier_close_status(t.get("order_id"))
                    still_filled = "FILL" in (st_after or "").upper()
                    if still_filled:
                        _f = fill_after or fill
                        t["status"] = "open_tradier"
                        rebase_levels(t, _f)
                        if not t.get("stop"):
                            _fs = synth_spread_stop(t.get("legs") or [], t.get("entry"))
                            if _fs:
                                t["stop"] = _fs
                        t["fill_note"] = (f"fill gate: order already FILLED @ {_f} — "
                                          f"cancel cannot un-fill; kept open with "
                                          f"stop {t.get('stop')} (D130)")
                        ledger_row({**t, "event": "ORDER", "reason": t["fill_note"]})
                        push(f"🛑 FILL GATE (D130) {t['id']}: entry FILLED at the "
                             f"broker before the cancel landed — keeping the real "
                             f"position with stop {t.get('stop')} (book mirrors "
                             f"broker now)", st)
                        log(f"fill gate: {t['id']} already FILLED @ {_f} — kept open (D130)")
                        save_state(st)
                        continue
                    if ok_c:
                        t["status"], t["pnl"] = "closed", 0.0
                        t["fill_note"] = ("fill gate cancelled the resting entry "
                                          "pre-open (moved against us, D104)")
                        st.setdefault("closed", {})[t["id"]] = t
                        st.get("open", {}).pop(t["id"], None)
                        ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
                        _lc_finish(t, "EXIT", t["fill_note"])   # D128
                        log(f"fill gate cancelled {t['id']} pre-open (confirmed)")
                        save_state(st)
                        continue
                    # cancel unconfirmed and no fill visible — stay resting
                    t["fill_note"] = ("fill gate: cancel unconfirmed, no fill "
                                      "visible — retrying next cycle (D130)")
                    log(f"fill gate: {t['id']} cancel unconfirmed — stays pending")
                    save_state(st)
                    continue
                t["status"] = "open_tradier"
                _sig_e = t.get("entry")      # D139: signal, before rebase overwrites
                rebase_levels(t, fill)
                # D82: a spread that filled without a known credit (image-only,
                # TL4503) gets its stop from the FILL price now that we have one
                # — otherwise it fills with no exit protection at all.
                if not t.get("stop"):
                    _fs = synth_spread_stop(t.get("legs") or [], t.get("entry"))
                    if _fs:
                        t["stop"] = _fs
                        log(f"{t['id']}: stop synthesised from fill "
                            f"({_fs:g} = 1.4x {t.get('entry')})")
                t["fill_note"] = f"entry filled on recheck @ {fill}"
                _warn_entry_fill(t, fill, _sig_e, st)
                ledger_row({**t, "event": "ORDER", "reason": t["fill_note"]})
                log(f"pending_entry filled {t['id']}")
                save_state(st)
            elif any(x in st_u for x in ("REJECT", "CANCEL", "EXPIRED", "FAILED")):
                t["status"], t["pnl"] = "closed", 0.0
                t["fill_note"] = f"entry order {st_u} — no position taken"
                st.setdefault("closed", {})[t["id"]] = t
                st.get("open", {}).pop(t["id"], None)
                ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
                _lc_finish(t, "EXIT", t["fill_note"])   # D128
                push(f"⚠️ MULTI-LEG entry {st_u} {t['id']} — no position, retired", st)
                log(f"pending_entry terminal {st_u} {t['id']}")
                save_state(st)
            elif not market_open_now():
                if t.get("fill_note", "").find("queued") < 0:
                    t["fill_note"] = "entry queued — market shut, fills on the open"
                    log(f"multileg entry queued (market shut) {t['id']}")
                continue
            elif open_hours_between(t.get("opened", now())) > CFG["max_open_retry_hours"]:
                try:
                    ok_c, why_c = _TRADIER().cancel(t.get("order_id"))
                except Exception as e:
                    ok_c, why_c = False, str(e)
                t["status"], t["pnl"] = "closed", 0.0
                t["fill_note"] = (f"entry unfilled >{CFG['max_open_retry_hours']} MARKET h — "
                                  f"cancel {'ok' if ok_c else 'FAILED: ' + why_c}, "
                                  f"no position")
                st.setdefault("closed", {})[t["id"]] = t
                st.get("open", {}).pop(t["id"], None)
                ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
                _lc_finish(t, "EXIT", t["fill_note"])   # D128
                push(f"⌛ MULTI-LEG entry expired unfilled ({t['id']}) — "
                     f"cancelled, no position\n{t['fill_note']}", st)
                log(f"pending_entry stale {t['id']} cancelled ({ok_c})")
                save_state(st)
            continue
        if t["status"] == "open_tradier" and t.get("close_reason"):
            # close intent left by an expired/cancelled order: re-arm every 10 min
            if time.time() - t.get("close_retry_at", 0) >= 600:
                t["close_retry_at"] = time.time()
                close_trade(t, 0.0, t["close_reason"], st)
            continue
        if t["status"] == "open_tradier" and t.get("opt_id"):
            # single TRADIER option: best-effort bracket check (opt_mark may be
            # permission-denied -> skip this cycle)
            mark = opt_mark(t)
            if mark is None:
                continue
            t["_exit_mark"] = mark      # D129: decision mark for fill check
            if t.get("direction") == "long":
                if t["stop"] and mark <= t["stop"] and not t.get("hold_until_expiry"):
                    close_trade(t, t["stop"],
                                f"stop hit ({mark:.2f} <= {t['stop']:.2f})", st)
                elif t["targets"] and mark >= t["targets"][0] and not t.get("hold_until_expiry"):
                    close_trade(t, t["targets"][0],
                                f"target hit ({mark:.2f} >= {t['targets'][0]:.2f})", st)
            else:                        # short premium: stop UP, target DOWN
                if t["stop"] and mark >= t["stop"] and not t.get("hold_until_expiry"):
                    close_trade(t, t["stop"],
                                f"stop hit ({mark:.2f} >= {t['stop']:.2f})", st)
                elif t["targets"] and mark <= t["targets"][0] and not t.get("hold_until_expiry"):
                    close_trade(t, t["targets"][0],
                                f"target hit ({mark:.2f} <= {t['targets'][0]:.2f})", st)
            continue
        if t["status"] == "pending" and t.get("executor") == "tradier_stock":
            st_o, fill = _tradier_close_status(t.get("order_id"))
            su = (st_o or "").upper()
            if "FILL" in su:
                t["status"] = "open_tradier_stock"
                rebase_levels(t, fill)
                ledger_row({**t, "event": "ORDER", "reason": f"fill recheck @ {t['entry']}"})
                save_state(st)
            elif any(x in su for x in ("REJECT", "CANCEL", "EXPIRED", "FAILED")):
                t["status"], t["pnl"] = "closed", 0.0
                t["fill_note"] = f"entry {su} — no position"
                st.setdefault("closed", {})[t["id"]] = t
                st.get("open", {}).pop(t["id"], None)
                ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
                _lc_finish(t, "EXIT", t["fill_note"])   # D128
                save_state(st)
            continue
        if t["status"] == "pending":
            check_fill(t)
            continue
        if t["status"] == "pending_open":
            retry_open_bb(t, st)
            continue
        if t["status"] == "pending_close":
            # closing order is resting: book it only when it truly fills
            _, trade, _ = tiger()
            if trade is None:
                continue
            st_close, fill = _order_fill(trade, t.get("close_order_id"))
            if fill and st_close and "FILLED" in st_close:
                book_option_close(t, fill, t.get("close_qty") or t["qty"],
                                  t.get("close_reason") or "close", st)
            elif st_close and any(x in st_close for x in
                                  ("CANCEL", "REJECT", "EXPIRED", "FAILED")):
                log(f"opt close order {st_close} — back to open, will retry")
                t.pop("close_order_id", None)
                t["status"] = "open_opt"
                t["fill_note"] = f"close order {st_close}, position still open"
                save_state(st)
            continue
        if t["status"] == "order_failed":
            # zombie: rejected order in the book — retire after 6h
            age_h = age_hours(t.get("opened", now()))
            if age_h >= 6:
                t["status"], t["fill_note"] = "closed", f"zombie order_failed retired ({age_h:.0f}h)"
                t["pnl"] = 0.0
                st.setdefault("closed", {})[t["id"]] = t
                st.get("open", {}).pop(t["id"], None)
                ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
                _lc_finish(t, "EXIT", t["fill_note"])   # D128
                log(f"retired zombie {t['id']}")
            continue
        if t["status"] == "unsupported":
            # contract not tradeable at all — retire immediately, no PnL
            t["status"], t["pnl"] = "closed", 0.0
            t["fill_note"] = t.get("fill_note") or "contract not tradeable"
            st.setdefault("closed", {})[t["id"]] = t
            st.get("open", {}).pop(t["id"], None)
            ledger_row({**t, "event": "EXIT", "reason": t["fill_note"]})
            _lc_finish(t, "EXIT", t["fill_note"])   # D128
            log(f"retired unsupported {t['id']}")
            continue
        if t["status"] == "open_opt":
            # premium marking via Tiger briefs (delayed OK for paper)
            mark = opt_mark(t)
            if mark is None:
                continue
            t["_exit_mark"] = mark      # D129: decision mark for fill check
            if t["stop"] and mark <= t["stop"]:
                close_option(t, t["stop"], t["qty"] or 1,
                             f"stop hit ({mark:.2f} <= {t['stop']:.2f})", st)
            elif t["targets"] and mark >= t["targets"][0]:
                close_option(t, t["targets"][0], t["qty"] or 1,
                             f"target hit ({mark:.2f} >= {t['targets'][0]:.2f})", st)
            continue
        if t["status"] not in ("open_tiger", "open_sim", "open_dry", "open_bb",
                               "open_tradier_stock"):
            continue
        price = quote_for(t)
        if price is None:
            continue
        t["_exit_mark"] = price         # D129: decision mark for fill check
        if t["direction"] == "long":
            if t["stop"] and price <= t["stop"]:
                close_trade(t, t["stop"], f"stop hit ({price:.2f} <= {t['stop']:.2f})", st)
            elif t["targets"] and price >= t["targets"][0]:
                close_trade(t, t["targets"][0], f"target hit ({price:.2f} >= {t['targets'][0]:.2f})", st)
        else:
            if t["stop"] and price >= t["stop"]:
                close_trade(t, t["stop"], f"stop hit ({price:.2f} >= {t['stop']:.2f})", st)
            elif t["targets"] and price <= t["targets"][0]:
                close_trade(t, t["targets"][0], f"target hit ({price:.2f} <= {t['targets'][0]:.2f})", st)
    # closes booked but not yet filled (records already in closed): reconcile
    for t in list(st.get("closed", {}).values()):
        if t.get("status") == "close_pending":
            check_tradier_close(t, st)
        elif (t.get("status") == "open_tradier" and t.get("close_reason")
              and time.time() - t.get("close_retry_at", 0) >= 600):
            t["close_retry_at"] = time.time()
            close_trade(t, 0.0, t["close_reason"], st)
    save_state(st)


def read_lines(p):
    if not p.exists():
        return []
    out = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    p.write_text("", encoding="utf-8")
    return out


def consume_close(st):
    for line in read_lines(CLOSE):
        if line.lower() == "all":
            ids = list(st.get("open", {}).keys())
        else:
            ids = [line] if line in st.get("open", {}) else []
        for tid in ids:
            t = st["open"][tid]
            price = quote_for(t)
            close_trade(t, price or t["entry"] or 0, "manual (close.txt)", st)


def strike_frame(text, ref):
    """Room between the named SHORT strike and price, in % (positive = OTM).
    Bear calls only keep paying while price stays BELOW the strike, bull puts
    while it stays ABOVE — so direction alone is not the whole claim."""
    ks = text_strikes(text)
    if not ks or not ref:
        return None, None
    fam = _structure_family(text)
    if fam == "C":
        return round((min(ks) - float(ref)) / float(ref) * 100, 2), "C"
    if fam == "P":
        return round((float(ref) - max(ks)) / float(ref) * 100, 2), "P"
    return None, fam


def record_prediction(norm, msg_id, text):
    """Every PREDICTION message becomes a scored row, not just a log line — the
    outcome is settled automatically later. Never raises."""
    try:
        t = (text or "").strip()
        if not t:
            return
        # de-dupe: James re-posts the same hold call verbatim through a session
        try:
            if PRED_FILE.exists():
                with PRED_FILE.open(encoding="utf-8") as f:
                    for old in csv.DictReader(f):
                        if (old.get("date") == today()
                                and (old.get("prediction") or "")[:120] == t[:120]):
                            return
        except Exception:
            pass
        low = t.lower()
        lv = text_levels(text)
        if lv:
            ptype = "level"
            level = "|".join(f"{k:g}={r}" for k, r in lv)
        elif re.search(r"\b(bull|bear|long|short|up|down|drop|rally|bounce)\b", low):
            ptype = "direction"
            _tk = [w for w in re.findall(r"\b[A-Z]{1,5}\b", text or "")
                   if w not in _TICK_STOP]
            sym = (_canon_underlying(_tk[0]) if _tk else None)                 or _canon_underlying(norm.get("instrument")) or "SPX"
            _cands = [c for c in (sym, _canon_underlying(norm.get("instrument")),
                                  "SPX") if c]
            sym, px = None, None
            for _c in _cands:      # a non-ticker token (FOMC, NYFP1) must not become the underlying
                try:
                    _q = _under_quote(_c)
                except Exception:
                    _q = None
                if _q:
                    sym, px = _c, _q
                    break
            if not sym:
                sym = _cands[-1]
            level = f"{sym}@{float(px):.2f}" if px else f"{sym}@"
        else:
            ptype, level = "other", ""
        spct = ""
        if ptype == "direction" and px:
            _sp, _ = strike_frame(text, px)
            spct = "" if _sp is None else f"{_sp:g}"
        m = re.search(r"\b(should|will|expect\w*)\b[^.\n]{0,50}", low)
        new = not PRED_FILE.exists()
        with PRED_FILE.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(PRED_HEAD)
            w.writerow([today(), f"msg {msg_id}", t[:160], ptype, level,
                        (m.group(0)[:60] if m else ""), "", "open", "", "",
                        "auto-captured", spct, ""])
    except Exception as e:
        log(f"prediction capture failed msg {msg_id}: {type(e).__name__}: {e}")


def settle_predictions(st):
    """AUTO-SETTLE the machine-checkable predictions (type 'level') once a day
    against the SPX print. Non-level claims stay manual. Never raises."""
    try:
        if not PRED_FILE.exists():
            return
        with PRED_FILE.open(encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if len(rows) < 2:
            return
        head, body = rows[0], rows[1:]
        try:
            spx = bbc().get_quote("SPX500")
        except Exception:
            spx = None
        settled = 0
        for r in body:
            if not spx:
                break
            if len(r) < 11 or (r[7] or "").strip().lower() not in ("open", ""):
                continue
            if "level" not in (r[3] or "").lower() or "direction" in (r[3] or "").lower():
                continue
            blob = (r[4] or "") + " " + (r[5] or "")
            m = re.search(r"(\d{4,5}(?:\.\d+)?)", blob)
            if not m:
                continue
            lvl = float(m.group(1))
            low = blob.lower()
            if re.search(r">|above|over", low) and not re.search(r"<|below|under", low):
                ok = float(spx) > lvl
            elif re.search(r"<|below|under", low):
                ok = float(spx) < lvl
            else:
                continue
            r[6] = f"SPX checked at {float(spx):,.2f} vs level {lvl:g}"
            r[7] = "correct" if ok else "wrong"
            r[10] = f"AUTO {now()} SPX {float(spx):,.2f}"
            settled += 1
        # DIRECTION claims: settle the session AFTER the claim against the
        # underlying's own print; +/-0.25% separates a real move from noise
        for r in body:
            if len(r) < 11 or (r[7] or "").strip().lower() not in ("open", ""):
                continue
            if "direction" not in (r[3] or "").lower():
                continue
            if (r[0] or "") >= today():          # same session, not decided yet
                continue
            m = re.match(r"([A-Z.]{1,8})@([\d.]+)", (r[4] or "").strip())
            if not m:
                continue
            sym, ref = m.group(1), float(m.group(2))
            if not ref:
                continue
            try:
                px = _under_quote(sym)
            except Exception:
                px = None
            if not px:
                continue
            low = (r[2] or "").lower()
            up = bool(re.search(r"\b(bull|long|up|bounce|rally|recover)\b", low))
            dn = bool(re.search(r"\b(bear|short|down|drop|fall)\b", low))
            if up == dn:
                continue
            move = (float(px) - ref) / ref * 100
            ok = move >= 0.25 if up else move <= -0.25
            r[6] = f"{sym} {ref:.2f} -> {float(px):.2f} ({move:+.2f}%)"
            r[7] = "correct" if ok else "wrong"
            r[10] = f"AUTO {now()} {sym} {float(px):.2f}"
            # STRIKE FRAME: did the named short strike survive? A right
            # direction with a blown strike is a losing spread, not a good call
            _ks = text_strikes(r[2] or "")
            if _ks:
                if re.search(r"\bcalls?\b", low):
                    held = float(px) < min(_ks)
                elif re.search(r"\bputs?\b", low):
                    held = float(px) > max(_ks)
                else:
                    held = None
                while len(r) < 13:
                    r.append("")
                r[12] = "" if held is None else str(bool(held))
            settled += 1
        if settled:
            tmp = PRED_FILE.with_suffix(".tmp")
            with tmp.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows([head] + body)
            tmp.replace(PRED_FILE)
            _at = f" at SPX {float(spx):,.2f}" if spx else ""
            push(f"🧮 PREDICTIONS AUTO-SETTLED{_at} — {settled} prediction(s) "
                 f"resolved", st)
            log(f"predictions: auto-settled {settled} row(s)")
    except Exception as e:
        log(f"prediction settle failed: {type(e).__name__}: {e}")


def daily_digest(st):
    """Once a day: liveness proof + book summary to Saved Messages."""
    settle_predictions(st)
    op = list(st.get("open", {}).values())
    lines = [f"🩺 james-pipe daily — {now()} UTC",
             f"open {len(op)}/{CFG['max_open']} | retry_q {len(st.get('parse_retry', {}))} | "
             f"alerts_today {st.get('alerts_today', 0)}"]
    for t in op:
        lines.append(f"  {t['id']} {t['instrument']} {t['direction']} "
                     f"@ {t['entry'] and round(t['entry'], 2)} [{t['status']}]")
    closed = [t for t in st.get("closed", {}).values() if t.get("pnl") is not None]
    tot = sum(t["pnl"] for t in closed)
    lines.append(f"closed {len(closed)} | realized PnL ${tot:+.2f}")
    for tier in ("bankable", "middle", "high"):      # promotion-ladder stats
        td = [t for t in closed if (t.get("tier") == tier)
              and (t.get("exit_ts") or "").startswith(today())]
        if not td:
            continue
        n, w = len(td), sum(1 for t in td if (t.get("pnl") or 0) > 0)
        net = sum(t["pnl"] for t in td)
        lines.append(f"{TIER_BADGE[tier]}: {n} done, {w}W, net ${net:+.2f}, "
                     f"exp ${net / n:+.2f}/trade")
    try:                              # James-vs-us outcome counts
        jr = list(csv.DictReader(JOUT.open(encoding="utf-8"))) if JOUT.exists() else []
        jw = sum(1 for r in jr if r["result"] == "win")
        jl = sum(1 for r in jr if r["result"] == "loss")
        uw = sum(1 for v in closed if (v.get("pnl") or 0) > 0)
        ul = sum(1 for v in closed if (v.get("pnl") or 0) < 0)
        lines.append(f"outcomes today-state — James {jw}W/{jl}L | us {uw}W/{ul}L "
                     f"(james_vs_us.py for full compare)")
    except Exception:
        pass
    push("\n".join(lines), st)
    st["digest_day"] = today()
    save_state(st)


def preflight(st):
    """Startup self-check of the whole execution chain. Pushes a card ALWAYS so a
    silent breakage (like the uninitialised-MT5 bug that killed a whole session)
    is visible immediately instead of being discovered hours later."""
    lines, problems = [], []
    # 1. MT5 terminal + login.
    # BOOT-ORDER RACE (2026-11-18): at login, blackbull_mt5.vbs, the hermes
    # gateway and this pipe all start together. MT5 needs ~20-60s to load, so a
    # first-look preflight used to declare "no terminal/account info" and the
    # session opened blind (the bug that once killed a whole session). Wait for
    # the terminal instead of failing on first sight — bounded, so a genuinely
    # missing terminal still reports clearly.
    _mt5_wait = int(CFG.get("preflight_mt5_wait_s", 120))
    _deadline = time.time() + _mt5_wait
    ti = ai = None
    _attempts = 0
    while True:
        try:
            bbc().connect()
            mt5 = bbc()._mt()
            ti = mt5.terminal_info()
            ai = mt5.account_info()
        except Exception as _e:
            ti = ai = None
        _attempts += 1
        if (ti and ai) or time.time() >= _deadline:
            break
        if _attempts == 1:
            log(f"preflight: MT5 not ready yet — waiting up to {_mt5_wait}s "
                f"(boot-order race)")
        time.sleep(5)
    try:
        if not ti or not ai:
            problems.append(f"MT5 not ready after {_mt5_wait}s")
            lines.append(f"❌ MT5: no terminal/account info after {_mt5_wait}s "
                         f"({_attempts} attempts)")
        else:
            lines.append(f"✅ MT5: {ai.login} @ {ai.server} | "
                         f"algo_trading={'ON' if ti.trade_allowed else 'OFF'}")
            if not ti.trade_allowed:
                problems.append("algo trading disabled in terminal")
            expected = json.loads(bbc().CONFIG.read_text(encoding="utf-8")).get("login")
            if expected and int(ai.login) != int(expected):
                problems.append(f"logged into {ai.login}, expected {expected}")
    except Exception as e:
        problems.append(f"MT5 connect failed: {type(e).__name__}: {e}")
        lines.append(f"❌ MT5: {type(e).__name__}: {str(e)[:80]}")
    # 2. symbol resolution + live quote for the core instruments
    if not problems:
        core = ["SPX500", "NDX100"]
        for inst in core:
            try:
                sym = bbc().resolve_symbol(inst)
                if not sym:
                    problems.append(f"{inst} unresolved")
                    lines.append(f"❌ {inst}: no MT5 symbol")
                    continue
                q = bbc().get_quote(inst)
                si = mt5.symbol_info(sym)
                mode = {0: "disabled", 1: "long-only", 2: "short-only",
                        3: "close-only", 4: "full"}.get(
                            getattr(si, "trade_mode", -1), "?")
                # a close-only symbol can NEVER open a position — that is a
                # failure, not a status note (US500 sat close-only all session)
                if mode != "full":
                    problems.append(f"{inst} maps to {sym} which is {mode} — cannot open trades")
                    lines.append(f"❌ {inst} → {sym} is {mode} (cannot open)")
                elif q:
                    lines.append(f"✅ {inst} → {sym} @ {q:,.2f} ({mode}, "
                                 f"spread {si.spread})")
                else:
                    problems.append(f"{inst} has no quote")
                    lines.append(f"⚠️ {inst} → {sym}: no quote ({mode})")
            except Exception as e:
                problems.append(f"{inst} check failed: {type(e).__name__}")
                lines.append(f"❌ {inst}: {type(e).__name__}")
    # 3. Tiger (options leg) — informational only, never blocks
    try:
        cfg_t, trade, _ = tiger()
        lines.append(f"✅ Tiger: account {cfg_t.account}" if trade
                     else "⚠️ Tiger: unavailable (options ledger-only)")
    except Exception as e:
        lines.append(f"⚠️ Tiger: {type(e).__name__}")
    # 4. Telegram session
    lines.append("✅ Telegram session authorised")
    head = ("🩺 PREFLIGHT OK — execution chain ready"
            if not problems else
            f"🚨 PREFLIGHT FAILED ({len(problems)} issue(s))")
    body = "\n".join(lines)
    if problems:
        body += "\n\n" + "\n".join(f"• {p}" for p in problems)
        body += "\n\nSignals will be LEDGER-ONLY until this is fixed."
    push(f"{head}\n{now()} UTC\n{body}", st)
    log(f"preflight: {'OK' if not problems else 'FAILED — ' + '; '.join(problems)}")
    return not problems


def health_monitor(st):
    """Periodic execution-chain check with transition-based alerting.
    - healthy -> broken: one 🚨 card (after attempting auto-recovery)
    - broken  -> healthy: one ✅ recovery card
    - still broken: reminder every reminder_hours, not every cycle
    Never raises; never blocks the main loop."""
    try:
        ok, detail = bbc().health()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"

    h = st.setdefault("health", {})
    was_ok = h.get("ok", True)          # assume healthy until proven otherwise
    now_ts = time.time()

    if ok:
        if not was_ok:
            down_for = (now_ts - h.get("since", now_ts)) / 60
            push(f"✅ RECOVERED — execution chain healthy again\n"
                 f"was down ~{down_for:.0f} min\n{detail}", st)
            log(f"health recovered after {down_for:.0f} min: {detail}")
        h.update(ok=True, detail=detail, since=None, last_alert=None)
    else:
        if was_ok:
            # first failure: try one recovery pass before alarming
            recovered = False
            try:
                if not bbc().terminal_alive():
                    log("health: terminal gone — relaunching")
                    bbc().launch_terminal()
                    time.sleep(15)
                bbc().connect()
                ok2, detail2 = bbc().health()
                if ok2:
                    recovered = True
                    detail = detail2
            except Exception as e:
                log(f"health recovery attempt failed: {type(e).__name__}: {e}")
            if recovered:
                log(f"health: auto-recovered, no alert needed ({detail})")
                h.update(ok=True, detail=detail, since=None, last_alert=None)
                return
            h.update(ok=False, detail=detail, since=now_ts, last_alert=now_ts)
            push(f"🚨 EXECUTION CHAIN DOWN — auto-recovery failed\n{now()}\n"
                 f"{detail}\n\nSignals are LEDGER-ONLY until this clears.\n"
                 f"I'll retry every {CFG['health_check_minutes']} min and alert "
                 f"again in {CFG['health_reminder_hours']}h if still down.", st)
            log(f"health DOWN: {detail}")
        else:
            last = h.get("last_alert") or h.get("since") or now_ts
            if now_ts - last >= CFG["health_reminder_hours"] * 3600:
                down_h = (now_ts - (h.get("since") or now_ts)) / 3600
                push(f"🚨 STILL DOWN ({down_h:.1f}h) — execution chain\n"
                     f"{detail}\n\nSignals remain LEDGER-ONLY.", st)
                h["last_alert"] = now_ts
                log(f"health still down {down_h:.1f}h: {detail}")
    save_state(st)


# ---------------------------------------------------------------- ingest

_RCV = {}                             # msg_id -> arrival epoch (exit latency)


def process_message(text, msg_id, msg_dt, photo, st, stale=False):
    """DESIGN #1: every exit path must record completion. The inner function
    returns early in many branches (dedupe, stale, link, narrative, park,
    no-trade...), so the completion write is wrapped here rather than repeated
    at ~20 return sites. Without this, `_INFLIGHT` leaked and the message was
    never marked seen — it would be re-parsed on every restart forever."""
    msg_id = str(msg_id)
    try:
        return _process_message_inner(text, msg_id, msg_dt, photo, st, stale=stale)
    finally:
        mark_completed(st, msg_id)
        save_state(st)


def _process_message_inner(text, msg_id, msg_dt, photo, st, stale=False):
    msg_id = str(msg_id)              # telethon ids are ints; state.json keys are strings
    _RCV[msg_id] = time.time()
    if msg_dt:
        _TS[msg_id] = msg_dt          # send epoch for LINK timestamps
    if len(_RCV) > 300:               # keep the map small
        for _k in list(_RCV)[:-200]:
            _RCV.pop(_k, None)
            _TS.pop(_k, None)
    # DESIGN #1: an IN-FLIGHT guard, not the completion record. The message is
    # only written into `seen_msg` when the queue item completes (see
    # mark_completed below). Marking it here meant a crash mid-parse left the
    # signal marked as handled while never having been traded — silent loss.
    with _DEDUPE_LOCK:
        if msg_id in st.get("seen_msg", {}):
            return                      # already completed in a previous run
        if msg_id in _INFLIGHT:
            return                      # already being processed
        _INFLIGHT.add(msg_id)
    # DESIGN (2026-11-18): tag this message AT ARRIVAL — classify it and, where
    # the rule says flow informs the decision, run the LSE check off-thread. The
    # underlying isn't parsed yet, so the tag carries classification + action;
    # the flow verdict fills once parse names the instrument (post-parse re-tag).
    _message_tag_on_arrival(text, msg_id, None)
    age_min = (time.time() - msg_dt) / 60 if msg_dt else 0
    old = age_min > CFG["catchup_max_age_min"]
    # photo-only charts carry the signal in the image — only skip truly empty ones
    if old or (len(text or "") < 10 and not photo):
        ledger_row({"event": "SKIP", "msg_id": msg_id,
                    "reason": f"backlog-old({age_min:.0f}m)" if old else "too-short",
                    "raw": (text or "")[:80]})
        # NO SILENT MISS: a stale signal is surfaced so a human can act, never dropped
        if old and (text or photo):
            push("⏰ STALE SIGNAL (msg %s, %.0fm old) - not auto-traded | price may have moved. Review manually: %s" % (msg_id, age_min, ((text or "(image)")[:140])), st)
        save_state(st)
        return
    # cheap pre-filter: link/promo messages never reach Hermes (saves quota)
    if LINKY.search(text or "") and not KEYWORD.search(text or ""):
        ledger_row({"event": "SKIP", "msg_id": msg_id, "reason": "link/promo",
                    "raw": (text or "")[:80]})
        save_state(st)
        return
    _parse_trade(text, msg_id, photo, st, tries=0, stale=stale)


def _exit_fast(text, st):
    """D138 exit fast-path gate. None = the message must go through hermes;
    else the instrument to inject into the null card ('' = the TEXT already
    carries the scope, brain adds nothing).

    Measured 167-324s from James's exit words to our close order — most of it
    the brain/vision parse of a message that handle_exit_update reads TEXT-FIRST
    anyway ('text ticker beats brain label'). Skipped only when the arrival
    classifier (signal_intent — D126's single definition) says exit/trim AND
    the scope needs no inference: the text names ticker/family/strikes, or the
    open book holds exactly ONE instrument (bare 'take gains' still resolves,
    same unique-thread rule as D128c). Multi-instrument books with bare text
    keep the brain — context decides there, unchanged. The downstream act=='exit'
    routes (exit_command_wins, the gate's non-entry branch, the act-route) all
    read TEXT first, so a null card reaches handle_exit_update and can never
    fall into an opener."""
    t = (text or "").strip()
    if not t:
        return None
    try:
        from signal_intent import classify
        if classify(t) not in ("exit", "trim"):
            return None
    except Exception:
        return None
    if scope_instrument(t) or scope_instruments(t) or scope_family(t) or text_strikes(t):
        return ""
    _inss = {}
    for _pt in (st.get("open") or {}).values():
        # D148 (2026-11-22): an unfilled entry is not exposure — D67's own
        # rule, extended to the fast gate (it counted pending_entry rows, so
        # a book of {SPXW unfilled, QQQ filled} read as 2 instruments and the
        # bare exit was refused). 2+ FILLED instruments still refuse.
        if str(_pt.get("status")) in ("pending_entry", "pending",
                                      "pending_open"):
            continue
        _c = _canon_underlying(_pt.get("instrument")) or _canon_underlying(_pt.get("symbol"))
        if _c:
            _inss[_c] = _pt.get("instrument") or _pt.get("symbol")
    return next(iter(_inss.values())) if len(_inss) == 1 else None


def _parse_trade(text, msg_id, photo, st, tries, stale=False):
    """Hermes parse + execute. Hermes failure = queued for retry (quota/rate limits
    are transient); after max retries it's ledgered as PARSE_FAIL and dropped.
    stale=True (D56): the message is a re-delivery/recovery artifact — its
    timestamp was re-stamped by the redirect, so relative dates ('for today')
    anchor to the WRONG session and the stale gate cannot see it. Entries PARK
    (counterfactual settles in park_outcomes); exits/management still process."""
    # CLARIFICATION ('Read this as Sell 7705 n buy 7715'): the inner command is
    # the trade; parse THAT. The original is kept in the raw columns.
    _rc = re.search(r"read (?:this|it) as\s+(.+)", text or "", re.I | re.S)
    if _rc:
        ledger_row({"event": "CLARIFY", "msg_id": msg_id,
                    "reason": "re-reads a prior message — parsing the inner command",
                    "raw": (text or "")[:120]})
        text = _rc.group(1).strip()
    if stale:
        # D58: a re-delivered message never needs the brain. Its fate is
        # 'park' (entries) or text-classification (everything else) — and a
        # hermes call would burn the free tier on messages we will not trade
        # (the 235-retry storm starved LIVE parses to rate-limit backoff).
        # The null-card path is the rehearsal's proven path (16/16).
        card = {"instrument": None, "direction": "short", "action": "entry",
                "entry": None, "stop": None, "targets": [], "confidence": 0,
                "horizon": "", "rationale": "", "trim": None, "spread": None}
    else:
        _fast = _exit_fast(text, st)
        if _fast is not None:
            # D138 (2026-11-21): deterministic exit → null card, NO hermes/
            # vision call (images skipped too — the text IS the instruction).
            # action="exit" routes through the existing act-routes to
            # handle_exit_update; a photo on an exit is evidence, not input.
            card = {"instrument": (_fast or None), "direction": "short",
                    "action": "exit", "entry": None, "stop": None,
                    "targets": [], "confidence": 0, "horizon": "",
                    "rationale": "text-first exit, brain skipped (D138)",
                    "trim": None, "spread": None}
            log(f"msg {msg_id}: D138 exit fast-path — brain skipped "
                f"({(text or '')[:60]!r})")
        else:
            card = hermes_parse(text, photo)
    if card is None:
        if tries < CFG["max_parse_tries"]:
            st.setdefault("parse_retry", {})[str(msg_id)] = {
                "text": text, "photo": photo, "tries": tries + 1, "at": time.time(),
                # D69: the ARRIVAL time — a queued message must date from when
                # it was sent, never from when it finally parses.
                "seen_at": (st.get("parse_retry", {}).get(str(msg_id)) or {})
                           .get("seen_at") or time.time(),
                "stale": stale}
            log(f"parse queued for retry ({tries + 1}/{CFG['max_parse_tries']}): msg {msg_id}")
        else:
            st.get("parse_retry", {}).pop(str(msg_id), None)
            # D143: record on the HIS axis even when the brain never parsed it
            # — text-only scoring keeps the scorecard complete through a
            # hermes outage (the retry queue will not come back for this id).
            try:
                import signal_scorecard as _sc
                if _sc.enabled():  # D145: never from a test fixture replay
                    _sc.track({}, text or "", msg_id)
            except Exception as _e:
                log(f"scorecard skipped (parse-fail) msg {msg_id}: "
                    f"{type(_e).__name__}: {_e}")
            ledger_row({"event": "PARSE_FAIL", "msg_id": msg_id, "raw": (text or "")[:80]})
        save_state(st)
        return
    st.get("parse_retry", {}).pop(str(msg_id), None)
    # IMAGE EXTRACTION (L4/A4): the screenshot carries qty/expiry/fill/account/
    # exact-pct that text never gives. Pull them via Hermes (vision) and merge
    # into the card so the trade uses real values, not inferred ones.
    # BOUNDED WAIT (img_enrich_timeout): the screenshot qty sizes the trade and
    # nothing else carries it, so give enrichment a window before placement.
    # On timeout the trade proceeds at the safest qty (1) and the thread still
    # fills the card gaps afterwards — never blocked indefinitely.
    if photo and Path(photo).exists():
        def _enrich(card=card, photo=photo, msg_id=msg_id, text=text):
            try:
                f = extract_image_fields(photo, CFG)
                if f:
                    _merge_img_into_card(card, f)
                    log(f"msg {msg_id}: image fields -> {sorted(f)}")
                    # PORTFOLIO SCREENSHOT: a positions screen is a BOOK
                    # SNAPSHOT — capture every row (positions + P&L marks).
                    if f.get("positions"):
                        try:
                            portfolio_capture(f, msg_id, st)
                        except Exception as e:
                            log(f"portfolio capture failed msg {msg_id}: "
                                f"{type(e).__name__}: {e}")
                    # NO MISSED CONTENT: if the image carries a painted message or
                    # level that the TEXT did not have, surface it — never let
                    # image-only content slip past a fast-path text parse.
                    # IMAGE AS EXIT SCOPE: a position screenshot IS the object
                    # ("$120 per contract, pl book gains" names nothing). Keep
                    # its strikes so the exit can bind to the book, never guess.
                    _ks = [float(x) for x in (f.get("strikes") or []) if x]
                    for _row in (f.get("positions") or []):
                        _ks += [float(x) for x in (_row.get("strikes") or []) if x]
                    if _ks:
                        card["img_strikes"] = sorted(set(_ks))
                    # LEG COUNT: how many legs the ticket actually names. A
                    # ticket with MORE legs than we parsed, on an image-only
                    # message, has no text to cross-check -> must park
                    # (msg 3883: 2-leg ticket traded as 1 long leg).
                    _imglegs = len(f.get("legs") or []) or len(set(_ks))
                    if _imglegs:
                        card["img_legs"] = int(_imglegs)
                    # the TICKET also states its credit and size — carry them so
                    # the entry can be cross-checked field by field (D35/3843)
                    if f.get("credit") is not None:
                        card["img_credit"] = f.get("credit")
                    if f.get("qty"):
                        card["img_qty"] = abs(float(f.get("qty")))
                    msg_img = (f.get("message_on_image") or "").strip()
                    lvls = f.get("chart_levels") or []
                    if (msg_img and msg_img.lower() not in (text or "").lower()) or lvls:
                        push("🖼️ IMAGE CONTENT (msg %s): %s | levels: %s | review against the text parse." % (msg_id, msg_img or "-", lvls or "-"), st)
            except Exception as e:
                log(f"image enrichment failed msg {msg_id}: {type(e).__name__}: {e}")
        th = threading.Thread(target=_enrich, daemon=True)
        th.start()
        th.join(timeout=float(CFG.get("img_enrich_timeout", 45)))
    img_fields = {}
    # TICKET-ONLY ENTRY (msg 3593 miss): a screenshot of a "TO OPEN" ticket is
    # order evidence even when the text has no strike/verb ("FTNT Puts making
    # now"). Force the entry and skip the narrative branch.
    _ticket_open = False
    # LEVEL STORE: explicit levels in narrative text ('7711 is target / if it
    # breaks 7711') are captured even when the message itself is no trade —
    # a later entry can be scored against his own zones.
    lv = text_levels(text)
    if lv:
        ledger_row({"event": "LEVELS", "msg_id": msg_id,
                    "instrument": (card.get("instrument")
                                   if isinstance(card, dict) else None),
                    "targets": "|".join(f"{k:g}={role}" for k, role in lv),
                    "reason": "levels captured from text",
                    "raw": (text or "")[:80]})
        log(f"msg {msg_id}: levels captured -> "
            f"{[f'{k:g}:{r}' for k, r in lv]}")
        store_levels(lv, msg_id, source="text",
                      symbol=(card.get("instrument")
                              if isinstance(card, dict) else None))
        # MARKET COLOUR -> LADDER: 'SPX below 7550 now' / 'Big drop on SPX'
        # carry a level that sits right before his next command. Arm it as an
        # exit trigger on any matching open position (exit fires when the level
        # is CROSSED, same as the exit ladder), instead of only storing it.
        # D124: pass the level's instrument so an SPX target cannot arm SOXL/NVDA.
        _armed = arm_colour_levels(lv, text, msg_id, st,
                                   instrument=(card.get("instrument")
                                               if isinstance(card, dict) else None))
    if text_bracket(text):
        arm_bracket(card, msg_id, text, st)
    trig = text_trigger(text)
    if trig:
        ledger_row({"event": "TRIGGER", "msg_id": msg_id,
                    "instrument": (card.get("instrument")
                                   if isinstance(card, dict) else None),
                    "reason": f"conditional plan captured: {trig}",
                    "raw": (text or "")[:80]})
        log(f"msg {msg_id}: watch trigger captured: {trig[:80]}")
    ok, reason, norm = validate_card(card, text)
    # an INVALID card returns norm=None, but everything below dereferences it —
    # msgs 3419/3423 ("no instrument") crashed here instead of reporting INVALID
    if norm is None:
        norm = {}
    if isinstance(card, dict) and card.get("img_strikes"):
        norm["img_strikes"] = card["img_strikes"]
    if isinstance(card, dict) and card.get("img_legs"):
        norm["img_legs"] = card["img_legs"]
    if isinstance(card, dict):
        for _k in ("img_credit", "img_qty"):
            if card.get(_k) is not None:
                norm[_k] = card[_k]
    # PAINTED MESSAGE: short chat text + words written on the image = the image
    # carries the message ('Pl book gains on all SPX Bull Puts' on a chart).
    # Adopt it as the text so the normal gates/classifiers run on it.
    _moi_t = (norm.get("message_on_image") or "").strip()
    if len((text or "").strip()) < 50 and len(_moi_t) >= 8:
        ledger_row({**norm, "event": "PAINTED", "msg_id": msg_id,
                    "reason": "message carried by the image", "raw": _moi_t[:120]})
        log(f"msg {msg_id}: painted message adopted: {_moi_t[:80]}")
        text = _moi_t
        norm["_raw_text"] = text[:400]
    # DESIGN #3: retraction language retires queued-but-unrun messages.
    for _victim in _supersede_targets(text, msg_id, _QUEUE):
        if _QUEUE.drop(_victim):
            ledger_row({"event": "SUPERSEDED", "msg_id": str(_victim),
                        "reason": f"retracted by msg {msg_id} before it ran",
                        "raw": (text or "")[:100]})
            log(f"msg {msg_id}: superseded queued msg {_victim}")
    # 'we can ignore' (3603 Kalshi — not our venue): record, act on nothing
    if re.search(r"\bwe can ignore\b|\bignore\s+(?:it|this|that)\b", text or "", re.I):
        ledger_row({**norm, "event": "IGNORED", "msg_id": msg_id,
                    "reason": "message says ignore", "raw": (text or "")[:100]})
        log(f"msg {msg_id}: ignored by instruction")
        save_state(st)
        return
    # BOOKKEEPING: every message updates the position book (watchlist) first
    try:
        book_apply(norm, text, msg_id, st)
    except Exception as e:
        log(f"book_apply err msg {msg_id}: {e}")
    # D143: signal scorecard — HIS axis: every signal is recorded the moment
    # it arrives (entry now, exit/claim later from his own message), scored
    # WIN/LOSS on HIS numbers, separate from our paper book. Advisory only:
    # never blocks, never trades, never raises into the pipe. Runs BEFORE the
    # INVALID return: his exits ('bought back at $235') and bare claims
    # ('80% up') carry no strikes, so validate_card fails them — the
    # scorecard must still see every message.
    try:
        import signal_scorecard as _sc
        if _sc.enabled():          # D145: never from a test fixture replay
            _sc.track(norm, text, msg_id)
    except Exception as _e:
        log(f"scorecard skipped msg {msg_id}: {type(_e).__name__}: {_e}")
    if not ok:
        ledger_row({"event": "INVALID", "msg_id": msg_id, "reason": reason,
                    "raw": (text or "")[:80]})
        log(f"invalid card msg {msg_id}: {reason}")
        # a dropped signal must be visible — otherwise it silently disappears
        push(f"⚠️ UNPARSED SIGNAL (msg {msg_id}) — {reason}\n"
             f"no trade; review manually:\n"
             f"\"{(text or '(photo only)').strip()[:180]}\"", st)
        save_state(st)
        return
    act = norm["action"]
    _moi_u = (norm.get("message_on_image") or "").upper()
    # a ticket with BOTH sides is a SPREAD's fill confirmation (often the legs
    # of a message already traded) — never re-enter it as a single (3883/3885)
    _ticket_open = ("TO OPEN" in _moi_u and _moi_u.count("TO OPEN") < 2
                    and bool(norm.get("option") or norm.get("spread")))
    # a level map is context, not a trade — capture it before any action logic
    if is_map_message(text):
        map_record(norm, msg_id, text, st)
        return
    # EXIT LADDER: "<level> next target to close all X" / "close all X at N"
    # ARMS a level-triggered close instead of closing at market now.
    if arm_exit_targets(norm, text, msg_id, st):
        return
    # EXIT-COMMAND PRECEDENCE (msg 3553 miss): "Pl book them" is a management
    # COMMAND; "we can enter again if it breaks the box" is a conditional
    # future. When the message names no structure to OPEN, the exit command is
    # the instruction — handle it even though an entry verb is present, or the
    # word "enter" routes the whole message away from the exit path.
    if exit_command_wins(text):
        cls = non_entry_class(text)
        ledger_row({"event": cls.upper(), "msg_id": msg_id,
                    "instrument": norm.get("instrument"),
                    "direction": norm["direction"],
                    "reason": f"exit command takes precedence over conditional "
                              f"entry verb — {cls}",
                    "raw": (text or "")[:160]})
        log(f"msg {msg_id}: exit-command precedence ({cls}) over entry verb")
        handle_exit_update("exit", norm, msg_id, st, text)
        return
    # ENTRY GATE: the brain's action label is unreliable (it called "we are
    # holding Bear calls ... we will see a big drop" an entry). Only a real
    # trade COMMAND in the text may open a position. Anything with NO command
    # and NO tradeable content is management/prediction/context — classify it on
    # its language and never trade it. (Gate on the TEXT, not the brain's label.)
    if _ticket_open and norm.get("action") != "entry":
        norm["action"], act = "entry", "entry"
    # a card that supplied a complete, coherent 2-leg structure is tradeable
    # content even when the text carries no verb (msg 3284 exchange-quality
    # message with a bull-put card and no 'buy/sell/collect' anywhere)
    _sp_unv = isinstance(norm.get("spread"), dict) and norm["spread"].get("_unverified")
    # D134 belt: a %-status message ('10% up') never trades — the no-command
    # branch below must take it even if a text structure would satisfy the
    # gate; only a real order ticket ('TO OPEN') may override. The branch
    # still classifies it as narrative and keeps its D119 lifecycle comment.
    if ((norm.get("_pct_status") and not _ticket_open)
            or not (entry_language(text) or has_tradeable_content(text)
                    or text_struct(text)[0] or _ticket_open or _sp_unv)):
        cls = non_entry_class(text)
        ledger_row({"event": cls.upper(), "msg_id": msg_id,
                    "instrument": norm.get("instrument"),
                    "direction": norm["direction"],
                    "reason": f"labelled '{act}' but no trade command — {cls}",
                    "raw": (text or "")[:160]})
        log(f"msg {msg_id}: {cls} (no trade command; brain said {act}) — not traded")
        # D119: lifecycle — narrative/prediction/hold that mentions an
        # instrument (or whose structure family uniquely names one) gets a
        # lifecycle entry so future messages can join it.
        try:
            import trade_lifecycle as _tlc
            _inst = norm.get("instrument") or ""
            if not _inst and cls in ("narrative", "prediction", "hold"):
                # D128: 'Holding bear calls' (4506) names no ticker — when the
                # structure family matches open positions in exactly ONE
                # instrument, that instrument identifies the thread. Ambiguous
                # (several instruments) or none -> no row, never a wrong link.
                _fam = _structure_family(text) or scope_family(text)
                if _fam and st.get("open"):
                    _cands = {}
                    for _pt in st.get("open", {}).values():
                        if _fam_ok(_pt, _fam):
                            _ci = _canon_underlying(_pt.get("instrument"))
                            if _ci:
                                _cands[_ci] = _pt.get("instrument")
                    if len(_cands) == 1:
                        _inst = next(iter(_cands.values()))
            if _inst and cls in ("narrative", "prediction", "hold"):
                _lc = _tlc.comment_lifecycle(f"LC-{msg_id}", msg_id, _inst,
                                             f"{cls}: {(text or '')[:60]}", text or "")
                # D123: compound messages chain onto the thread they continue
                # ('the remaining 20% of 7315/7325 Bull Puts' -> the trade's
                # lifecycle; 'Take Overnight trades...' -> the earlier plan).
                # D128: related_and_link now returns the target ONLY when a
                # LINK row was actually written — this log can no longer claim
                # a link that never happened ('lifecycle None linked -> ...').
                _linked = _tlc.related_and_link(_lc, _inst, (text or "")[:50],
                                                msg_id=str(msg_id),
                                                src_ts=_fmt_msg_ts(msg_id, msg_dt))
                if _linked:
                    log(f"lifecycle { _lc } linked -> {_linked} (same instrument)")
        except Exception:
            pass
        if cls == "prediction" or (
                cls in ("exit", "trim") and re.search(
                    r"\b(will|should|expect)\w*\b[^.]{0,30}\d{1,3}\s*%",
                    text or "", re.I)):
            # a trim that carries a claim ('trim 80% ... should make 100%') is
            # scored too — the instruction and the call are both real
            record_prediction(norm, msg_id, text)
        if cls in ("exit", "trim"):
            handle_exit_update("exit", norm, msg_id, st, text)
        elif cls == "scanner":
            scanner_record(norm, msg_id, text, st)
        else:
            save_state(st)
        return
    # D56: a re-delivered/recovered message must not open a position — the
    # redirect RE-STAMPS timestamps, so 'for today' anchors to the wrong
    # session and the age gate cannot see the staleness (the 8-day backlog
    # burst traded on Sunday night). Recovery-batch and aged-retry provenance
    # parks the ENTRY here; exits/management above already processed. The
    # park's counterfactual settles in park_outcomes like any other park.
    if stale:
        park_signal(norm, msg_id, text,
                    "stale re-delivery (D56): re-stamped timestamp — recorded, not traded")
        ledger_row({"event": "PARKED", "msg_id": msg_id,
                    "instrument": norm.get("instrument"),
                    "direction": norm.get("direction"),
                    "reason": "stale re-delivery (D56) — counterfactual only",
                    "raw": (text or "")[:80]})
        log(f"msg {msg_id}: stale re-delivery (D56) — parked, not traded")
        save_state(st)
        return
    # TEXT-STRUCT ROUTE: the brain non-deterministically labels real spreads
    # "none"/no-spread (msgs 3672/3681/3683/3685/3688) or emits a single-leg
    # card (3667). The TEXT is deterministic: if it parses to >=2 legs, route
    # to the multileg executor regardless of the brain's label. Texts whose
    # legs cannot be resolved fall through inside open_multileg_tradier to the
    # brain's own spread legs; inverted legs (3681) park with an alert there.
    tl, tflags, tcredit = text_struct(text)
    if not is_explicit_close(text) and len(tl) >= 2:
        # DESIGN (2026-11-18): James's FOOTPRINT as ADVISORY context. Prints the
        # historical record for this exact style; never blocks, never sizes.
        try:
            import james_patterns as _jpat
            _ks = [float(l["strike"]) for l in tl if l.get("strike")]
            _w = round(max(_ks) - min(_ks), 2) if len(_ks) >= 2 else ""
            _dir = "bullish" if any(l.get("action") == "sell" for l in tl) else "bearish"
            _dte = "0DTE" if re.search(r"\bfor\s+today\b", text or "", re.I) else "1-2"
            _line, _fp = _jpat.advisory("vertical_credit", _dir, _dte, tcredit, _w,
                                        instrument=(norm.get("instrument") or ""))
            ledger_row({"event": "PATTERN", "msg_id": msg_id,
                        "instrument": norm.get("instrument"),
                        "direction": norm.get("direction"),
                        "reason": _line, "raw": (text or "")[:80]})
        except Exception as _e:
            log(f"pattern advisory skipped msg {msg_id}: {type(_e).__name__}")
        norm["entry"] = tcredit or norm.get("entry") or (norm.get("spread") or {}).get("net")
        distrust_qty(norm, text, msg_id)
        open_multileg_tradier(norm, text, msg_id, st)
        return
    # MULTI-LEG WINS: a real multi-leg with text-verified legs + a trade command
    # routes to Tradier REGARDLESS of the brain's action/direction label (both
    # are non-deterministic — the brain flip-flops on fly direction between runs,
    # which sent real flies to the risk gate as "naked").
    if (norm.get("spread") and entry_language(text)
            and not is_explicit_close(text)):
        norm["entry"] = norm.get("entry") or (norm.get("spread") or {}).get("net")
        open_multileg_tradier(norm, text, msg_id, st)
        return
    if not norm.get("option"):
        _tc = text_option_card(text)
        if _tc:
            norm["option"] = {k: _tc[k] for k in ("underlying", "strike", "right", "expiry")}
            norm["instrument"] = norm["instrument"] or _tc["underlying"]
            if not norm.get("entry") and _tc["entry"]:
                norm["entry"] = _tc["entry"]
            log(f"msg {msg_id}: single contract rebuilt from text ({_tc['matched']})")
    if (act == "none" or (act == "entry" and norm["direction"] == "flat"))             and not norm.get("option"):
        # the brain sometimes labels a leg-scaffold as "none"; the TEXT is
        # deterministic, so park anything that names two or more strikes
        if norm.get("spread"):
            # a real multi-leg with text-verified legs: EXECUTE via Tradier
            # (park only if placement fails). The text-first guard already
            # stripped any fabricated legs, so this is a genuine structure.
            norm["entry"] = norm.get("entry") or (norm["spread"] or {}).get("net")
            open_multileg_tradier(norm, text, msg_id, st)
            return
        ledger_row({**{k: norm.get(k, "") for k in ("instrument", "direction", "action")},
                    "event": "NO_TRADE", "msg_id": msg_id,
                    "reason": reason_text(norm, text), "raw": (text or "")[:80]})
        log(f"no-trade msg {msg_id}: {reason_text(norm, text)[:70]}")
        save_state(st)
        return
    # TEXT-WINS GUARD: the brain sometimes labels a real ENTRY as "exit" in a
    # batch (the CVNA 62C bracket was misclassified as an SPX500 exit and lost).
    # If the text is a clear entry (trade command + strike + premium) and is NOT
    # an explicit close, treat it as an ENTRY regardless of the brain's label.
    if (act in ("exit", "update") and entry_language(text)
            and has_tradeable_content(text) and not is_explicit_close(text)):
        log(f"msg {msg_id}: brain said '{act}' but text is an ENTRY — forcing entry")
        act = "entry"
        norm["action"] = "entry"
    if act in ("exit", "update"):
        handle_exit_update(act, norm, msg_id, st, text)
        return
    # EXPLICIT-CLOSE routing (L27): "Sell to Close X n Buy to close Y" names legs
    # AND is an exit. The sell/buy verbs make the parser treat it as a spread
    # entry; send it to the exit path first so the close is honoured, not parked.
    if is_explicit_close(text):
        log(f"msg {msg_id}: explicit close detected -> exit path")
        handle_exit_update("exit", norm, msg_id, st, text)
        return
    # V2 RISK GATE (james_core): blocks naked/lotto/undefined-risk BEFORE any
    # broker call. Defined-risk structures pass through untouched (balance guard).
    if not risk_gate(norm, text, msg_id, st):
        return
    if norm.get("spread"):
        # multi-leg structure: we cannot execute these on the current venue, and
        # silently taking one leg (as happened with "Sell 106P / Buy 102P") would
        # open a position James never called. Park it instead of dropping it.
        sp = norm["spread"]
        legs = sp.get("legs") or []
        desc = " / ".join(
            f"{str(l.get('action', '?')).upper()} {l.get('strike', '?')}"
            f"{str(l.get('right', '?'))[0].upper()}" for l in legs) or sp.get("type") or "structure"
        norm["entry"] = norm.get("entry") or sp.get("net")
        open_multileg_tradier(norm, text, msg_id, st)
        return
    if norm.get("option"):
        if norm.get("crossed_card"):
            log(f"msg {msg_id}: CROSSED CARD — card says "
                f"{norm['option']['underlying']}, text never names it")
            push(f"⚠️ CROSSED CARD (msg %s) — the card underlying %s never "
                 f"appears in the message text. Trading the card; verify the "
                 f"contract." % (msg_id, norm["option"]["underlying"]), st)
        if norm["direction"] == "flat":
            norm["direction"] = "long"        # buying the stated contract
        ledger_row({**norm, "event": "INGEST", "msg_id": msg_id,
                    "symbol": map_symbol(norm["option"]["underlying"]) or "-",
                    "targets": "|".join(str(x) for x in norm["targets"]),
                    "raw": (text or "")[:80]})
        log(f"card msg {msg_id}: OPTION {norm['option']['underlying']} "
            f"{norm['option']['strike']:g}{norm['option']['right'][0].upper()} "
            f"exp {norm['option']['expiry']} entry {norm['entry']} stop {norm['stop']} "
            f"targets {norm['targets']} conf {norm['confidence']}")
        open_option_trade(norm, msg_id, st, text)
        return
    # a long-option idea with NO contract in the text ("enter IWM Puts for Monday")
    # is DEFINED risk (max loss = premium) but unfillable as-is. Don't guess a
    # strike and don't misroute it to a stock/CFD trade — surface it so the
    # contract can be added. (This is the 3653 case the risk gate used to block
    # as "naked"; it is not naked, just incomplete.)
    if re.search(r"\b(call|put)s?\b", text or "", re.I) and not norm.get("option") and not norm.get("spread"):
        # D116: a RE-ENTRY INVITE ("enter the SAME bull puts again") points BACK
        # at a structure we already hold/held — resolve it instead of filing an
        # idea with no legs. Resolution is strict: same side + right + canonical
        # ticker, and the matched structure must be defined-risk (2 legs).
        _same = resolve_same_structure(text, st, msg_id)
        if _same:
            _prior, _reason = _same
            ledger_row({"event": "RESOLVED_SAME", "msg_id": msg_id,
                        "instrument": _prior.get("instrument"),
                        "direction": _prior.get("direction"),
                        "reason": f"re-entered same structure as {_prior.get('id')}: {_reason}",
                        "raw": (text or "")[:160]})
            push(f"🔁 SAME AGAIN (msg %s) — re-entering the %s %s structure from "
                 f"%s (%s)." % (msg_id, _prior.get("direction"), _prior.get("instrument"),
                                _prior.get("id"), _reason), st)
            open_multileg_tradier(_prior, text, msg_id, st)
            save_state(st)
            return
        ledger_row({"event": "OPTION_IDEA", "msg_id": msg_id,
                    "instrument": norm.get("instrument"), "direction": norm.get("direction"),
                    "reason": "long-option idea: defined risk but no strike/premium in text — needs a contract",
                    "raw": (text or "")[:160]})
        log(f"msg {msg_id}: long-option idea {norm.get('instrument')} — no contract in text, recorded not filled")
        push("📌 OPTION IDEA (msg %s) %s %s — defined-risk long option, but no strike/premium in the text. Recorded, not auto-filled. Add the contract (e.g. 'IWM 215P 11/2') to trade it." % (msg_id, norm.get("direction"), norm.get("instrument")), st)
        save_state(st)
        return
    if len(st.get("open", {})) >= CFG["max_open"]:
        ledger_row({**norm, "event": "SKIP", "msg_id": msg_id,
                    "reason": f"max_open {CFG['max_open']} reached",
                    "raw": (text or "")[:80]})
        save_state(st)
        return

    ledger_row({**norm, "event": "INGEST", "msg_id": msg_id,
                "symbol": map_symbol(norm["instrument"]) or "-",
                "targets": "|".join(str(x) for x in norm["targets"]),
                "raw": (text or "")[:80]})
    log(f"card msg {msg_id}: {norm['action']} {norm['direction']} {norm['instrument']} "
        f"entry {norm['entry']} stop {norm['stop']} targets {norm['targets']} "
        f"conf {norm['confidence']}")
    open_trade(norm, msg_id, st)


# ---------------------------------------------------------------- v2 EXIT ENGINE WIRING
# Bridges the legacy exit path to the v2 ExitEngine (james_core) so ALL exit
# forms resolve through one place. handle_exit_update calls resolve_exit(); the
# returned ExitAction drives which positions close and how much.

class _Pos:
    """dict position -> attribute access, for ScopeResolver/ExitEngine."""
    def __init__(self, d):
        self.__dict__.update(d)


def parse_exit_directive(norm, text):
    """Pull the exit FORM out of the message: scope, exit_by, close_fraction.
    This is the exit-grammar parser (spec 4.4/4.7 + L15/L18/L21/L23/L35/L36/L43)."""
    t = (text or "").lower()
    scope, exit_by = None, None
    # scope dimensions
    tw = "today" if "today" in t else ("tomorrow" if "tomorrow" in t or "tomorrows" in t else None)
    sector = "chip" if "chip" in t else None
    expiry = "tomorrow" if ("tomorrow" in t or "tomorrows" in t) else None
    if tw or sector or expiry:
        scope = {"time_window": tw, "sector": sector, "expiry": expiry}
    # exit_by forms
    m = re.search(r"buy\s+back\s+at\s+\$?([\d.]+)", t)
    if m:
        exit_by = {"type": "limit", "price": float(m.group(1))}
    elif re.search(r"when[^.]*green|in\s+green|in\s+profit", t):
        exit_by = {"type": "in_profit"}
    elif re.search(r"stop\s+loss|stoploss", t):
        exit_by = {"type": "stop_loss"}
    elif re.search(r"before\s+fomc|until\s+\d|by\s+\d|before\s+close", t):
        exit_by = {"type": "time", "at": None}
    elif re.search(r"session\s+opens|evening\s+session", t):
        exit_by = {"type": "session", "venue": "rh"}
    # close fraction. '<n>% ON' is the PROFIT MARK, not a size — '95% on QQQ
    # bear calls, pl book gains' closes FULLY (CASE 1-10: '100% on' = full
    # close). Only verb-anchored percentages ('trim 80%', 'book 25%') and
    # verb-anchored ranges without a % sign ('take gains 25/35' → lower bound)
    # are partial trims.
    # D126: TRIM_RE lives in signal_intent — one definition, not a copy here.
    from signal_intent import TRIM_RE
    frac = None
    if TRIM_RE.search(t):
        mm0 = re.search(r"(?:trim|book|close|take|exit|cut)\w*\D{0,10}?(\d{1,3})\s*%", t)
        frac = int(mm0.group(1)) / 100.0 if mm0 else 0.5
    elif re.search(r"\d{1,3}\s*%\s*up\b", t):
        frac = 1.0                     # '20% up' is a progress mark, not a size
    elif re.search(r"\b(\d{1,3})\s*%\s*on\b", t):
        # standing alone '<n>% on bull puts' = take n% OFF. Paired with a booking
        # verb ('95% on X, pl book gains') it means the whole position.
        _m0 = re.search(r"\b(\d{1,3})\s*%\s*on\b", t)
        frac = (1.0 if re.search(r"\b(?:book|take|close|exit|collect)\w*", t)
                else int(_m0.group(1)) / 100.0)
    else:
        mm = re.search(r"(?:trim|book|close|take|exit|cut)\w*\D{0,10}?(\d{1,3})\s*%", t)
        mr = re.search(r"(?:trim|book|close|take|exit|cut)\w*\D{0,24}?"
                       r"(\d{1,3})([a-z]?)\s*/\s*(\d{1,3})\b(?!\s*%)", t)
        if mm:
            frac = int(mm.group(1)) / 100.0
        elif mr:
            a, b = int(mr.group(1)), int(mr.group(3))
            if a < 10 and mr.group(2):   # '2t/35' = 25/35 typo form
                a = a * 10 + 5
            if 1 <= a < 100 and 1 <= b <= 100 and a < b:
                frac = a / 100.0
    return scope, exit_by, frac


def resolve_exit(norm, text, msg_id, positions):
    """norm+text -> (ExitAction | None, match_key, matched_positions, note)."""
    from james_core import Record as _R, MsgClass as _M, Action as _A
    scope, exit_by, frac = parse_exit_directive(norm, text)
    rec = _R(msg_id=msg_id, ts=now(), cls=_M.EXIT, action=_A.EXIT, raw=(text or "")[:400],
             underlying=norm.get("instrument"), scope=scope, exit_by=exit_by,
             pct_claimed=(frac * 100 if frac else None),
             targets=list(norm.get("targets") or []),
             legs=(norm.get("spread") or {}).get("legs") or [])
    ea, tag = _EXITS.resolve(rec, [_Pos(x) for x in positions.values()])
    if frac is not None and ea is not None:
        ea.close_fraction = frac          # engine defaults to a full close

    # match the positions this exit addresses
    if scope:
        matched, why = _SCOPE.match(scope, list(positions.values()))
    elif norm.get("instrument"):
        sym = map_symbol(norm["instrument"]) or norm["instrument"]
        matched = [x for x in positions.values()
                   if x.get("instrument") == norm["instrument"] or x.get("symbol") == sym
                   or x.get("opt_underlying") == sym]
        why = "named"
    else:
        matched = list(positions.values())
        why = "all-open"
    return ea, tag, matched, why


def execute_exit_action(ea, matched, norm, msg_id, st):
    """Carry out a resolved ExitAction on the matched positions."""
    frac = ea.close_fraction if ea and ea.close_fraction else 1.0
    for t in matched:
        if t.get("executor") == "opt":
            qty_close = max(1, round((t.get("qty") or 1) * frac))
            premium = (ea.limit_price if ea and ea.limit_price else None) or opt_mark(t) or t.get("entry") or 0
            # D129: remember the mark this exit was decided on — the booking
            # path warns if the real fill lands far from it
            t["_exit_mark"] = premium or None
            close_option(t, premium, qty_close, f"exit[{ea.kind.value}] (msg {msg_id})", st)
        else:
            if frac and frac < 1:
                t["close_qty"] = max(1, round((t.get("qty") or 1) * frac))
            price = (ea.limit_price if ea and ea.limit_price else None) or quote_for(t) or t.get("entry") or 0
            # D129: stash the decision mark for the fill-divergence check —
            # in the trade's OWN units: premium for single options, the
            # debit-to-close for spreads, the quote for stocks/CFDs. Using
            # the underlying stock quote for an option fill = false warns.
            if t.get("opt_id"):
                t["_exit_mark"] = (ea.limit_price if ea and ea.limit_price
                                   else None) or opt_mark(t) or t.get("entry") or None
            elif t.get("legs"):
                t["_exit_mark"] = spread_mark(t) or None
            else:
                t["_exit_mark"] = price or None
            close_trade(t, price, f"exit[{ea.kind.value}] (msg {msg_id})", st)


def handle_exit_update(act, norm, msg_id, st, text=""):
    """'booked gains' -> close open trades in that instrument; target/stop
    revisions -> scale by the trade's stored conversion factor and apply."""
    tick = scope_instrument(text)
    if tick:
        norm = {**norm, "instrument": tick}   # text ticker beats brain label
    sym = map_symbol(norm["instrument"]) or norm["instrument"]
    fam = scope_family(text)          # family of the CLOSED object, verb-anchored
    named = set(text_strikes(text))   # named strikes = positions in scope
    if norm.get("img_strikes"):       # ...and the position on the screenshot
        named |= {float(x) for x in norm["img_strikes"]}
    uset = {_canon_underlying(x) for x in (scope_instruments(text) |
                                           ({norm["instrument"]} if norm["instrument"] else set()))}
    uset.discard(None)
    if uset:
        hits = [t for t in st.get("open", {}).values()
                if (_canon_underlying(t.get("instrument")) in uset
                    or _canon_underlying(t.get("symbol")) in uset
                    or _canon_underlying(t.get("opt_underlying")) in uset
                    or t["instrument"] == norm["instrument"]
                    or t["symbol"] == sym)
                and _fam_ok(t, fam)]
    else:
        # unnamed exit/update: NEVER blind-close the whole book (msg 3769
        # 'Nice gains with CLX, FISV, Trim 80% on FISV' closed 4 unrelated
        # positions). Caps tickers in the text scope it; with no ticker,
        # family or strikes at all -> surface, do not act.
        ticks = {w for w in re.findall(r"\b[A-Z]{2,5}\b", text or "")
                 if w not in _TICK_STOP}
        if re.search(r"\b(?:all|both)\s+trades?\b", text or "", re.I):
            # 'took gains in all trades of funded accounts' — account scoping
            # is not modelled, so the whole open book is the honest read
            hits = [t for t in st.get("open", {}).values()
                    if _fam_ok(t, fam) and _strikes_ok(t, named)]
        elif ticks:
            hits = [t for t in st.get("open", {}).values()
                    if _fam_ok(t, fam) and _strikes_ok(t, named)
                    and (str(t.get("instrument", "")).upper().split()[0] in ticks
                         or str(t.get("symbol") or "").upper() in ticks)]
        elif fam or named:
            hits = [t for t in st.get("open", {}).values()
                    if _fam_ok(t, fam) and _strikes_ok(t, named)]
        else:
            push("⚠️ AMBIGUOUS EXIT (msg %s) — no ticker/family/strikes named; "
                 "not closing anything" % msg_id, st)
            ledger_row({**norm, "event": "AMBIG_EXIT", "msg_id": msg_id,
                        "reason": "unnamed exit refused (no scope)", "raw": (text or "")[:80]})
            return
    # D67 (2026-11-16, SPX-critical): a management command acts on positions we
    # HOLD. An entry that has not filled is not a position — msg 4271 'Big drop
    # on SPX, 7615 t1 to book 80% gains' cancelled TWO unfilled SPX entries
    # (one would have won ~$3,560). Pending entries are cancelled only by an
    # explicit cancel/abandon instruction, never by a trim/exit.
    if hits and not re.search(r"\b(cancel|abandon|unfilled|not filled|never filled)\b",
                              text or "", re.I):
        _filled_only = [t for t in hits
                        if str(t.get("status")) not in
                        ("pending_entry", "pending", "pending_open")]
        if len(_filled_only) != len(hits):
            log(f"exit msg {msg_id}: {len(hits) - len(_filled_only)} unfilled "
                f"entr(y/ies) excluded from the trim/exit scope (D67)")
        hits = _filled_only
        if not hits:
            push("ℹ️ EXIT/TRIM (msg %s) — nothing FILLED matched; unfilled "
                 "entries left alone (they cancel on their own if stale, D67)"
                 % msg_id, st)
            ledger_row({**norm, "event": "EXIT", "msg_id": msg_id,
                        "reason": "exit/trim matched only unfilled entries — "
                                  "left alone (D67)", "raw": (text or "")[:80]})
            save_state(st)
            return
    # TIME SCOPE: 'tomorrow's bear calls' / 'bear calls of tomorrow' filter by
    # expiry (3590: it closed every call spread, not only tomorrow's).
    _tq_all = re.findall(r"\b(today|tomorrow)'s\b|\b(?:of|and)\s+(today|tomorrow)\b",
                         text or "", re.I)
    if _tq_all and hits:
        from datetime import timedelta as _td
        _dates = set()
        for _tup in _tq_all:
            _w = next((g for g in _tup if g), "").lower()
            if not _w:
                continue
            _d = us_session_for_today()
            if _w == "tomorrow":
                _d += _td(days=1)
                while _d.weekday() >= 5:
                    _d += _td(days=1)
            _dates.add(_d.isoformat())
        hits = [t for t in hits if str(t.get("expiry")) in _dates]
    # Named strikes bind only when they hit a real position — a mentioned index
    # level ('close to 7714') must not empty the scope (msg 3553 regression).
    if named:
        scoped = [t for t in hits if _strikes_ok(t, named)]
        if scoped:
            hits = scoped
        elif not text_strikes(text) and fam:
            # image strikes missed the book: fall back to the named family
            # rather than refusing a real exit
            named = set()
            hits = [t for t in st.get("open", {}).values() if _fam_ok(t, fam)]
    if not hits:
        ledger_row({**norm, "event": "NO_POSITION", "msg_id": msg_id,
                    "reason": f"{act} for {norm['instrument']} but nothing open"})
        log(f"{act} msg {msg_id}: no open position in {norm['instrument']}")
        # a trim/exit we cannot fire means he is managing something we lack —
        # surface it instead of burying it in the ledger
        _sc = norm.get("instrument") or "?"
        if named:
            _sc += " " + ",".join(f"{k:g}" for k in sorted(named))
        push(f"🫥 {act.upper()} NOT HELD (msg {msg_id}): {_sc} — he is managing a "
             f"position we do not have open; nothing to do.", st)
        save_state(st)
        return
    # GREEN-WHEN ARM (3614: 'Be ready to book gains ... when you see them in
    # Green'): don't book red — arm and close only once price is past the short
    # strike; already-green ones close now.
    if act == "exit" and fam == "P" and re.search(
            r"when (?:you )?see (?:them|it) in green|\bin green\b", text or "", re.I):
        armed, rest_g = [], []
        for t in hits:
            _sh = [l for l in (t.get("legs") or []) if str(l[2]) == "sell"]
            if not _sh:
                rest_g.append(t)
                continue
            try:
                _S = bbc().get_quote("SPX500") \
                    if _canon_underlying(t.get("instrument")) == "SPX" else quote_for(t)
            except Exception:
                _S = None
            if not _S:
                rest_g.append(t)
                continue
            _lvl = round(float(_sh[0][0]) * (1 + CFG.get("itm_buffer_pct", 0.05) / 100), 2)
            if float(_S) >= _lvl:
                rest_g.append(t)              # already green — close below
            else:
                t["green_when"] = {"level": _lvl, "msg": msg_id}
                armed.append(t["id"])
        if armed:
            ledger_row({**norm, "event": "ARMED", "msg_id": msg_id,
                        "reason": f"green-when armed {armed}",
                        "raw": (text or "")[:80]})
            push("🟢 GREEN-WHEN ARMED (msg %s): %s — closes when the index is "
                 "above the short strike" % (msg_id, "/".join(armed)), st)
            save_state(st)
        hits = rest_g
        if not hits:
            return
    # PARTIAL STRUCTURE EXIT (3606: 'Take gains on AMZN 255/260 Bear call Leg
    # of the Iron fly'): when the named legs are only PART of a >2-leg position,
    # close just those legs and keep the rest open.
    if act == "exit" and fam and named:
        rest = []
        for t in hits:
            legs = t.get("legs") or []
            if len(legs) <= 2 or t.get("executor") != "tradier":
                rest.append(t)
                continue
            fam_legs = [l for l in legs if str(l[1])[0].lower() == fam.lower()]
            # any fam-side strike named -> the whole SIDE goes (fly: '7700/7710
            # Bull Puts' names 7700 on the put side; 7690 is its sibling)
            if named and not any(float(l[0]) in named for l in fam_legs):
                rest.append(t)
                continue
            sub = fam_legs
            keep = [l for l in legs if l not in sub]
            if not sub or not keep:
                rest.append(t)
                continue
            try:
                # D135: same width cap as the full close — a sub-side of a
                # credit structure is itself a credit vertical (its close is
                # a debit that can never legitimately exceed its width).
                _slim = None
                if len(sub) > 1 and str(t.get("direction") or "").lower() != "long":
                    try:
                        _sks = [float(l[0]) for l in sub if l and l[0] is not None]
                    except (TypeError, ValueError):
                        _sks = []
                    if len(_sks) >= 2:
                        _slim = round(max(_sks) - min(_sks), 2)
                        # D140: same real-spot anchor on the sub-side close
                        _S2 = _anchor_spot(t.get("instrument"), sub)
                        if _S2:
                            _ac2 = anchor_close_cap(sub, _S2)
                            if _ac2 is not None:
                                _slim = min(_slim, _ac2)
                ex = _TRADIER().close({**t, "legs": sub}, limit_price=_slim)
            except Exception as e:
                ex = None
                log(f"partial exit failed {t['id']}: {e}")
            if ex is not None and ex.ok:
                t["legs"] = keep
                t["fill_note"] = (f"partial exit {fam} legs closed (order {ex.order_id}); "
                                  f"{len(keep)} legs remain — reconcile PnL manually")
                t.setdefault("partial_closes", []).append(
                    {"order_id": ex.order_id, "legs": sub, "msg": msg_id, "ts": now()})
                ledger_row({**t, "event": "PARTIAL_EXIT", "msg_id": msg_id,
                            "reason": t["fill_note"]})
                push(f"✂️ PARTIAL EXIT {t['id']} — closed {len(sub)} {fam}-side leg(s) "
                     f"(order {ex.order_id}); {len(keep)} legs still open. PnL manual.",
                     st)
                log(f"partial exit {t['id']}: {len(sub)} legs closed")
            else:
                log(f"partial exit refused/failed for {t['id']} — falling back to full")
                rest.append(t)
        hits = rest
        if not hits:
            save_state(st)
            return
    if act == "exit":
        # V2 EXIT ENGINE: resolve the exit FORM (explicit/scope/ladder/limit/
        # session/in-profit/stop) and execute it. Falls back to a full close on
        # the matched set if the engine cannot resolve a specific form.
        try:
            ea, tag, matched, why = resolve_exit(norm, text=text, msg_id=msg_id,
                                                 positions={t["id"]: t for t in hits})
            if not matched:
                matched = hits
            execute_exit_action(ea, matched, norm, msg_id, st)
            # 'trim 80% and hold 20% until expiry' — the remainder is a runner
            # he has said to keep; stops must not take it early
            if re.search(r"\bhold\b[^.]{0,24}until\s+exp", (text or "").lower()):
                for t in matched:
                    if t.get("status") not in ("closed",):
                        t["hold_until_expiry"] = True
                        t["fill_note"] = ((t.get("fill_note") or "")
                                          + " | hold remainder until expiry").strip(" |")
            log(f"exit msg {msg_id}: engine form={ea.kind.value if ea else 'none'} "
                f"tag={tag} matched={len(matched)} ({why})")
        except Exception as e:
            log(f"exit engine failed msg {msg_id}: {type(e).__name__}: {e} -> legacy close")
            for t in hits:
                if t.get("status") in ("closed", "close_pending", "pending_close"):
                    continue
                if t.get("executor") == "opt":
                    close_option(t, opt_mark(t) or t["entry"] or 0, t["qty"] or 1,
                                 f"james exit (msg {msg_id})", st)
                else:
                    close_trade(t, quote_for(t) or t["entry"] or 0,
                                f"james exit (msg {msg_id})", st)
        save_state(st)
        return
    _rtxt = reason_text(norm, text)
    for t in hits:
            if t.get("executor") == "opt":
                # premium-level levels: apply natively, no conversion
                if norm["stop"]:
                    t["stop"] = norm["stop"]
                if norm["targets"]:
                    t["targets"] = list(norm["targets"])
                t["fill_note"] = f"updated msg {msg_id}: {_rtxt[:60]}"
                ledger_row({**t, "event": "UPDATE", "msg_id": msg_id,
                            "targets": "|".join(f"{x:.2f}" for x in t["targets"]),
                            "reason": _rtxt[:80]})
                push(f"🔧 UPDATED {t['instrument']} [OPT] (msg {msg_id})\n"
                     f"stop {t['stop'] and round(t['stop'], 2)} | "
                     f"targets {[round(x, 2) for x in t['targets']] or '—'}\n"
                     f"\"{_rtxt}\"", st)
                log(f"updated {t['id']} msg {msg_id}")
            else:
                k = t.get("k", 1.0)
                if norm["stop"]:
                    t["stop"] = norm["stop"] * k
                if norm["targets"]:
                    t["targets"] = [x * k for x in norm["targets"]]
                t["fill_note"] = f"updated msg {msg_id}: {_rtxt[:60]}"
                ledger_row({**t, "event": "UPDATE", "msg_id": msg_id,
                            "targets": "|".join(f"{x:.2f}" for x in t["targets"]),
                            "reason": _rtxt[:80]})
                push(f"🔧 UPDATED {t['symbol']} {t['direction']} (msg {msg_id})\n"
                     f"stop {t['stop'] and round(t['stop'], 2)} | "
                     f"targets {[round(x, 2) for x in t['targets']] or '—'}\n"
                     f"\"{_rtxt}\"", st)
                log(f"updated {t['id']} msg {msg_id}: stop {t['stop']} targets {t['targets']}")
    save_state(st)


# ---------------------------------------------------------------- main loop

async def gap_backfill(client, channel, st, process, backscan=None):
    """Reconcile against the channel: the live telethon listener can silently
    drop events (2026-11-02: msgs 3716-3720 and 3734/3735 vanished while the
    loop was healthy). Fetch everything near the last processed id and run it
    through the normal path; the staleness gate inside process_message keeps
    old signals from trading."""
    try:
        _last = max((int(k) for k in st.get("seen_msg", {}) if str(k).isdigit()),
                    default=0)
        if not _last:
            return []
        msgs = [m async for m in client.iter_messages(channel, limit=50)]
        want = set(gap_candidates(st.get("seen_msg", {}), [m.id for m in msgs],
                                  _last, int(backscan or CFG.get("gap_backscan", 60))))
        missed = [m for m in msgs if m.id in want]
        log(f"gap-check: scanned {len(msgs)} ids, {len(missed)} to recover")
        if not missed:
            return []
        log(f"gap-check: recovering {len(missed)} missed message(s): "
            f"{[m.id for m in missed]}")
        # D56: a bulk recovery is a re-delivery, not live traffic
        _stale = len(missed) >= 25
        for m in reversed(missed):
            photo = None
            if getattr(m, "photo", None):
                PHOTOS.mkdir(exist_ok=True)
                photo = str(PHOTOS / f"{m.id}.jpg")
                try:
                    await m.download_media(file=photo)
                except Exception as de:
                    log(f"photo download failed msg {m.id}: {de}")
            dt = m.date.timestamp() if getattr(m, "date", None) else None
            await asyncio.to_thread(process, getattr(m, "message", "") or "", m.id,
                                    dt, photo, st, _stale)
        return [m.id for m in missed]
    except Exception as e:
        log(f"gap-check failed: {type(e).__name__}: {e}")
        return []


PROBE_PLAN = {          # (probe every N s, re-arm after N silent cycles)
    "rth":   (60, 2),    # cash session: messages flow, deafness is a real fault
    "pre":   (120, 3),   # 04:00-09:30 ET: entries still come (3855 was 08:46)
    "post":  (120, 3),   # 16:00-20:00 ET: management and wrap-up
    "closed": (300, 6),  # overnight/weekend: queued ideas, sparse, expect quiet
}


def session_phase(now_et=None):
    """Where we are in the US day. The channel is only continuously busy during
    cash hours; outside them messages are sparse (queued for the open) or are
    management/prediction. A watchdog tuned for RTH would cry wolf all night."""
    n = now_et or us_now()
    if n.weekday() >= 5:
        return "closed"
    hm = n.hour * 60 + n.minute
    if 4 * 60 <= hm < 9 * 60 + 30:
        return "pre"
    if 9 * 60 + 30 <= hm < 16 * 60:
        return "rth"
    if 16 * 60 <= hm < 20 * 60:
        return "post"
    return "closed"


async def listener_probe(client, channel, st):
    """One cheap question: is the channel's newest id newer than anything we
    have seen? Returns (newest_id, unseen?). The caller decides to recover and
    when to re-arm the client. A deaf listener shows up here and nowhere else —
    the loop keeps ticking while events vanish (3734/3735, 3854-3857)."""
    got = await client.get_messages(channel, limit=1)
    nid = got[0].id if got else 0
    mx = max((int(k) for k in st.get("seen_msg", {}) if str(k).isdigit()), default=0)
    return nid, bool(nid and nid > mx)


def gap_candidates(seen, msg_ids, last, backscan=60):
    """Message ids worth re-processing. Includes ids BELOW the last seen one: a
    listener can deliver out of order (on 10/30 ids 3645/3646 were skipped while
    3647 arrived), and min_id-only recovery can never fetch those again."""
    floor = last - backscan
    return sorted(i for i in msg_ids if str(i) not in seen and i > floor)


def d72_boot_cap(st):
    """D72 qty ceiling at boot — the cap must survive a restart so rows queued
    before it was set cannot fill at their original size.

    D131 (2026-11-20): the old version capped EVERY open row above the
    ceiling, including FILLED positions that mirror the broker — the adopted
    9-lot QQQ repair was silently shrunk to 1 lot and its stop-close then
    covered 1 of 9 (order 41931908, cancelled in the repair window). A filled
    row's qty is broker truth, never a sizing decision (D74 principle: book
    records carry ceiling=999). The cap now touches only rows we still
    control: no order_id yet, i.e. nothing resting at the broker.

    Returns the list of (tid, old_qty) it capped."""
    cap = int(CFG.get("max_qty", 10))
    over = []
    for tid, t in (st.get("open") or {}).items():
        if (t.get("qty") or 1) <= cap:
            continue
        if t.get("order_id") or str(t.get("status") or "") not in ("pending", "pending_entry"):
            # filled / adopted / resting at the broker — book mirrors reality
            log(f"boot: {tid} qty {t.get('qty')} over cap {cap} but broker-side "
                f"— left as-is (D131)")
            continue
        over.append((tid, t.get("qty")))
        t["qty"] = cap
        _n = (t.get("fill_note") or "")
        t["fill_note"] = _n + f" | qty capped {cap} at boot (D72)"
    return over


async def main():
    st = load(STATE, {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
    env = load_env()
    client = TelegramClient(str(SESSION), int(env["TELEGRAM_API_ID"]), env["TELEGRAM_API_HASH"])
    await client.connect()
    if not await client.is_user_authorized():
        log("session not authorized — cannot start")
        return

    channel = CFG["channel"]

    # ---------------------------------------------------------------
    # D73 (user directive 2026-11-17): ONE MESSAGE AT A TIME.
    # The old handler fired create_task per message, so six signals in three
    # minutes became six concurrent Hermes calls, six order placements and
    # interleaved book writes - the "derailing" seen in live sessions.
    # Messages now QUEUE: each is parsed, judged, positioned and its outcome
    # captured before the next one starts. A queue that waits costs seconds;
    # a race costs trades.
    # DESIGN #1: durable, crash-safe queue (disk is the source of truth).
    msg_q = DurableQueue()
    # D72 ENFORCEMENT at boot — see d72_boot_cap for the D131 scope fix
    # (cap only rows we still control; never shrink broker truth).
    _over = d72_boot_cap(st)
    if _over:
        log(f"boot: capped {len(_over)} row(s) to qty {CFG.get('max_qty')}: {_over}")
        save_state(st)
    global _QUEUE
    _QUEUE = msg_q
    if msg_q.depth():
        log(f"queue: resuming with {msg_q.depth()} item(s) from the last run")

    async def drain_msgs():
        """Strictly serial consumer - never two messages in flight. An item is
        written to disk before it is processed and removed only after it
        completes, so a restart resumes instead of losing it."""
        while True:
            try:
                item = msg_q.pop()
                if item is None:
                    await asyncio.sleep(1)      # idle: cheap poll
                    continue
                kind, mid = item.get("kind"), item.get("mid")
                t0 = time.time()
                # DESIGN #2 (2026-11-18): PEEK-AHEAD JOIN, zero wait.
                # If this message is one HALF of a spread and the very next
                # queued item is the complementary half, merge them into ONE
                # structure and trade it as a unit. Both legs are already in
                # the queue, so this is a look, not a wait - genuine naked
                # singles cost nothing. A false merge would be worse than a
                # miss, so _joinable() is deliberately strict.
                if kind == "live":
                    _nxt = msg_q.peek(skip_mid=mid)
                    if _nxt and _nxt.get("kind") == "live" and                             _is_lone_leg(item.get("text")) and                             _joinable(item.get("text"), _nxt.get("text")):
                        _merged = ((item.get("text") or "") + " n "
                                   + (_nxt.get("text") or ""))
                        _victim = _nxt.get("mid")
                        _joined = msg_q.drop(_victim)
                        if _joined:
                            ledger_row({"event": "JOINED", "msg_id": str(mid),
                                        "reason": f"halves merged with msg "
                                                  f"{_victim} (peek-ahead, "
                                                  f"zero-wait)",
                                        "raw": _merged[:120]})
                            log(f"queue: msg {mid} JOINED with {_victim} "
                                f"(one structure)")
                            item["text"] = _merged
                            mid = str(mid)
                        else:
                            _log_split_suspect(item, None)
                    elif _is_lone_leg(item.get("text")):
                        # looked like a half, but the next item is not its
                        # partner: record it. After a few weeks this tells us
                        # whether split pairs exist in this feed AT ALL.
                        _log_split_suspect(item, _nxt)
                # DESIGN #4: a signal past its deadline describes a market that
                # no longer exists (0DTE at 15:58, parsed at 16:05). Record the
                # counterfactual and move on — never trade the stale intent.
                _dl = item.get("deadline")
                if _dl and time.time() > float(_dl):
                    try:
                        park_signal({"instrument": None, "direction": None,
                                     "entry": None, "spread": None},
                                    mid, item.get("text") or "",
                                    "expired before processing (deadline "
                                    f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(_dl)))} "
                                    "UTC) — recorded, not traded (DESIGN #4)")
                        ledger_row({"event": "EXPIRED", "msg_id": str(mid),
                                    "reason": "deadline passed before processing "
                                              "— parked, not traded",
                                    "raw": (item.get("text") or "")[:100]})
                    except Exception as ex:
                        log(f"expired-park failed msg {mid}: {ex}")
                    mark_completed(st, mid)
                    save_state(st)
                    log(f"queue: msg {mid} EXPIRED — parked, not traded")
                    continue
                try:
                    if kind == "retry":
                        # D81: retry items go straight to _parse_trade, which
                        # never touches seen_msg — so without this they would
                        # be re-queued on EVERY restart forever. Mark the
                        # message complete on the retry path too.
                        try:
                            await asyncio.to_thread(_parse_trade,
                                                    item.get("text"),
                                                    mid, item.get("photo"), st,
                                                    item.get("tries", 0),
                                                    item.get("stale", False))
                        finally:
                            mark_completed(st, mid)
                            save_state(st)
                    else:
                        await asyncio.to_thread(process_message,
                                                item.get("text"), mid,
                                                item.get("dt"),
                                                item.get("photo"), st,
                                                item.get("stale", False))
                except Exception as ex:
                    log(f"queue parse failed msg {mid}: {type(ex).__name__}: {ex}")
                finally:
                    # completion is written by process_message's own wrapper;
                    # this is just the observability line.
                    log(f"queue: msg {mid} done in {time.time() - t0:.1f}s "
                        f"(depth {msg_q.depth()})")
            except Exception as ex:
                log(f"queue consumer error: {type(ex).__name__}: {ex}")
                await asyncio.sleep(1)

    asyncio.create_task(drain_msgs())

    @client.on(events.NewMessage(chats=[channel]))
    async def on_msg(e):
        try:
            text = e.message.message or ""
            photo = None
            if e.message.photo:
                PHOTOS.mkdir(exist_ok=True)
                photo = str(PHOTOS / f"{e.message.id}.jpg")
                await e.message.download_media(file=photo)
            dt = e.message.date.timestamp() if e.message.date else None
            # D73: serial queue — the message waits its turn instead of
            # racing other parses. Live traffic is stale=False.
            msg_q.put("live", text, e.message.id, dt, photo, False, 0, prio=0)
        except Exception as ex:
            log(f"handler error: {type(ex).__name__}: {ex}")

    @client.on(events.MessageEdited(chats=[channel]))
    async def on_edit(e):
        """D93 (2026-11-18): James EDITS messages - his journal shows 52
        revisions for 25 messages, over 2x. We treated an edit as either a
        duplicate (acked) or nothing, so a corrected credit or strike was
        never seen. Record every revision; if the corrected text changes a
        level on a message we already acted on, park the revision for review
        instead of silently trading the stale value."""
        try:
            mid = str(e.message.id)
            new_text = e.message.message or ""
            old = (_EDIT_TEXT.get(mid) or "")
            _EDIT_TEXT[mid] = new_text
            changed_levels = _levels_changed(old, new_text) if old else False
            ledger_row({"event": "EDIT", "msg_id": mid,
                        "reason": ("levels changed - revision parked for review"
                                   if changed_levels and old
                                   else f"revision recorded (was {'known' if old else 'unseen'})"),
                        "raw": new_text[:120]})
            log(f"msg {mid}: EDIT received"
                + (" - levels changed" if changed_levels and old else ""))
            if changed_levels and old and mid in (st.get("seen_msg") or {}):
                push("MESSAGE EDITED (msg %s) - the corrected text changes a level "
                     "on a message we already processed." + chr(10) +
                     ("WAS: " + old[:120]) + chr(10) +
                     ("NOW: " + new_text[:120]) + chr(10) +
                     "Review before acting on the old values.", st)
            save_state(st)
        except Exception as ex:
            log(f"edit handler error: {type(ex).__name__}: {ex}")

    # catch-up backlog (oldest first; stale messages are logged, not traded)
    try:
        # NO MISSED MESSAGES: fetch everything since the last processed id,
        # not a fixed window. A real-money watcher cannot drop signals.
        _last = max((int(k) for k in st.get("seen_msg", {}) if str(k).isdigit()), default=0)
        if _last:
            # scan a back-window too: an out-of-order hole can sit BELOW _last
            msgs = [m async for m in client.iter_messages(channel, min_id=_last)]
            holes = [m async for m in client.iter_messages(
                channel, limit=int(CFG.get("gap_backscan", 60)))]
            have = {m.id for m in msgs}
            msgs += [m for m in holes
                     if m.id not in have and m.id > _last - int(CFG.get("gap_backscan", 60))
                     and str(m.id) not in st.get("seen_msg", {})]
            unseen_old = [m.id for m in msgs if m.id <= _last]
            log(f"catch-up: fetching all msgs since {_last}"
                + (f" (+{len(unseen_old)} below it: {unseen_old})" if unseen_old else ""))
        else:
            msgs = [m async for m in client.iter_messages(channel, limit=50)]
        for m in reversed(msgs):
            photo = None
            if m.photo:
                PHOTOS.mkdir(exist_ok=True)
                photo = str(PHOTOS / f"{m.id}.jpg")
                try:
                    await m.download_media(file=photo)
                except Exception as de:
                    log(f"photo download failed msg {m.id}: {de}")
            dt = m.date.timestamp() if m.date else None
            # D56: a bulk recovery is a re-delivery, not live traffic — the
            # redirect re-stamps timestamps so the age gate cannot see it.
            # Normal restarts recover a handful; a burst is tens at once.
            _stale = len(msgs) >= 25
            await asyncio.to_thread(process_message, m.message or "", m.id, dt,
                                    photo, st, _stale)
        log(f"catch-up done: {channel} ({len(msgs)} msgs)")
    except Exception as e:
        log(f"catch-up failed {channel}: {e}")

    mode = "DRY-RUN" if CFG["dry_run"] else "LIVE-PAPER"
    log(f"james-pipe LIVE [{mode}] — channel: {channel} | open {len(st.get('open', {}))}/{CFG['max_open']}")

    # startup self-check: proves the execution chain before trusting it with signals
    try:
        await asyncio.to_thread(preflight, st)
    except Exception as e:
        log(f"preflight crashed (continuing): {type(e).__name__}: {e}")

    async def _guarded(fn, *args, timeout=60, label="step"):
        """Run fn with a hard timeout so a stuck broker/Hermes call cannot freeze
        the main loop. ASYNC callables are awaited on the loop; sync ones are
        offloaded to a thread. (Previously everything went through to_thread —
        which for an async fn just returned an un-awaited coroutine, so the
        gap-check NEVER ran: that is why 3734 and 3735 vanished unnoticed.)"""
        try:
            if asyncio.iscoroutinefunction(fn):
                await asyncio.wait_for(fn(*args), timeout)
            else:
                await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout)
        except asyncio.TimeoutError:
            log(f"loop step '{label}' timed out after {timeout}s - continuing")
        except Exception as e:
            log(f"loop step '{label}' failed: {type(e).__name__}: {e}")

    async def gap_check():
        await gap_backfill(client, channel, st, process_message)

    cycles = 0
    last_hb = time.time()
    last_gap = time.time()
    last_probe = time.time()
    silent_streak = 0
    probe_fail = 0
    last_phase = session_phase()
    while True:
        await asyncio.sleep(CFG["recheck_seconds"])
        cycles += 1
        try:
            # heartbeat on WALL-CLOCK (every ~15 min). A cycle-based heartbeat
            # is unreliable when steps are slow (a retry takes ~90s), which made
            # the watchdog think the loop was hung. Time-based = true liveness.
            if time.time() - last_hb >= 15 * 60:
                last_hb = time.time()
                log(f"heartbeat: open {len(st.get('open', {}))} "
                    f"retry_q {len(st.get('parse_retry', {}))} "
                    f"alerts_today {st.get('alerts_today', 0)} "
                    f"pushed {st.get('alerts_today', 0)} "
                    f"quiet {st.get('push_suppressed_today', 0)} "
                    f"silent_streak {silent_streak}")
            if st.get("digest_day") != today():
                await _guarded(daily_digest, st, timeout=120, label="digest")
            # execution-chain health (cheap probe, transition-based alerts only)
            if time.time() - st.get("health", {}).get("checked", 0) >= \
                    CFG["health_check_minutes"] * 60:
                st.setdefault("health", {})["checked"] = time.time()
                await _guarded(health_monitor, st, timeout=60, label="health")
            consume_close(st)
            # GAP-CHECK every 10 min: recover anything the listener dropped
            if time.time() - last_gap >= 600:
                last_gap = time.time()
                await _guarded(gap_check, timeout=120, label="gap")
            # LISTENER WATCHDOG (60s, one cheap call): the event handler can go
            # deaf while the loop still ticks — 3734/3735 and 3854-3857 were
            # lost exactly that way, with only retry noise in the log. Ask the
            # channel for its newest id; if it is unseen, recover immediately and
            # re-arm the client when it keeps happening.
            _phase = session_phase()
            if _phase != last_phase:
                log(f"session phase -> {_phase} (probe {PROBE_PLAN[_phase][0]}s, "
                    f"re-arm after {PROBE_PLAN[_phase][1]} silent cycles)")
                last_phase, silent_streak, probe_fail = _phase, 0, 0
            _every, _rearm = PROBE_PLAN[_phase]
            if time.time() - last_probe >= _every:
                last_probe = time.time()
                try:
                    _nid, _unseen = await listener_probe(client, channel, st)
                    if _unseen:
                        silent_streak += 1
                        log(f"listener watchdog: newest id {_nid} unseen "
                            f"(streak {silent_streak}) — recovering")
                        await _guarded(gap_backfill, client, channel, st,
                                       process_message, timeout=120, label="probe")
                        if silent_streak >= _rearm:
                            log("listener watchdog: re-arming client")
                            try:
                                await client.disconnect()
                                await asyncio.sleep(1)
                                await client.connect()
                            except Exception as re_e:
                                log(f"listener re-arm failed: {re_e}")
                            silent_streak = 0
                    else:
                        silent_streak = 0
                    probe_fail = 0
                except Exception as pe:
                    probe_fail += 1
                    log(f"listener probe failed ({probe_fail}): {type(pe).__name__}")
                    if probe_fail >= 3:
                        try:
                            await client.disconnect()
                            await asyncio.sleep(1)
                            await client.connect()
                            probe_fail = 0
                            log("listener watchdog: reconnected after probe failures")
                            # D55: the deaf window drops messages — recover them
                            # NOW, not on the next 10-min gap_check sweep.
                            await _guarded(gap_backfill, client, channel, st,
                                           process_message, timeout=120,
                                           label="probe-recover")
                        except Exception as re_e:
                            log(f"listener re-arm failed: {re_e}")
            # ONE retry per cycle for LIVE messages; stale backlog drains in
            # bulk (D62 — it skips the brain and is cheap). A re-delivery
            # storm must never starve a live signal again (msg 4387 sat 53+
            # minutes behind 150 stale items because equal-tries picked the
            # oldest junk first).
            for mid, r in _retry_batch(st.get("parse_retry") or {}):
                r["at"] = time.time()
                save_state(st)
                log(f"parse retry {r['tries']}/6: msg {mid} -> queue")
                # D76: retries go through the SAME serial queue as live
                # messages (FIFO). Calling _parse_trade here ran a second
                # parse concurrently with the consumer — the exact race D73
                # was meant to end.
                msg_q.put("retry", r["text"], mid, None, r.get("photo"),
                          r.get("stale", True), r["tries"],
                          prio=2 if r.get("stale", True) else 1)
            needs_manage = st.get("open") or any(
                t.get("status") == "close_pending"
                or (t.get("status") == "open_tradier" and t.get("close_reason"))
                for t in st.get("closed", {}).values())
            if needs_manage:
                await _guarded(manage_once, st, timeout=60, label="manage")
            if st.get("manual_settle"):
                await _guarded(manage_manual_settle, st, timeout=60, label="settle")
            if any(s.get("status") == "sh_open" for s in st.get("shadow", {}).values()):
                await _guarded(manage_shadow, st, timeout=60, label="shadow")
            # parked trades settle too — parking is fine, losing the outcome is
            # not (hourly: the expiry close only moves once a day)
            if time.time() > st.get("park_settle_next", 0):
                st["park_settle_next"] = time.time() + 3600
                await _guarded(settle_parked, st, timeout=180, label="park-settle")
        except Exception as e:
            log(f"loop error (continuing): {type(e).__name__}: {e}")


def single_instance_guard(pid_file, pid=None):
    """SINGLE INSTANCE (D32): two pipes = two listeners = duplicate handling and
    book clobber. Refuse to start when a live pipe already owns the pid file.
    Returns the owning pid if one is alive (caller exits), else writes ours."""
    import os as _os
    from pathlib import Path as _Path
    pid_file = _Path(pid_file)
    pid = pid if pid is not None else _os.getpid()
    if pid_file.exists():
        try:
            old = int(pid_file.read_text().strip() or "0")
        except (ValueError, OSError):
            old = 0
        if old and old != pid:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, old)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return old
    pid_file.write_text(str(pid))
    return None


if __name__ == "__main__":
    import os
    from pathlib import Path
    # Windows console defaults to cp1252 -> emoji in log/push lines raise
    # UnicodeEncodeError and we misreport a SENT push as failed. Force UTF-8.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    _owner = single_instance_guard(Path(__file__).parent.joinpath("pipe.pid"))
    if _owner:
        print("PIPE ALREADY RUNNING (pid %d) — refusing a second instance. "
              "Use the watchdog to restart." % _owner)
        sys.exit(3)
    # mark start so the watchdog grants a grace window for the first heartbeat
    # (prevents a restart-loop: watchdog restarted us before we could heartbeat)
    Path(__file__).parent.joinpath("pipe_restarted_at").write_text(str(time.time()))
    if "--dry" in sys.argv:
        CFG["dry_run"] = True
    asyncio.run(main())
