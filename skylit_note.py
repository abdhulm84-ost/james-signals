"""skylit_note.py — log Skylit dealer levels beside a live SPX position (free tier).

Skylit is manual until a paid plan exists: no API on free. The point is to test,
before paying, whether wall proximity predicts the reversals our exit ladder
misses. One row per observation; then compare against the trades.

Usage:
  python skylit_note.py --trade TL4055 --flip 7405 --wall-up 7445 --wall-dn 7395 \
      --king 7445 --note "holding into king node"
  python skylit_note.py --show 20
"""
import argparse
import csv
import datetime

OUT = "skylit_notes.csv"
COLS = ["ts", "trade_id", "symbol", "spot", "flip", "wall_up", "wall_dn",
        "king", "note"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trade")
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--spot")
    ap.add_argument("--flip")
    ap.add_argument("--wall-up", dest="wall_up")
    ap.add_argument("--wall-dn", dest="wall_dn")
    ap.add_argument("--king")
    ap.add_argument("--note", default="")
    ap.add_argument("--show", type=int)
    a = ap.parse_args()

    if a.show:
        try:
            with open(OUT, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))[-a.show:]
        except FileNotFoundError:
            print("no notes yet")
            return
        for r in rows:
            print(f"{r['ts']} {r['trade_id'] or '-'} {r['symbol']} "
                  f"spot {r['spot'] or '?'} flip {r['flip'] or '-'} "
                  f"walls {r['wall_up'] or '-'}/{r['wall_dn'] or '-'} "
                  f"| {r['note']}")
        return

    new = not __import__("os").path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(COLS)
        w.writerow([datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"), a.trade or "", a.symbol, a.spot or "",
            a.flip or "", a.wall_up or "", a.wall_dn or "", a.king or "",
            a.note])
    print(f"logged {a.trade or '-'} {a.symbol}: flip {a.flip or '-'} "
          f"walls {a.wall_up or '-'}/{a.wall_dn or '-'}")


if __name__ == "__main__":
    main()
