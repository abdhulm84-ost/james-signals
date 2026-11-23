🛡️ PRE-OPEN DISCIPLINE — your question found two real gaps (D150, now closed)

Asked: do we (1) check resurfacing defects, (2) confirm messages tagged before open, (3) prove LSE calls work, (4) run a complete system check before the open?

1. Defect resurface — YES, already: every fix has a DEFECT_REGISTER row (D01-D150) with its guard test; defect_lookup --msg/--word finds prior matches; --postmortem D## separates REGRESSION (fix broke) from BYPASS (new path around it); the pre-open chain re-runs the critical guards daily.
4. Complete system check — YES, already: preopen_audit.bat runs AUDIT -> SELF-HEAL -> RE-AUDIT -> REGRESSION daily (12:30), covering feed/brain/decision/flow/earnings/queue/execution/recon/memory/delivery.
2. Messages caught up before open — WAS A GAP: the pipe gap-checked every ~10 min but only wrote log lines; nothing surfaced it. NOW: CATCHUP component — last gap-check must be <=40 min old AND report "0 to recover" or the gate BLOCKS.
3. LSE working — WAS A GAP: only the weekly Monday audit proved it. NOW: LSE component — key resolves + one live usage() call (1 free call) proves key AND quota end to end, every pre-open.

Live result: VERDICT GO — 0 down / 2 warns / 27 checked (warns: nous pool 401 = known, alibaba primary healthy; drift ledger quiet = no drift in 60h, expected). Also fixed a latent 13h timezone bug in the audit's failure window (UTC logs parsed as NZ local).

Guard: test_d150_preopen.py 8/8. The gate now proves, before every open: feed caught up, LSE callable, all systems up, critical defect guards still holding.
