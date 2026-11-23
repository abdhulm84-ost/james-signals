🔧 EXIT-SPEED BUILD — D137/D138/D139 live (2026-11-21), your "build it" order done

1. D137 — every Tradier close now carries a LIMIT, and re-prices itself:
• Credit spreads: debit min(width, 1.30x our own mark) — impossible packages refused (the Friday -24,131 legging class) AND fills far from our quote refused (MRNA's buy-back at 1.41 vs 2.44 mark).
• Single options: pay at most 1.30x mark / accept at least 0.70x mark (singles were the last unprotected closes).
• No mark (dead feed) -> old behaviour: width cap / market. Exits are never blocked by data.
• Re-price: a resting close unfilled 60s+ gets cancelled ONCE and re-priced against fresh marks (max 2), then escapes to market. An exit waits one cycle, never strands (≤3 cancels per close event — the TL3731 storm rule).

2. D138 — exit commands skip the brain AND vision entirely:
• Measured 167-324s from James's exit words to our close order — almost all of it the hermes/photo parse of an instruction written in the text.
• Now: classifier says exit/trim AND scope needs no inference (text names ticker/family/strikes, OR the book holds exactly one instrument) -> null card -> straight to handle_exit_update. "Take gains on MRNA puts" reaches the exit path in milliseconds; hermes never called (proven in the guard test).
• Multi-instrument bare exits, entries, %-status, image-only: unchanged, still brain.

3. D139 — entry limits + divergence warnings:
• Single-option entries were the LAST market orders: now LIMIT at signal ±15% (unfilled limits rest into the existing recheck/6h-cancel machinery — same as spreads, which were always limit-at-signal).
• New alert at every option fill: |fill - signal|/signal > 25% -> ⚠️ ENTRY FILL DIVERGED (threshold 25% = rebase_levels' own card_far rule; 30% would have missed MRNA's +29.3%). MRNA and WMT's silent fills would both have spoken up. Alert only — fills are never undone.

Guard: test_d137_exit_speed.py 26/26; neighbours green; DEFECT_REGISTER D130-D139 complete. Full suite running before the restart.
