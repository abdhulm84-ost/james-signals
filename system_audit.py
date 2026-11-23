#!/usr/bin/env python3
"""system_audit.py — the pre-open internal audit (DESIGN: user directive
2026-11-18: *"internal verification / system-ready mechanism, 1-2 hours before
ET open, check all components, fix + regression + replay, daily report"*).

Checks ALL EIGHT component groups from the system map, writes a daily report,
and pushes a verdict to Saved Messages. Read-only against brokers (except the
 Tradier /orders probe which is read-only too).

  python system_audit.py            # run the audit, print + save + push
  python system_audit.py --quiet    # save + push, no stdout

Exit code: 0 = all green, 1 = any component down/degraded.
"""
import argparse
import csv
import os
import datetime
import json
import pathlib
import re
import subprocess
import tempfile
import sys
import time
import calendar
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

REPORT = HERE / "SYSTEM_AUDIT.md"
AUDIT_CSV = HERE / "system_audit.csv"

STATUS = []          # (component, sub, status, detail)


def rec(component, sub, ok, detail=""):
    STATUS.append((component, sub, "GREEN" if ok else "DOWN", detail))
    sym = "✅" if ok else "❌"
    print(f"  {sym} {component:<10} {sub:<26} {detail[:90]}")


def rec_warn(component, sub, detail):
    STATUS.append((component, sub, "WARN", detail))
    print(f"  ⚠️ {component:<10} {sub:<26} {detail[:90]}")


# ------------------------------------------------------------------ 1. FEED
def check_feed(audit):
    import fetch_review  # uses the review session; never the live watcher
    ok = True
    try:
        # newest channel id reachable + frontier comparison
        import james_pipe as jp
        st = json.loads((HERE / "state.json").read_text(encoding="utf-8"))
        seen = st.get("seen_msg") or {}
        frontier = max((int(k) for k in seen if str(k).isdigit()), default=0)
        # telethon quick probe via the review session
        r = subprocess.run(
            [sys.executable, "-c",
             "import asyncio\n"
             "from telethon import TelegramClient\n"
             "from james_pipe import load_env, HERE\n"
             "import os\n"
             "env = load_env()\n"
             "async def m():\n"
             "    c = TelegramClient(str(HERE / 'telegram_review.session'),"
             " int(env['TELEGRAM_API_ID']), env['TELEGRAM_API_HASH'])\n"
             "    await c.connect()\n"
             "    async for msg in c.iter_messages('"
             + audit["channel"] + "', limit=1):\n"
             "        print('NEWEST', msg.id)\n"
             "    await c.disconnect()\n"
             "asyncio.run(m())"],
            capture_output=True, text=True, timeout=90, cwd=str(HERE))
        line = [l for l in (r.stdout or "").splitlines() if "NEWEST" in l]
        if line:
            newest = int(line[0].split()[-1])
            behind = newest - frontier
            rec("FEED", "telegram newest id vs frontier", True,
                f"newest {newest}, frontier {frontier}, behind {behind}")
            if behind > 5:
                rec_warn("FEED", "backlog depth", f"{behind} unseen messages")
        else:
            ok = False
            rec("FEED", "telegram reachable", False, (r.stderr or "")[:100])
    except Exception as e:
        ok = False
        rec("FEED", "telegram reachable", False, f"{type(e).__name__}: {str(e)[:80]}")
    return ok


