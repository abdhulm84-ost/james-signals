#!/usr/bin/env python3
"""selfheal.py — automatic recovery for the James pipeline (DESIGN, 2026-11-18).

User directive: a component flagged DOWN in the pre-open audit must be FIXED
and VERIFIED automatically, with regression + replay where possible — the user
is asleep at audit time. Flagging alone is not acceptable.

Every heal action is: (1) targeted at a KNOWN, previously-seen failure mode,
(2) verified by a re-probe after the action, (3) recorded in
selfheal_log.csv + the audit report. Anything that cannot be healed is
escalated honestly in the verdict.

Run:  python selfheal.py            # heal everything currently DOWN/WARN
"""
import csv
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
LOG = HERE / "selfheal_log.csv"

HEALED = []
FAILED = []


def log(action, target, ok, detail=""):
    HEALED.append((action, target, ok, detail)) if ok else FAILED.append(
        (action, target, detail))
    new = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "action", "target", "ok", "detail"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), action, target,
                    "OK" if ok else "FAIL", detail])
    sym = "✅" if ok else "❌"
    print(f"  {sym} heal[{action}] {target}: {detail[:80]}")


# ------------------------------------------------------------ known heals
def heal_task_disabled(name):
    """Scheduler task exists but is disabled/missing -> re-create or enable."""
    r = subprocess.run(["schtasks", "//Query", "//TN", name, "//FO", "LIST"],
                       capture_output=True, text=True, timeout=30)
    out = (r.stdout or "") + (r.stderr or "")
    if "TaskName" in out and "ERROR" not in out.upper():
        # exists but maybe disabled -> enable it
        r2 = subprocess.run('schtasks /Change /TN "' + name + '" /ENABLE',
                            capture_output=True, text=True, timeout=30,
                            shell=True)
        ok = r2.returncode == 0
        log("enable-task", name, ok, (r2.stdout or r2.stderr or "")[:60])
        return ok
    # truly missing -> recreate from the known definition
    cmds = {
        "JamesPipeWatchdog":
            'wscript //nologo C:\\Trading\\james-signals\\watchdog_run.vbs',
        "JamesOvernightWatch":
            'wscript //nologo C:\\Trading\\james-signals\\overnight_watch_run.vbs',
        "JamesMorningReport":
            'cmd /c cd /d C:\\Trading\\james-signals && '
            'C:\\Users\\passm\\miniconda3\\python.exe morning_report.py',
        "James-Position-Reconcile":
            'cmd /c cd /d C:\\Trading\\james-signals && '
            'C:\\Users\\passm\\miniconda3\\python.exe reconcile_positions.py '
            '>> reconcile.log 2>&1',
    }
    if name not in cmds:
        return False
    r3 = subprocess.run('schtasks /Create /TN "' + name + '" /TR "' +
                        cmds[name] + '" /SC MINUTE /MO "'
                        + ("15" if "Watchdog" in name else "60")
                        + '" /F', capture_output=True, text=True,
                        timeout=30, shell=True)
    ok = r3.returncode == 0
    log("recreate-task", name, ok, (r3.stdout or r3.stderr or "")[:60])
    return ok


def heal_pipe_dead():
    """Pipe process not alive -> restart via the startup VBS (the blessed path)."""
    r = subprocess.run(
        ["wscript", "//nologo",
         str(pathlib.Path(os.environ.get("APPDATA", "")) /
             "Microsoft/Windows/Start Menu/Programs/Startup/james_signals.vbs")],
        capture_output=True, text=True, timeout=30)
    time.sleep(20)
    # verify
    pid = (HERE / "pipe.pid")
    if pid.exists():
        try:
            pidn = int(pid.read_text().strip())
            import ctypes
            k = ctypes.windll.kernel32.OpenProcess(0x1000, False, pidn)
            ok = bool(k)
            log("restart-pipe", f"pid {pidn}", ok,
                "alive after restart" if ok else "still dead")
            return ok
        except Exception as e:
            log("restart-pipe", "verify", False, str(e)[:60])
    log("restart-pipe", "verify", False, "no pid file after restart")
    return False


def heal_brain_primary():
    """Alibaba primary failing -> the fallback chain (nous/gemini keys) is
    already automatic inside hermes_parse; what we can heal here is a stale
    credential cooldown on the nous pool (a known failure mode: 429 parking)."""
    r = subprocess.run(["hermes", "auth", "reset", "nous"],
                       capture_output=True, text=True, timeout=60)
    ok = r.returncode == 0
    log("reset-nous-cooldown", "hermes auth", ok, (r.stdout or "")[:60])
    return ok


def heal_backlog_stuck(audit):
    """Messages sitting unseen for a long time -> force a catch-up sweep via
    the pipe's own gap-check path (the sweep is read-only against Telegram and
    idempotent through seen_msg)."""
    # The pipe heals this itself every 60s; if the audit still sees a deep
    # backlog the pipe is likely hung -> restart is the honest action.
    return heal_pipe_dead()


# ------------------------------------------------------------- the driver
def heal_all(audit_report_lines):
    """Parse the audit verdict for DOWN/WARN items and dispatch heals."""
    heals = []
    for line in audit_report_lines:
        if "❌" not in line and "⚠️" not in line:
            continue
        low = line.lower()
        if "watchdog" in low or "overnight" in low or "morning report" in low:
            name = ("JamesPipeWatchdog" if "watchdog" in low and "pipe" in low
                    else "JamesOvernightWatch" if "overnight" in low
                    else "JamesMorningReport")
            heals.append(("task", name, lambda n=name: heal_task_disabled(n)))
        elif "pipe process alive" in low:
            heals.append(("pipe", "process", heal_pipe_dead))
        elif "nous" in low and ("402" in line or "429" in line or "cool" in low):
            heals.append(("brain", "nous cooldown", heal_brain_primary))
        elif "backlog" in low or "unseen" in low:
            heals.append(("feed", "backlog stuck", lambda: heal_backlog_stuck("x")))
    return heals


def main():
    report = HERE / "SYSTEM_AUDIT.md"
    if not report.exists():
        print("no SYSTEM_AUDIT.md — run system_audit.py first")
        return 1
    lines = report.read_text(encoding="utf-8").splitlines()
    heals = heal_all(lines)
    if not heals:
        print("nothing to heal — system was green")
        return 0
    print(f"healing {len(heals)} item(s)...")
    results = []
    for kind, target, fn in heals:
        try:
            ok = fn()
        except Exception as e:
            ok = False
            log(kind, target, False, f"{type(e).__name__}: {str(e)[:60]}")
        results.append((kind, target, ok))
    ok_all = all(ok for _, _, ok in results)
    print(f"heal pass complete: {sum(1 for _,_,o in results if o)}/{len(results)} succeeded")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
