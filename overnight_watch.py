"""
overnight_watch.py — independent health watch for the james-signals pipeline.

The in-pipe watchdog (every 15 min) restarts a dead/hung pipe and the in-pipe
health monitor pushes transition alerts. This script is a SECOND, independent
layer so that while the operator sleeps we can (a) periodically verify the stack
is healthy and executing cleanly, and (b) push one consolidated morning report.

Modes:
  python overnight_watch.py            # periodic check; pushes a Telegram alert ONLY on a problem
  python overnight_watch.py summary    # morning report pushed to Telegram

It tracks james_pipe.log by LINE COUNT (not timestamps) so a check only looks at
log lines added since the previous check — no stale re-alerts. Robust by design:
every step is wrapped so it never crashes.
"""
import sys
import json
import re
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
STATE = HERE / "watch_state.json"
RUNLOG = HERE / "watch_overnight.log"
LOG = HERE / "james_pipe.log"
SIG = HERE / "signals.csv"


def log(m):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {m}"
    print(line)
    try:
        with RUNLOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def push(text):
    """Send to Telegram Saved Messages via the pipeline's proven sender."""
    try:
        from james_pipe import _push_send
        _push_send(text)
        log(f"pushed: {text.splitlines()[0][:70]}")
    except Exception as e:
        log(f"push failed: {type(e).__name__}: {e}")


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"lines_seen": 0, "checks": 0, "problems": 0}


def save_state(s):
    try:
        STATE.write_text(json.dumps(s), encoding="utf-8")
    except Exception:
        pass


def pipe_running():
    pid_f = HERE / "pipe.pid"
    if not pid_f.exists():
        return False, None
    try:
        pid = int(pid_f.read_text().strip())
    except Exception:
        return False, None
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return False, pid
        k32.CloseHandle(h)
        return True, pid
    except Exception:
        return None, pid


def heartbeat_recent():
    """Is there a heartbeat in the recent log? Presence-based (no fragile
    wall-clock/UTC math). True if the main loop is still emitting."""
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
        return any(re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s+heartbeat", ln)
                   for ln in reversed(lines))
    except Exception:
        return None


def scan_new_lines(state):
    """Return (problems, placed, signals) over log lines added since last check."""
    problems, placed, signals = [], 0, 0
    try:
        all_lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return problems, placed, signals
    new = all_lines[state.get("lines_seen", 0):]
    for ln in new:
        low = ln.lower()
        if any(k in low for k in ("traceback", "exception", "parse_fail")):
            problems.append(ln.strip()[:130])
        elif "risk block" in low and "lotto" not in low:
            problems.append(ln.strip()[:130])          # a real defined-risk block = misfire
        elif "error" in low and "no error" not in low:
            problems.append(ln.strip()[:130])
        if "multileg tradier placed" in low or re.search(r"\border\b.*placed", low):
            placed += 1
        if re.match(r"\d{4}-\d{2}-\d{2}.*msg \d+", ln):
            signals += 1
    state["lines_seen"] = len(all_lines)
    return problems, placed, signals


def check():
    st = load_state()
    first = st.get("checks", 0) == 0
    st["checks"] = st.get("checks", 0) + 1
    running, pid = pipe_running()
    hb = heartbeat_recent()
    if first:
        # Baseline the log on the first run so pre-existing history (old test
        # errors) is never re-alerted; only genuinely new lines are scanned.
        try:
            st["lines_seen"] = len(LOG.read_text(encoding="utf-8", errors="replace").splitlines())
        except Exception:
            st["lines_seen"] = 0
        problems, placed, signals = [], 0, 0
    else:
        problems, placed, signals = scan_new_lines(st)
    status = []
    if running is False:
        status.append(f"PIPELINE NOT RUNNING (pid {pid}) — watchdog should restart within 15 min")
    if hb is False:
        status.append("no recent heartbeat — main loop may be hung")
    status.extend(problems)
    if status:
        st["problems"] = st.get("problems", 0) + 1
        save_state(st)
        push("🚨 JAMES-SIGNALS WATCH — %d issue(s) detected:\n%s\n(pipeline pid %s, heartbeat %s, %d signals / %d orders since last check)"
             % (len(status), "\n".join("- " + s for s in status[:8]), pid or "?",
                "ok" if hb else "STALE", signals, placed))
        log(f"PROBLEM ({len(status)}): {status[0][:70]}")
    else:
        save_state(st)
        log(f"check #{st['checks']} OK — pid {pid} alive, heartbeat {'ok' if hb else '?'}, {signals} signals / {placed} orders since last check")


def summary():
    """Overnight/morning report."""
    st = load_state()
    today = time.strftime("%Y-%m-%d")
    counts = {}
    trades = []
    try:
        import csv
        for r in csv.DictReader(open(SIG, encoding="utf-8")):
            if (r.get("ts") or "").startswith(today):
                ev = r.get("event") or "?"
                counts[ev] = counts.get(ev, 0) + 1
                if ev in ("ORDER", "MULTILEG", "PLACED", "INGEST"):
                    trades.append((r.get("ts", "")[11:16], ev, r.get("instrument") or "", (r.get("reason") or "")[:30]))
    except Exception:
        pass
    running, pid = pipe_running()
    hb = heartbeat_recent()
    push("☀️ JAMES-SIGNALS MORNING REPORT (%s)\n"
         "Status: %s (pid %s, heartbeat %s)\n"
         "Checks run overnight: %s | problem-checks: %s\n"
         "Signals processed today: %s\n"
         "Events: %s\n"
         "Recent trades/entries: %s\n"
         "BlackBull fresh demo still pending (for CFD fills)."
         % (today,
            "RUNNING" if running else "NOT RUNNING",
            pid or "?",
            "ok" if hb else "STALE",
            st.get("checks", 0), st.get("problems", 0),
            sum(counts.values()),
            ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none",
            "; ".join(f"{t[0]} {t[1]} {t[2]}" for t in trades[-6:]) or "none"))
    log("morning report pushed")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "summary":
        summary()
    else:
        check()