# ----------------------------------------------------------------- 2. BRAIN
def check_brain(audit):
    ok = True
    auth = json.loads(
        (pathlib.Path(os.environ.get("LOCALAPPDATA", "")) /
         "hermes/auth.json").read_text(encoding="utf-8"))
    p = auth.get("providers", {}).get("nous", {})
    tok = p.get("access_token")
    url = p.get("inference_base_url") + "/chat/completions"
    body = json.dumps({"model": "google/gemini-3.8-flash",
                       "messages": [{"role": "user", "content": "reply: ok"}],
                       "max_tokens": 5}).encode()
    try:
        t0 = time.time()
        r = urllib.request.urlopen(urllib.request.Request(
            url, data=body,
            headers={"Authorization": f"Bearer {tok}",
                     "Content-Type": "application/json"}), timeout=45)
        d = json.load(r)
        rec("BRAIN", "nous reachability", True,
            f"{time.time() - t0:.1f}s (fallback pool live)")
    except urllib.error.HTTPError as e:
        det = ""
        try:
            det = json.loads(e.read()).get("error", {}).get("message", "")[:70]
        except Exception:
            det = str(e)[:70]
        # 402 = daily cap, a KNOWN state, not a fault: the fallback must carry
        rec_warn("BRAIN", "nous pool", f"HTTP {e.code}: {det}")
    except Exception as e:
        rec_warn("BRAIN", "nous pool", f"{type(e).__name__}: {str(e)[:70]}")

    # the PRIMARY: alibaba token plan, exercised through hermes itself
    hermes = audit["hermes_bin"]
    qf = HERE / "parse_query.txt"
    qf.write_text("Reply with exactly: ALIVE", encoding="utf-8")
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8",
                                errors="replace") as of, \
         tempfile.TemporaryFile(mode="w+", encoding="utf-8",
                                errors="replace") as ef:
        try:
            t0 = time.time()
            r = subprocess.run(
                ["cmd", "/c", hermes, "chat", "--query-file", str(qf), "-Q",
                 "--in", str(HERE), "--provider", "custom", "-m",
                 audit["brain_model"]],
                stdout=of, stderr=ef, timeout=90, cwd=str(HERE))
            dt = time.time() - t0
            of.seek(0)
            out = of.read().strip()
            ok2 = r.returncode == 0 and "ALIVE" in out
            rec("BRAIN", "alibaba primary end-to-end", ok2,
                f"{dt:.1f}s rc={r.returncode} out={out[:30]!r}")
            ok = ok and ok2
            if dt > 60:
                rec_warn("BRAIN", "alibaba latency", f"{dt:.1f}s > 60s")
        except subprocess.TimeoutExpired:
            ok = False
            rec("BRAIN", "alibaba primary end-to-end", False, "timeout 90s")
        except Exception as e:
            ok = False
            rec("BRAIN", "alibaba primary end-to-end", False,
                f"{type(e).__name__}: {str(e)[:80]}")
    return ok


# ------------------------------------------------------------- 3. DECISION
def check_decision(audit):
    ok = True
    t = "Sell SPX 7520P n Buy 7505P n collect $615 for today"
    legs = json.loads(json.dumps(None)) if False else None
    # grammar parser: the deterministic legs layer
    import importlib
    import james_pipe as jp
    importlib.reload(jp)
    st_legs = jp.text_struct(t)[0]
    rec("DECISION", "text grammar parser", len(st_legs) == 2, str(st_legs)[:80])
    # entry gate: painted adoption, dedupe
    rec("DECISION", "gates wired (painted/dedupe/D67/D75)",
        "painted message adopted" in (HERE / "james_pipe.py").read_text(encoding="utf-8"))
    return ok


# ------------------------------------------------------------ 3b. FLOW (UW)
def check_flow(audit):
    """DESIGN: the UW net-premium read must RUN DAILY, not on demand. It was
    built but never wired - so it did not warn us on TL4403/4433. It is free
    (the public overview table), read-only, and part of the tape evidence."""
    ok = True
    f = HERE / "uw_flow.csv"
    try:
        import subprocess as _sp
        r = _sp.run([sys.executable, str(HERE / "uw_flow.py"), "--from-state"],
                    capture_output=True, text=True, timeout=180, cwd=str(HERE))
        ok = r.returncode == 0
        tail = [l for l in (r.stdout or "").splitlines() if l.startswith("  ")]
        rec("FLOW", "uw net-premium snapshot", ok,
            (tail[-1].strip() if tail else "no rows")[:90])
    except Exception as e:
        ok = False
        rec("FLOW", "uw net-premium snapshot", False, f"{type(e).__name__}: {str(e)[:60]}")
    if f.exists():
        age_h = (time.time() - f.stat().st_mtime) / 3600
        if age_h > 30:
            rec_warn("FLOW", "uw_flow.csv staleness", f"last write {age_h:.0f}h ago")
    return ok


