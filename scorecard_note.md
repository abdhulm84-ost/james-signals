🧮 SIGNAL SCORECARD — D143 live: James's signals now scored on HIS numbers

Format adopted exactly as you asked — the row is written the moment the signal arrives, exit left BLANK, filled in later from James's own message:

| # | Signal | Entry → exit (his) | Return | Result |
|---|--------|--------------------|--------|--------|
| — | INTU 317.5/325C | 5.0 →  | — | ⏳ PENDING (exp Nov 27) |
| — | RUT 2665/2655P | 4.4 →  | — | ⏳ PENDING (exp Nov 20) |

Seeded with his two still-open items only (4515 INTU, 4540 RUT) — no backfill of old history.

HOW A ROW CLOSES (never guessed):
• his exit price — "sold AMD 555C at 12.91" → +646% (your example, reproduced by the guard test)
• "2nd target done" → closes at that target level
• bare "80% up" / "100% now" → closes at his claimed %
• a trim ("trim 80%") never closes the row — only a FULL exit does
• no prices anywhere → 🚫 UNSCOREABLE row kept in the table (your SPY 747C example)
• 2+ positions open and a bare "bought back at $235" with no ticker → REFUSED (it will not guess which one; name the ticker or wait for a priced exit)

GUARDS: test_signal_scorecard.py 24/24 (header survives rewrites, his +646% example, credit math, x100 prices, ambiguity refusal, hook placement before the INVALID return) · DEFECT_REGISTER D143 · advisory only — never blocks, never trades.
