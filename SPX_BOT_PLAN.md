# SPX real-funds bot — graduation plan (2026-11-13)

User directive: *"i wanted a separate bot for SPX as that will touch the funds
real first and pipeline will prioritise based on their winning capability and
winning streaks will be graduated first."* SPX graduates to real money FIRST;
everything else stays paper until its band proves out. Winning streaks are the
graduation engine.

## What exists to build on

- `park_bands.py` / `PARK_WIN_LOSS_REPORT.md` — the evidence base. The golden
  band (BEARISH + thin credit) is 24W/0L +$8,747 in the parked counterfactuals;
  the traded SPXW verticals are +$1,650 vs singles -$4,470; the SPX milestone
  gate is 62% on 30 CLEAN trades (currently 5/8 clean).
- `tier_of` + `tier_report.py` (promotion ladder, 2026-11-06): per-class tier
  ranking, promote/demote by class evidence. ADOPTED trades never count as gate
  evidence. Paper stays the control arm.
- The pipe already routes SPXW defined-risk verticals on Tradier.

## Graduation policy (the rule set)

1. **Band = the unit of graduation**, not the instrument. Bands:
   `SPX bear-call thin-credit`, `SPX bull-put thin-credit`, `SPX bear-call fat`,
   `SPX bull-put fat`, `SPX fly/condor`, singles = never graduate first.
2. **Rolling gate per band** (over the last 30 clean outcomes, live + park
   counterfactuals tracked separately): `win rate >= 62%` AND `current streak >= 3`.
3. **Streaks graduate first**: when two bands pass the gate, the one with the
   longer current streak graduates first. Streak map lives in `park_bands.py`.
4. **Ladder**: paper -> 1 lot real -> +1 lot per additional 5 wins in-band.
   **Demote** on 2 consecutive losses after graduation, or rolling rate < 55%.
5. **Wrong-trade rule stays supreme** (a wrong trade is worse than a missed
   one): ambiguity (text/ticket conflict), invalid structure and unresolvable
   symbols still PARK, regardless of band. Only soft guards (ITM, near-BE,
   structure-label) yield to the band policy, and only at reduced size.
6. **Analysis-strength condition** (user: "if their analysis is strong
   predicting bearish then i m okay with it"): a bearish band entry at real size
   requires an active bearish alignment — prediction claim or armed level below
   spot for the session. Without it, the band still trades but capped at 1 lot.

## Separate-bot architecture (proposed build)

```
james_pipe.py (paper, all instruments)          spx_bot.py (SPX only, real)
        |                                               |
  full gate stack                                 same parse + gates
  paper executor                                  Tradier PROD executor
        |                                               |
  signals.csv / park_outcomes.csv  <---shared--->  band scoring (park_bands)
        |                                               |
  tier_report (paper evidence)                    graduation ladder (real)
```

- `spx_bot.py`: separate process (own pid + single_instance_guard), reads the
  SAME parsed cards from a small handoff (file queue or in-process import of
  the parser — no second telethon session, the listener stays single), applies
  band policy + analysis-strength condition, routes to Tradier **prod** account
  with the ladder size. Its ledger is `spx_trades.csv`; every entry tagged with
  its band and streak length at entry time (the graduation audit trail).
- Priority inside the bot: signals whose band is graduated come first; a
  non-graduated band never blocks a graduated one (throughput binds before
  size, per the promotion-ladder memory).
- Risk envelope: max 2 concurrent real positions, $300 risk per position at
  1 lot, daily loss cap -$600 (halt for the day, everything back to paper).
- Kill switch: `sp x_halt.txt` in the project folder — the bot checks it every
  cycle and flattens/halts.

## Pre-build checklist (needs user go)

1. Tradier **prod** account id + confirmation the account trades SPXW
   multilegs (the sandbox account rejected some roots; prod must be probed —
   `probe market params before build` doctrine).
2. Confirm real-funds appetite for the graduated band only, 1 lot start.
3. The SPX milestone gate (62% on 30 clean) is the band gate's backbone —
   the bot's first real entry can only graduate out of paper when its band
   holds the numbers for 30 clean outcomes. Until then the bot runs REAL-CODE
   PAPER-SHADOW (same code path, simulated fills) so the graduation evidence
   is collected without money.

## Do-nots (standing doctrine)

- No band graduates on <30 clean outcomes (small samples lie).
- ADOPTED/reconcile rows never count as gate evidence.
- Risk rules shrink size / move exits — they never park flow; ambiguity parks.
- **Watch-only bands (user 2026-11-13):** bullish thin-credit and neutral
  (flies/condors) never trade in the main lane and never graduate — they are
  parked and settled for outcome capture only.
- Every fix in the bot gets a DEFECT_REGISTER row + guard test + suite + restart.

## Flow overlay (Unusual Whales trial — user 2026-11-15)

Two mechanisms, both aimed at the same outcome (real funds behind fewer, better trades):

1. **LOSS REDUCTION (flow-flip exit):** while a position is held, if prints flip
   heavily against it (e.g. ask-side puts on our WPM-style bull put), exit early
   instead of riding to the stop. WPM case study: the stop capped the loss at
   -$640, but the money left days earlier — a flow-flip rule would likely have
   saved $300-500. Rule earns its place only if the trial's WPM post-mortem
   (11-05 -> 11-13 flow) confirms the flag would have fired BEFORE the stop.
2. **FASTER GRADUATION (flow-gated counting):** only flow-confirmed signals
   count toward a band's 30-clean graduation sample. Fewer but stronger
   observations -> the golden band reaches its gate on evidence faster.

Trial probes (7 days, log-only, no behavior change): (a) WPM post-mortem,
(b) log every James signal's flow at signal time vs outcome, (c) does
ask-side+sweep separate winners from losers beyond the 95% band baseline?
Exit criteria: no discrimination -> don't pay; discrimination -> subscribe and
implement (1) first, (2) second.
