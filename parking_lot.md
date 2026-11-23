# Parking lot — legitimate signals we cannot trade yet

Structures and instrument classes from the James channel that are real, analysed and
worth tracking, but which the current pipeline cannot execute or book. Nothing here
is a lost signal: each row is preserved with its economics so it can be picked up
once the machinery exists.

**Rule:** analysed detail goes in `parking_lot.csv`; prose reasoning goes here.
Skylit-gated items (dealer walls, expected move) live in `skylit_paid_backlog.md`.
Nothing is deleted. Nothing in this file has been traded.

## Why each item is parked

### SPX short iron butterflies (4-leg)
- **Blocked by:** pipeline has no 4-leg structure support — spreads are already refused
  as multi-leg, and a butterfly adds a second body leg plus wing-width maths.
- **Need:** `structure_type`, `body`, `wing_low`, `wing_high`, `wing_width`, `legs[]`.
  Implied probability is `credit / width` and runs **above 100%** (body sold twice), so
  the vertical formula is wrong for them.
- **Worth it:** these were the best trades of the set — the 7720 fly paid **14:1**.
  They carry *defined, small* risk, unlike the naked/vertical shorts.

### QQQ (and any non-SPX underlying)
- **Blocked by:** different instrument class — QQQ is 1/10.72th of SPX in point terms
  (721 vs 7732); strikes, widths and credits are a different scale entirely.
- **Need:** `underlying` + `underlying_class` with per-class width and premium scale,
  and implied probability computed per contract, never across symbols.
- **Also open:** the leg is **unknown structure** — "sold 720C" with no long leg stated,
  so it is naked-short (unlimited risk) or an unstated spread. Different risk model.

### Unstated quantities
- **Blocked by:** no size in text on most messages; it appears only in screenshots.
- Affects everything already parked, and the live book: credit is **per contract**, so
  every P&L row is wrong by the lot count until size is captured.

### Overnight carry ("holding tomorrows")
- **Blocked by:** daily 0DTE cycle has no concept of holding across the close, so an
  overnight leg is invisible to the book.
- Evidence: the one disclosed overnight leg (7725/7735 into 10/28) **lost ~0.49R**.
