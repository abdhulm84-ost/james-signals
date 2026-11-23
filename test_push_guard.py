"""test_push_guard.py — two protections for the Saved Messages channel.

1. PUSH DISCIPLINE: a stuck close used to re-push identical text every cycle
   (BMRN: 572 identical copies). Repeats inside 30 min collapse; record-only
   classes stay in the ledger.
2. LISTENER WATCHDOG: one cheap call reveals an unseen newest id, which is the
   only visible symptom of a deaf listener (lost 3734/3735 and 3854-3857).
Run: python test_push_guard.py   (exit 0 = green)
"""
import asyncio
import james_pipe as jp

FAILS = []
SENT = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp._push_send = lambda t: SENT.append(t)
jp.log = lambda m: None

st = {"alerts_today": 0}
BAD = "⚠️ CLOSE FAILED BMRN 60C 11-20 O17939 — HTTP 400"
jp.push(BAD, st)
# the push now carries a register annotation (D23 etc.), so assert the
# original text is intact and it went out exactly once — not bare equality
check("first occurrence pings", len(SENT) == 1 and BAD in SENT[0], str(SENT))
check("a known defect names its register row",
      "known defect" in SENT[0] and "DEFECT_REGISTER" in SENT[0], SENT[0])
jp.push(BAD, st)
check("identical repeat is collapsed", len(SENT) == 1, str(SENT))
jp.push("⚠️ CLOSE FAILED BMRN 60C 11-20 O17939 — HTTP 500", st)
check("same trade, different code still collapses", len(SENT) == 1, str(SENT))
jp.push("⚠️ CLOSE FAILED PRU 120C 11-20 O17940 — HTTP 400", st)
check("a different trade still pings", len(SENT) == 2, str(SENT))

SENT.clear()
jp.push("🫥 EXIT NOT HELD (msg 771): SPX — nothing to do", st)
jp.push("📌 OPTION IDEA (msg 772) IWM long — add the contract", st)
check("record-only classes stay quiet", not SENT, str(SENT))
jp.push("🧾 CLOSE SENT SPXW — book gains", st)
jp.push("🩺 PREFLIGHT OK — execution chain ready", st)
check("orders and health still ping", len(SENT) == 2, str(SENT))

SENT.clear()
recent = st["push_recent"]
recent[[k for k in recent][0]] = 0        # age the newest entry out
jp.push("⚠️ CLOSE FAILED BMRN 60C 11-20 O17939 — HTTP 400", st)
check("repeat pings again after the window", len(SENT) == 1, str(SENT))


# ---- watchdog ----
class _C:
    def __init__(self, ids):
        self.ids = ids

    async def get_messages(self, ch, limit=1):
        class M:
            def __init__(self, i):
                self.id = i
        return [M(self.ids[-1])] if self.ids else []


def probe(ids, seen):
    return asyncio.new_event_loop().run_until_complete(
        jp.listener_probe(_C(ids), "chan", {"seen_msg": seen}))


nid, unseen = probe([100, 101, 102], {"100": 1, "101": 1})
check("unseen newest id is flagged", nid == 102 and unseen is True, f"{nid} {unseen}")
nid, unseen = probe([100, 101], {"100": 1, "101": 1})
check("caught-up channel is quiet", nid == 101 and unseen is False, f"{nid} {unseen}")
nid, unseen = probe([], {"100": 1})
check("empty channel does not alarm", nid == 0 and unseen is False, f"{nid} {unseen}")

# ---- session phase: the watchdog's sensitivity, not its job ----
import datetime as _dt


def at(h, m, wd=0):
    return (_dt.datetime(2026, 11, 9) + _dt.timedelta(days=wd)).replace(hour=h, minute=m)


check("08:46 ET is premarket (entry window)", jp.session_phase(at(8, 46)) == "pre")
check("09:30 ET opens RTH", jp.session_phase(at(9, 30)) == "rth")
check("16:00 ET is post", jp.session_phase(at(16, 0)) == "post")
check("22:00 ET is closed", jp.session_phase(at(22, 0)) == "closed")
check("Saturday is closed", jp.session_phase(at(12, 0, 5)) == "closed")
check("RTH probes hardest", jp.PROBE_PLAN["rth"][0] < jp.PROBE_PLAN["closed"][0])
check("closed tolerates far more silence",
      jp.PROBE_PLAN["closed"][1] > jp.PROBE_PLAN["rth"][1] * 2)

# 3. PREFLIGHT BATCHING (user 2026-11-14): a fix session restarts the pipe
# 3-6x and each boot used to ping Saved Messages. Identical restart notices
# now collapse for the class window; other events keep their 30-min window.
PF = "🩺 PREFLIGHT OK — execution chain ready"
st["push_recent"].pop("🩺 PREFLIGHT OK", None)   # fresh window (section 1 pushed one)
_n0 = len(SENT)
jp.push(PF, st)
check("first preflight of a window pings", len(SENT) == _n0 + 1, str(SENT[_n0:]))
jp.push(PF, st)
jp.push(PF, st)
check("preflight repeats are batched", len(SENT) == _n0 + 1, str(SENT[_n0:]))
jp.push("⚠️ CLOSE FAILED PRU 120C 11-20 O17941 — HTTP 400", st)
check("other events still ping after batching", len(SENT) == _n0 + 2, str(SENT[_n0:]))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
