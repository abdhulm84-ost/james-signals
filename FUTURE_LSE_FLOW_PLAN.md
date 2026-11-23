# FUTURE BUILD (PARKED) — LSE option-flow: entry confidence + direction exits

USER SUGGESTION 2026-11-22: "we can do LSE on what is option flow then we
should have an entry fill based on confidence and then exit quickly based on
the direction — it is just suggestion, don't build now, just do the analysis
and save, we will park for future build."  => ANALYSIS ONLY. Nothing here is
wired.

## What LSE gives us (proven from live use)

- Per-print OPRA data: symbol, strike, expiry, premium (cents), size, venue,
  IV, delta/gamma/theta/vega, underlying_price on every row.
- Free tier: 60 calls/min, 12.5M rows/day (~0 used), 12.89 GB/week (~0 used).
- Two access modes already built: live window fetches (lse_fill_audit's
  `_live_fetch`, ±10 min windows — works, used today) and the bulk archive
  `lse_flow.csv` (5.1M+ rows, grows from audits).
- Known limits: bulk archive coverage lags (11-19 dte0 SPXW prints were NOT
  in 3 sampled live windows — verify dte0 coverage before relying on it for
  0DTE flow); direction inference from prints is noisy.

## Existing pieces to REUSE (no new build needed)

1. `delayed_entry.py` (discord-signals) — the confidence MODEL already exists
   as v1 for the tracker: BS price at receipt + realistic fill (max of
   stated/current) + slippage gate (max 30%) + target room (min 15%) +
   LSE confidence 0-100 -> verdict ENTER / CAUTION / SKIP with reasons.
   First real run: 7 SKIP, 1 CAUTION — it already declines bad entries.
2. `lse_advisory` tape-direction rule (tracker): BULLISH/BEARISH/MIXED from
   net premium vs a 1.5x threshold — 6/6 bearish scenarios correct through
   the crash.
3. `uw_flow.py` (free Unusual Whales monitor) — standing exit-warning tool.
4. D138 exit fast path (ms) — the ACTUATOR for a flow-triggered exit already
   exists; flow would only supply the TRIGGER.
5. D139/D147 entry gates — confidence would rank INSIDE them: fresh price +
   in-band + flow agreement -> enter; flow against -> SKIP even when fresh.

## Proposed mechanics (parked)

ENTRY (per fresh signal, 1-2 flow calls):
  confidence = f(print size vs recent average, premium aggression, tape side
  vs our direction, tape-vs-signal agreement) on the underlying root window
  [-5 min, now] -> score 0-100:
    >= threshold      -> ENTER (gates still apply: qty-1, band, D147, D134)
    mid band          -> CAUTION (paper only / smaller confidence note)
    below             -> SKIP + push "flow disagrees"
  Budget: own counter, NEVER the 8/day audit budget.

EXIT (while a position is open, RTH only):
  poll the flow window on a low cadence (free-tier safe) ->
  net premium flips against our side beyond threshold ->
  fire the D138 fast exit (bounded by existing stops/targets — flow is a
  TRIGGER, never an override of the stop floor).

## Open questions to resolve FIRST when unparked

1. Measure live latency of `options_flow` (is it seconds, minutes, or
   end-of-day?) — the whole value depends on it.
2. dte0 coverage: today's probe saw zero 11-19-expiry SPXW prints in three
   live windows; if the endpoint skips dte0, 0DTE flow confidence is archive
   only.
3. Confidence thresholds need a baseline: replay the capture week's signals
   through the score and see whether ENTER would have beaten today's
   always-enter behaviour (same method as delayed_entry.py's first run).
4. Standing doctrine preserved regardless: qty-1 ceiling — confidence may
   gate ENTER/SKIP, NEVER size.

## Status

PARKED (user 2026-11-22). Nearest relatives already live: D137/D138 exits,
D139/D147 entry gates, D143 scorecard (the benchmark this would be judged
against). When unparked: build in the James pipe as an ADVISORY gate first
(push the score, let it warn), then hard-SKIP after the replay proves it.
