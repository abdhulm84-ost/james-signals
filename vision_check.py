#!/usr/bin/env python3
"""vision_check.py — exercise the IMAGE extractor against known images.

The vision layer had no harness: every test stubs the brain, so extraction was
only ever proven by a live message. This runs the real extractor and compares
what it reads with the truth already in the ledger (the strikes the text names).

  python vision_check.py --msg 4047            # one stored photo
  python vision_check.py --fixtures            # the known ticket/chart set
  python vision_check.py --ledger --limit 15   # sample against ledger ground truth
  python vision_check.py --ledger --all        # every testable stored image
  python vision_check.py --live                # newest 3 photos in the channel

Exit 0 = every compared field agrees. Exit 2 = mismatch (printed).
Costs Hermes quota (~1 call per image). Read-only: never places or books.
"""
import argparse
import csv
import re
import sys

import james_pipe as jp
from james_images import extract_image_fields

# Images whose text is known, with the strikes the TEXT actually named. A
# mismatch here is a real vision error, not a judgement call.
FIXTURES = {
    # 3734/3855 have no photo: the listener was deaf when they arrived
    "3956": [7625, 7635],      # ticket: "SELL TO OPEN 55 SPXW 7625 ... 7635"
    "3964": [7490, 7500],      # ticket: "BUY TO OPEN 25 SPXW 7490 ... 7500"
    "4047": [],                # chart: no legs expected, levels instead
}


def show(mid):
    photo = jp.PHOTOS / f"{mid}.jpg"
    if not photo.exists():
        print(f"{mid}: no photo on disk")
        return None
    fields = extract_image_fields(str(photo), jp.CFG)
    got = fields.get("strikes") or []
    got_i = sorted(int(round(float(x))) for x in got) if got else []
    print(f"{mid}: strikes={got_i} structure={fields.get('structure')!r} "
          f"underlying={fields.get('underlying')!r} credit={fields.get('credit')!r}")
    return got_i