# ------------------------------------------------------- 3c. EARNINGS CALENDAR
def check_earnings(audit):
    """The earnings calendar must be CURRENT before the open - it is the gate
    that flags an earnings trade holding through the binary event (WPM -960)."""
    ok = True
    try:
        import earnings_guard as _eg
        rows = _eg.refresh(days=5, log=lambda m: None)
        today = time.strftime("%Y-%m-%d")
        upcoming = [r for r in rows if r["date"] >= today]
        rec("EARNINGS", "calendar refresh (5d)", bool(upcoming),
            f"{len(upcoming)} upcoming ticker(s)")
        ok = bool(upcoming)
    except Exception as e:
        ok = False
        rec("EARNINGS", "calendar refresh", False,
            f"{type(e).__name__}: {str(e)[:60]}")
    return ok


# --------------------------------------------------------------- 4. QUEUE
def check_queue(audit):
    import james_pipe as jp
    q = jp.DurableQueue(HERE / "msg_queue.json")
    n = q.depth()
    rec("QUEUE", "durable file readable", True, f"{n} item(s) pending")
    # deadline audit: expired items should be rare (the consumer parks them)
    now = time.time()
    expired = [x for x in q.snapshot()
               if x.get("deadline") and float(x["deadline"]) < now]
    if expired:
        rec_warn("QUEUE", "expired items still queued",
                 f"{len(expired)} item(s) past deadline")
    # retry dict (legacy, pre-durable) should shrink over time
    st = json.loads((HERE / "state.json").read_text(encoding="utf-8"))
    rq = st.get("parse_retry") or {}
    if rq:
        rec_warn("QUEUE", "legacy parse_retry backlog", f"{len(rq)} item(s)")
    return True


# ------------------------------------------------------------ 5. EXECUTION
def check_execution(audit):
    ok = True
    import james_pipe as jp
    # Tradier sandbox reachable + order book readable
    try:
        ex = jp._TRADIER()
        st_o, _ = jp._tradier_close_status("41818222")   # a known past order
        rec("EXECUTION", "tradier reachable", True, f"known order status: {st_o}")
    except Exception as e:
        ok = False
        rec("EXECUTION", "tradier reachable", False,
            f"{type(e).__name__}: {str(e)[:70]}")
    # MT5
    try:
        bb = jp.bbc()
        bb.connect()
        mt = bb._mt()
        ai = mt.account_info()
        rec("EXECUTION", "blackbull MT5", bool(ai),
            f"{ai.login} @ {ai.server}" if ai else "no account info")
        ok = ok and bool(ai)
    except Exception as e:
        rec("EXECUTION", "blackbull MT5", False, f"{type(e).__name__}: {str(e)[:70]}")
    # Tiger
    try:
        _, trade, _ = jp.tiger()
        rec("EXECUTION", "tiger paper", bool(trade), "client ready")
    except Exception as e:
        rec("EXECUTION", "tiger paper", False, f"{type(e).__name__}: {str(e)[:50]}")
    # open book state
    st = json.loads((HERE / "state.json").read_text(encoding="utf-8"))
    op = st.get("open") or {}
    over = [(k, v.get("qty")) for k, v in op.items()
            if (v.get("qty") or 1) > audit["max_qty"]]
    if over:
        rec_warn("EXECUTION", "oversized rows", str(over))
    rec("EXECUTION", "open book", len(op), f"{len(op)} position(s), "
        f"{sum((v.get('qty') or 1) for v in op.values())} lot(s) total")
    return ok


# ------------------------------------------------------ 6. RECONCILIATION
def check_reconciliation(audit):
    ok = True
    ok1, out1 = _task_ready("James-Position-Reconcile")
    rec("RECON", "position reconcile task", ok1, out1[-60:] if not ok1 else "")
    ok2, out2 = _task_ready("James-Open-Reconcile")
    rec("RECON", "open reconcile task", ok2, out2[-60:] if not ok2 else "")
    ok = ok and ok1 and ok2
    # drift ledger freshness
    f = HERE / "position_drift.csv"
    if f.exists():
        age_h = (time.time() - f.stat().st_mtime) / 3600
        rec_warn("RECON", "drift ledger", f"last write {age_h:.1f}h ago")
    return ok


