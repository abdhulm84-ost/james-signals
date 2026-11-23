#!/usr/bin/env python3
"""watchdog.py — dead-man switch for james_pipe. Run by Task Scheduler every 15 min.

LIVENESS = a recent HEARTBEAT, not log freshness.

Why: the message listener and the main loop both write to james_pipe.log. A hung
main loop with a working listener keeps the log fresh, so an mtime check misses
the hang (this happened 2026-10-30). Only the main loop emits "heartbeat:", so
that is the true signal of life.

Restarts the pipe if: the process is gone, OR no heartbeat in >HEARTBEAT_STALE,
OR the log is fully stale (nothing at all in >STALE_SEC).
"""
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "james_pipe.log"
WD_LOG = HERE / "watchdog.log"
STARTUP_VBS = Path(r"C:\Users\passm\AppData\Roaming\Microsoft\Windows\Start Menu"
                   r"\Programs\Startup\james_signals.vbs")
STALE_SEC = 25 * 60          # nothing at all in the log
HEARTBEAT_STALE_SEC = 25 * 60  # heartbeat is every ~15 min; >25 = hung loop
HB_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+heartbeat")


def log(msg):
    with WD_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")


def heartbeat_age_sec():
    """Seconds since the last main-loop heartbeat (UTC). None if never seen."""
    if not LOG.exists():
        return None
    last = None
    try:
        with LOG.open("r", encoding="utf-8", errors="replace") as f:
            # scan backwards through the file for the last heartbeat line
            lines = f.readlines()[-400:]
        for line in reversed(lines):
            m = HB_RE.match(line)
            if m:
                last = m.group(1)
                break
    except OSError:
        return None
    if not last:
        return None
    try:
        ts = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except ValueError:
        return None


def pipe_running():
    """Alive check via pipe.pid + ctypes OpenProcess (no subprocess quoting)."""
    pid_file = HERE / "pipe.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    k32.CloseHandle(h)
    return True


def restart():
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(HERE / "kill_pipe.ps1")], timeout=120)
    time.sleep(3)
    subprocess.run(["wscript", "//nologo", str(STARTUP_VBS)], timeout=60)
    (HERE / "pipe_restarted_at").write_text(str(time.time()))
    log("pipe relaunched")


def restart_age_sec():
    f = HERE / "pipe_restarted_at"
    try:
        return time.time() - float(f.read_text().strip())
    except (OSError, ValueError):
        return None


def main():
    running = pipe_running()
    hb_age = heartbeat_age_sec()
    hb_stale = (hb_age is None) or (hb_age > HEARTBEAT_STALE_SEC)
    log_stale = (not LOG.exists()) or (time.time() - LOG.stat().st_mtime) > STALE_SEC
    # GRACE: first heartbeat is due ~15 min after start; without grace the
    # watchdog restart-loops (sees the OLD stale heartbeat, restarts again
    # before the new pipe can emit one).
    r_age = restart_age_sec()
    in_grace = (r_age is not None) and (r_age < HEARTBEAT_STALE_SEC)
    if running and (in_grace or not hb_stale) and not log_stale:
        return                                  # healthy or warming up: silent
    log(f"unhealthy: running={running} heartbeat_age={'%.0fm' % (hb_age/60) if hb_age is not None else 'none'} "
        f"hb_stale={hb_stale} log_stale={log_stale} restart_age={'%.0fm' % (r_age/60) if r_age else 'none'} — restarting")
    restart()
    time.sleep(20)
    hb2 = heartbeat_age_sec()
    log(f"post-restart: running={pipe_running()} heartbeat={'ok' if hb2 is not None and hb2 < HEARTBEAT_STALE_SEC else 'pending'}")


if __name__ == "__main__":
    main()