def ledger_truth():
    """msg ids where the ledger carries BOTH a photo and a text naming strikes.
    The text is the ground truth; the extractor has to agree with it."""
    by = {}
    for r in csv.DictReader(open(jp.HERE / "signals.csv", encoding="utf-8")):
        if r["msg_id"].isdigit() and r.get("raw") and r["msg_id"] not in by:
            by[r["msg_id"]] = r["raw"]
    out = []
    for mid, raw in by.items():
        if not (jp.PHOTOS / f"{mid}.jpg").exists():
            continue
        # text_legs expands the C/P shorthand ("7735C/P" = call AND put at
        # that strike) the way the pipeline does; text_strikes does not, so
        # it understates a straddle and makes the vision look wrong
        _tl = jp.text_legs(raw)
        _src = [float(l["strike"]) for l in _tl] if _tl else jp.text_strikes(raw)
        want = sorted(int(round(float(x))) for x in _src)
        if not want:
            continue
        # ground truth only where the TEXT names a real STRUCTURE — a level
        # ('Spx close to 7710 now') is not a leg, and expecting the extractor to
        # read it as one would manufacture false mismatches
        legs = jp.text_struct(raw)[0]
        if len(legs) < 2 and not jp.has_tradeable_content(raw):
            continue
        # only ENTRY tickets are ground truth for an image: an exit message
        # ('Pl book gains on 7490/7500 Bull Puts') names strikes but the image
        # carries no ticket, so the vision finding none is CORRECT
        if not jp.entry_language(raw):
            continue
        # an exit/level message or a chart/dealer ladder is NOT a ticket even
        # when it contains 'sell'/'buy' words — the vision finding no legs there
        # is CORRECT (3861/3965/3993/4031/4047 all looked like tickets to a
        # crude filter and were not)
        _low = raw.lower()
        if re.search(r"book gains|take gains|pl book|is target|next target", _low):
            continue
        if re.search(r"rlm|mtfdom|o\s*[\d,]+\.\d+\s+h|range\s+h", _low):
            continue
        # anchor 2-digit strikes the way the pipeline does ("Buy 70P" is 7670P
        # on an SPX 5-wide) — the raw text drops the leading digits, the image
        # does not, so comparing un-anchored makes the VISION look wrong
        big = [w for w in want if w >= 100]
        if big:
            base = int(max(big) // 100) * 100
            want = sorted(base + w if w < 100 else w for w in want)
        out.append((mid, want, raw))   # full text: truncating breaks the credit parse
    return out


def ledger_run(limit, offset=0):
    rows = ledger_truth()
    if offset:
        rows = rows[offset:]
    if limit:
        rows = rows[:limit]
    ok = bad = 0
    c_ok = c_bad = 0
    for mid, want, raw in rows:
        photo = jp.PHOTOS / f"{mid}.jpg"
        fields = extract_image_fields(str(photo), jp.CFG)
        got = sorted(int(round(float(x))) for x in (fields.get("strikes") or []))
        vcr = fields.get("credit")
        tcr = jp.text_credit(raw)
        if got and all(w < 100 for w in want) and all(g >= 100 for g in got):
            # the text carries ONLY 2-digit forms ("20/30 Bear calls") and the
            # image read the full 4-digit strikes. Anchor the text to the
            # vision's own hundreds and compare the DIGITS: agreement means the
            # image read the number right and the text just dropped its prefix.
            base = int(max(got) // 100) * 100
            want = sorted(base + w for w in want)
        # compare DISTINCT strikes: the text cannot express a duplicate leg
        # ("7720C/P" is two legs at 7720), so a leg-count diff is information,
        # not a mismatch. A real mismatch is a strike the text never named.
        if set(got) == set(want):
            print(f"{mid}: strikes OK {want} credit={vcr} (text {tcr})")
            ok += 1
        else:
            print(f"{mid}: STRIKES MISMATCH want {want} got {got}  <- {raw[:80]}")
            bad += 1
        if vcr is not None and tcr:
            if abs(float(vcr) - float(tcr)) <= 0.15 * max(float(tcr), 0.01):
                c_ok += 1
            else:
                c_bad += 1
                print(f"     CREDIT OFF: vision {vcr} vs text {tcr}")  # raw shown below
        elif vcr is None and tcr:
            # a MISSING credit when the text has one is a miss, not a skip —
            # silently ignoring it hid 3607 (vision None vs text 2.5)
            c_bad += 1
            print(f"     CREDIT MISSING: vision None vs text {tcr}")
    print(f"strikes: {ok} match, {bad} mismatch  |  credit: {c_ok} ok, {c_bad} off "
          f"({ok+bad} run)")
    return 2 if (bad or c_bad) else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--msg")
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--ledger", action="store_true",
                    help="sample stored images with ledger ground truth")
    ap.add_argument("--all", action="store_true", help="with --ledger: every testable image")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    if a.msg:
        show(a.msg)
        return 0

    if a.live:
        from telethon.sync import TelegramClient
        env = jp.load_env()
        c = TelegramClient(str(jp.HERE / "telegram_review.session"),
                           int(env["TELEGRAM_API_ID"]), env["TELEGRAM_API_HASH"])
        c.connect()
        ch = c.get_entity("James Private Signal Inbox")
        got = 0
        for m in c.iter_messages(ch, limit=25):
            if not m.photo or got >= 3:
                continue
            p = jp.PHOTOS / f"{m.id}.jpg"
            jp.PHOTOS.mkdir(exist_ok=True)
            m.download_media(file=str(p))
            show(str(m.id))
            got += 1
        c.disconnect()
        return 0

    if a.ledger:
        return ledger_run(None if a.all else a.limit, a.offset)

    if not a.fixtures:
        ap.print_help()
        return 0

    bad = 0
    for mid, want in FIXTURES.items():
        got = show(mid) or []
        if not want:
            print(f"   chart fixture — expect no legs (got {got})")
            continue
        if got == sorted(want):
            print("   OK")
        else:
            print(f"   MISMATCH want {sorted(want)}")
            bad += 1
    print(f"\n{bad} mismatch(es)")
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