# ----------------------------------------------------------------- 7. MEMORY
def check_memory(audit):
    ok = True
    for f, minrows in (("signals.csv", 100), ("parking_lot.csv", 50),
                       ("park_outcomes.csv", 20)):
        fp = HERE / f
        if not fp.exists():
            rec("MEMORY", f, False, "missing")
            ok = False
            continue
        n = sum(1 for _ in fp.open(encoding="utf-8", errors="replace"))
        rec("MEMORY", f, n >= minrows, f"{n} lines")
    # brain failure rate over the last 24h of the log
    log = HERE / "run_stdout.log"
    if log.exists():
        day_ago = time.time() - 86400
        txt = log.read_text(encoding="utf-8", errors="replace")
        fails = 0
        runs = 0
        for line in txt.splitlines():
            try:
                # log timestamps are UTC — calendar.timegm, not mktime (mktime
                # read them as NZ local and shifted the 24h failure window)
                ts = calendar.timegm(time.strptime(line[:19],
                                                   "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            if ts < day_ago:
                continue
            if "failed rc" in line:
                fails += 1
            if "queue: msg" in line:
                runs += 1
        if fails > 200:
            rec_warn("MEMORY", "brain failures (24h)", f"{fails} failed calls")
        rec("MEMORY", "queue completions (24h)", True, f"{runs} item(s)")
    return ok


# --------------------------------------------------------------- 8. DELIVERY
def check_delivery(audit):
    ok = True
    for nm in ("JamesPipeWatchdog", "JamesOvernightWatch", "JamesMorningReport"):
        okn, outn = _task_ready(nm)
        rec("OPS", nm, okn, outn[-60:] if not okn else "scheduled")
        ok = ok and okn
    # pipe process alive
    pid = (HERE / "pipe.pid")
    alive = False
    if pid.exists():
        try:
            pidn = int(pid.read_text().strip())
            import ctypes
            k = ctypes.windll.kernel32.OpenProcess(0x1000, False, pidn)
            alive = bool(k)
        except Exception:
            pass
    rec("OPS", "pipe process alive", alive, f"pid {pid.read_text().strip() if pid.exists() else '?'}")
    ok = ok and alive
    return ok


def check_catchup(audit):
    """D150: every channel message must be caught up BEFORE the open — the
    pipe gap-checks continuously but nothing surfaced its verdict, so a deaf
    window could hide in a log while the audit went green. Reads the log tail
    only: last gap-check must be fresh (<=40 min; it runs ~every 10) and must
    report 0 to recover."""
    ok = True
    log = HERE / "james_pipe.log"
    last_gc = None
    if log.exists():
        try:
            lines = log.read_text(encoding="utf-8",
                                  errors="replace").splitlines()[-400:]
            for ln in lines:
                if "gap-check:" in ln:
                    last_gc = ln
        except OSError as e:
            rec("CATCHUP", "log readable", False, str(e)[:60])
            return False
    if not last_gc:
        rec("CATCHUP", "gap-check seen", False, "no gap-check line in log tail")
        return False
    import calendar as _cal
    try:
        ts = _cal.timegm(time.strptime(last_gc[:19], "%Y-%m-%d %H:%M:%S"))
        age_min = (time.time() - ts) / 60
    except ValueError:
        age_min = 1e9
    fresh = age_min <= 40
    rec("CATCHUP", "gap-check freshness", fresh, f"last {age_min:.0f} min ago")
    ok = ok and fresh
    # "scanned N ids, 0 to recover" — anything else = holes still open
    caught_up = "0 to recover" in last_gc
    rec("CATCHUP", "channel caught up", caught_up,
        last_gc.split("gap-check:")[-1].strip()[:70])
    ok = ok and caught_up
    return ok


def check_lse(audit):
    """D150: LSE must be CALLABLE before the open (fill audits, tape grading,
    park settles all ride it). One live usage() call proves key + quota end
    to end; artifact freshness proves the pipelines actually write."""
    ok = True
    key = None
    try:
        from lse_flow import _key
        key = _key()
        rec("LSE", "api key resolves", bool(key),
            "from hermes .env" if key else "MISSING")
        ok = ok and bool(key)
    except Exception as e:
        rec("LSE", "api key resolves", False, f"{type(e).__name__}: {str(e)[:60]}")
        ok = False
    if key:
        try:
            import lse
            u = lse.LSE(key).usage()          # 1 free call, 60/min tier
            rec("LSE", "live usage call", True,
                f"{u.get('calls_per_minute')} calls/min, "
                f"{u.get('bytes_since_start')} B since start")
        except Exception as e:
            ok = False
            rec("LSE", "live usage call", False,
                f"{type(e).__name__}: {str(e)[:60]}")
    # artifacts: the Monday audit and the tape must have been written lately
    for f in ("lse_fill_audit.csv", "lse_flow.csv"):
        fp = HERE / f
        if fp.exists():
            age_d = (time.time() - fp.stat().st_mtime) / 86400
            if age_d > 8:
                rec_warn("LSE", f"{f} staleness", f"last write {age_d:.1f}d ago")
        else:
            rec_warn("LSE", f"{f} missing", "no artifact yet")
    return ok


def _task_ready(name):
    try:
        # NOTE: from Git Bash the double-slash form (//Query) is what reaches
        # schtasks intact; a single slash is consumed by MSYS path translation.
        # schtasks probing: single-slash + shell=True is the ONLY form that
        # works when this runs inside the pipe/Git Bash contexts (the // form
        # is MSYS-translated and returns usage errors).
        r = subprocess.run('schtasks /Query /TN "' + name + '" /FO LIST',
                           capture_output=True, text=True, timeout=30, shell=True)
        out = (r.stdout or "") + (r.stderr or "")
        ok = ("Status:" in out or "Next Run Time" in out)
        last = ""
        for ln in out.splitlines():
            if "Status:" in ln or "The task" in ln:
                last = ln.strip()
                break
        return ok, last[:60]
    except Exception as e:
        return False, str(e)[:60]


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    import james_pipe as jp
    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    audit = {"channel": cfg.get("channel"), "hermes_bin": cfg.get("hermes_bin"),
             "brain_model": cfg.get("hermes_model"),
             "max_qty": cfg.get("max_qty", 10)}

    if not a.quiet:
        print("=== PRE-OPEN SYSTEM AUDIT ===")
    for fn in (check_feed, check_brain, check_decision, check_flow,
               check_earnings, check_queue, check_catchup, check_lse,
               check_execution, check_reconciliation, check_memory,
               check_delivery):
        if not a.quiet:
            print(f"-- {fn.__name__.replace('check_', '').upper()}")
        try:
            fn(audit)
        except Exception as e:
            print(f"  ❌ audit section {fn.__name__} crashed: {e}")

    downs = [s for s in STATUS if s[2] == "DOWN"]
    warns = [s for s in STATUS if s[2] == "WARN"]
    verdict = "GO" if not downs else "BLOCKED"
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    # daily csv
    new = not AUDIT_CSV.exists()
    with AUDIT_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "component", "sub", "status", "detail"])
        for c, s2, st2, d in STATUS:
            w.writerow([stamp, c, s2, st2, d])

    # daily md report
    md = [f"# SYSTEM AUDIT — {stamp}", "",
          f"## Verdict: **{verdict}** — {len(downs)} down, {len(warns)} warn, "
          f"{len(STATUS)} checked", ""]
    cur = None
    for c, s2, st2, d in STATUS:
        if c != cur:
            md.append(f"### {c}")
            cur = c
        sym = {"GREEN": "✅", "WARN": "⚠️", "DOWN": "❌"}[st2]
        md.append(f"- {sym} **{s2}** — {d}")
    REPORT.write_text("\n".join(md), encoding="utf-8")

    if not a.quiet:
        print(f"\nVERDICT: {verdict} ({len(downs)} down / {len(warns)} warn "
              f"/ {len(STATUS)} checked)")
        print(f"report: {REPORT.name} | history: {AUDIT_CSV.name}")

    # push the verdict to Saved Messages
    try:
        import james_pipe as jp
        head = (f"🧾 SYSTEM AUDIT — {stamp}\n{verdict}: "
                f"{len(STATUS)} checks, {len(downs)} down, {len(warns)} warn")
        body = "\n".join(f"{c}: {s2} → {st2} {d}" for c, s2, st2, d in STATUS
                         if st2 != "GREEN")
        jp._push_send(head + ("\n" + body if body else ""))
    except Exception as e:
        print(f"push failed: {e}")
    return 1 if downs else 0


if __name__ == "__main__":
    sys.exit(main())
