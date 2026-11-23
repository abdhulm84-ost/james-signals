# SPX graduation gate — baseline and counting rule

**Set 2026-11-18.** The n=30 target for the SPX milestone (62% on 30 CLEAN
trades) starts counting from the day the defect-fix set went live, not from
the beginning of the book. Trades before that were placed by a system with
known holes, and counting them would measure the bugs, not the edge.

## The counting epoch

**From 2026-11-17** — the day these were all live and verified:
- D72 qty-1 ceiling (+ boot enforcement, so a restart cannot resurrect size)
- D86 single-leg expiry anchored to arrival (a Monday 0DTE can't become Tuesday)
- D87 naked-stop synthesis (`single_stop_pct` was NULL -> a naked put had no exit)
- D88 closeable single-leg positions (OCC derived when `opt_id` is missing)
- D67 trim/exit scoped to FILLED positions (a management message cannot
  cancel an unfilled entry)
- D78/D79/D92 queue: durable, deadline-aware, FIFO with peek-ahead join

## Where the count stands (snapshot 2026-11-18)

| metric | value |
|---|---|
| SPX positions in the book, all time | 51 |
| ... of which never executed (cancel/reject/stale/expired) | 15 |
| **SPX executed trades, all time** | **36** |
| **SPX executed trades since the epoch** | **12** (5W / 7L, net -$1,415) |
| **Remaining to n=30** | **18** |

## What counts as a CLEAN trade

Counted: a position that actually filled and closed.
NOT counted (they measure the plumbing, not the edge):
- entries cancelled unfilled / rejected by the broker
- stale re-deliveries parked by D56
- expired-before-fill entries
- ADOPTED rows (broker reconcile) — never gate evidence

## Why the epoch matters — the -$17,000

`O1793804304` alone was a **naked 7700C at qty 10**, manually settled for
**-$17,000**. Excluding it, the SPX book is roughly **+$7,775**. That single
trade is ~184% of the net deficit, and it is exactly the shape today's rules
forbid:
- **D72** caps any order at **1 lot** (10 -> 1 = 1/10th the damage)
- **D87** gives a naked short a synthesized stop (it had none)
- **D88** makes a position closeable (it once could not be)

Counting it toward the gate would mean grading the current system on a trade
the current system cannot place.

## The rule going forward

- Every filled+closed SPX trade from 2026-11-17 onward increments the count.
- Gate passes at **62% over 30 clean trades**; paper stays the control arm.
- A band (instrument|structure|direction|dte|quality) only graduates to real
  size on its own 30-clean sample — the aggregate rate is context, not proof.
