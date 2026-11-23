"""signal_scorecard.py — score JAMES's signals on HIS numbers (2026-11-21).

Format (user, 2026-11-21) — his axis, not our paper book:

    #  Signal            Entry → exit (his)  Return   Result
    1  AMD 555C          1.73 → 12.91         +646%    ✅ WIN
    —  HGV 50C           0.95 →               —        ⏳ PENDING (exp Dec 18)
    —  SPY 747C          no prices            —        🚫 UNSCOREABLE

Rules:
  * a signal is recorded the moment it ARRIVES — entry filled, exit blank;
    the exit/claim fills in later from HIS message (price, target-done, or
    an "N% up / N% now" claim). Never guessed: no exit -> stays PENDING.
  * no entry price at all -> UNSCOREABLE immediately (his example row).
  * returns are HIS: longs (exit-entry)/entry, credits (entry-exit)/entry.
  * narrative + relationship tracking stay in trade_lifecycle (timestamped
    LINKs) — this table is the numeric axis only.

Pipe hook: _parse_trade calls track() after validate_card (advisory only,
never blocks, never trades). Idempotent by msg_id (reprocessing safe).
Run:  python signal_scorecard.py          # print the table
      python signal_scorecard.py --push    # push to Saved Messages
"""
import argparse
import csv
import datetime
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
CSV = HERE / "signal_scorecard.csv"
COLS = ["ts", "msg_id", "signal", "underlying", "expiry", "direction",
        "entry", "targets", "exit", "return_pct", "result", "note"]


def enabled():
    """False under the test suite (D145): fixture replays of _parse_trade
    carry fake msg ids and must never write the LIVE scorecard CSV — the
    first full-suite run after D143 went live dirtied it with 21 rows
    (r-bracket, ok1, 4033, ...). Direct calls from test_signal_scorecard
    patch CSV to a temp path and are unaffected."""
    import sys as _s
    a = (_s.argv[0] if _s.argv else "").lower().replace("\\", "/")
    b = a.rsplit("/", 1)[-1]
    return not (b.startswith("test_") or b.startswith("pytest"))


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S")


def _load():
    if not CSV.exists():
        return []
    with CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save(rows):
    # the file is rewritten WHOLE every save — the header must be rewritten
    # with it (the old `new = not exists` check truncated first and only wrote
    # the header on creation, so the second save dropped it and the next
    # _load read data values as fieldnames).
    with CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)


def _canon(tok):
    try:
        import ticker_taxonomy as tx
        return (tx.canonical_of(tok) or tok).upper()
    except Exception:
        return tok


def _underlying_of(norm):
    """Canonical underlying for matching (SPXW/SPX500 -> SPX, 'WMT 128C' -> WMT)."""
    raw = (norm.get("instrument")
           or (norm.get("option") or {}).get("underlying") or "")
    parts = str(raw).upper().split()
    return _canon(parts[0]) if parts else ""


def _inst_from_text(text):
    """INVALID cards carry no norm — pull the ticker out of the raw text
    ('SPY 747C 20% up' -> SPY, 'SPXW 7170/7155P' -> SPX)."""
    m = re.search(r"\b([A-Za-z]{1,6})\s+\d{2,5}(?:\.\d+)?\s*/\s*"
                  r"\d{2,5}(?:\.\d+)?\s*[CP]\b", text or "")
    if not m:
        m = re.search(r"\b([A-Za-z]{1,6})\s+\d{2,5}(?:\.\d+)?\s*[CP]\b",
                      text or "")
    return _canon(m.group(1)) if m else ""


def _exp_from_text(text):
    """'for 11/20' / 'for 12/18' -> ISO date (a date more than 3 days behind
    rolls to next year; a 3-day grace keeps a just-expired 'for 11/20' read
    in the settle window on its own year)."""
    m = re.search(r"\bfor\s+(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", text or "")
    if not m:
        return ""
    mo, dy = int(m.group(1)), int(m.group(2))
    yr = int(m.group(3)) if m.group(3) else None
    if yr is None:
        yr = datetime.date.today().year
        try:
            d = datetime.date(yr, mo, dy)
        except ValueError:
            return ""
        if d < datetime.date.today() - datetime.timedelta(days=3):
            yr += 1
    try:
        datetime.date(yr, mo, dy)
    except ValueError:
        return ""
    return f"{yr:04d}-{mo:02d}-{dy:02d}"


def _signal_name(norm, text=""):
    opt = norm.get("option") or {}
    if opt.get("strike") and opt.get("right"):
        r = "C" if str(opt.get("right")).lower().startswith("c") else "P"
        und = str(opt.get("underlying") or norm.get("instrument") or "").split()
        und = und[0] if und else _inst_from_text(text)
        return f"{und} {float(opt['strike']):g}{r}"
    legs = (norm.get("spread") or {}).get("legs") or []
    ks = sorted(float(l.get("strike")) for l in legs if l.get("strike"))
    if len(ks) >= 2 and legs:
        r = "P" if str(legs[0].get("right", "p")).lower().startswith("p") else "C"
        und = str(norm.get("instrument") or "").split()
        und = und[0] if und else _inst_from_text(text)
        return f"{und} {ks[0]:g}/{ks[-1]:g}{r}"
    inst = str(norm.get("instrument") or "").split()
    if inst:
        return inst[0]
    # INVALID card: rebuild the name from the raw text ('SPY 747C')
    m = re.search(r"\b([A-Za-z]{1,6})\s+(\d{2,5}(?:\.\d+)?)\s*/\s*"
                  r"(\d{2,5}(?:\.\d+)?)\s*([CP])\b", text or "")
    if m:
        return (f"{m.group(1).upper()} {float(m.group(2)):g}/"
                f"{float(m.group(3)):g}{m.group(4).upper()}")
    m = re.search(r"\b([A-Za-z]{1,6})\s+(\d{2,5}(?:\.\d+)?)\s*([CP])\b",
                  text or "")
    if m:
        return f"{m.group(1).upper()} {float(m.group(2)):g}{m.group(3).upper()}"
    return "?"


def _fmt_exp(iso):
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
        return f"{d.strftime('%b')} {d.day}"
    except (ValueError, TypeError):
        return ""


def _txt_credit(text):
    try:
        import james_pipe as jp
        return jp.text_credit(text or "")
    except Exception:
        return None


def _txt_entry(text):
    """Entry price from text when the brain gave none: his credit form first
    ('collect $440' -> 4.40), else an 'at $X' fill price (x100 convention)."""
    v = _txt_credit(text)
    if v is not None:
        return v
    m = re.search(r"\b(?:at|@)\s*\$?\s*([0-9]+(?:\.[0-9]+)?)", text or "")
    if not m:
        return None
    v = float(m.group(1))
    if v >= 30:
        v = v / 100.0
    return round(v, 2)


# opening-report context — a past-tense fill ('Sold QQQ 648P n collected
# $253 for today') must never be read as a close of an existing position.
_ENTRY_CTX = re.compile(
    r"\bcollect\w*\b|\bn\s+(?:buy|sell)\b|\bto\s+open\b|"
    r"\bfor\s+\$|"
    r"\b(?:for|on|expires?)\s+(?:today|tomorrow|this\s+(?:morning|afternoon)|"
    r"\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2})\b", re.I)


def _exit_shaped(text):
    """Full exits only (not trims): his exit vocabulary."""
    t = text or ""
    try:
        import james_pipe as jp
        # a TRIM is partial management — his scorecard rows are per-signal and
        # only a FULL exit closes one ('trim 80%' must leave the row pending)
        if jp.non_entry_class(t) == "trim":
            return False
        if jp.exit_command_wins(t) or jp.is_explicit_close(t):
            return True
    except Exception:
        pass
    if re.search(r"\bbought\s+back\b|\bstopp?ed\s+(?:out|it)\b", t, re.I):
        return True
    # a reported close of a named leg ('sold AMD 555C at 12.91', 'closed my
    # 555C at 3.20', 'took my 555C') — entry context keeps it an entry.
    if re.search(r"\b(?:sold|closed|took|booked)\b", t, re.I) and \
            re.search(r"\b(?:at|@)\s*\$?\s*\d|\b\d{2,5}(?:\.\d+)?\s*[CP]\b",
                      t, re.I) and not _ENTRY_CTX.search(t):
        return True
    return False


def _pct_status(text):
    """D134 rule: bare '<n>% up/now' with no command = a return CLAIM."""
    t = text or ""
    if not re.search(r"\d{1,3}\s*%\s*(?:up|now)", t, re.I):
        return False
    try:
        import james_pipe as jp
        return not jp.entry_language(t) and not jp.ACTION_VERB.search(t)
    except Exception:
        return True


def _claim(text, norm):
    """His stated return on the position ('80% up', '100% now') — last match."""
    ms = re.findall(r"(\d{1,3})\s*%\s*(?:up|now)", text or "", re.I)
    if ms:
        return float(ms[-1])
    pct = norm.get("pct_actual")
    return float(pct) if pct is not None else None


def _exit_price(text):
    """His exit price from text: 'bought back at $235' -> 2.35 (options are
    quoted in dollars; >=30 means the message used the x100 convention)."""
    m = re.search(r"\b(?:at|@)\s*\$?\s*([0-9]+(?:\.[0-9]+)?)", text or "")
    if not m:
        return None
    v = float(m.group(1))
    if v >= 30:
        v = v / 100.0
    return round(v, 2)


def _find_pending(rows, underlying):
    """Oldest PENDING row for that underlying (FIFO — each signal is scored
    once; a later re-entry gets its own row). With NO underlying named
    ('bought back at $235'), bind only when exactly one signal is open —
    never guess between two."""
    pend = [r for r in rows if r.get("result") == "PENDING"]
    if underlying:
        for r in pend:
            if r.get("underlying") == underlying:
                return r
        return None
    return pend[0] if len(pend) == 1 else None


def _close_row(row, exit_px=None, claim=None, note=""):
    if claim is not None:
        row["return_pct"] = f"{float(claim):+.0f}"
        row["result"] = "WIN" if float(claim) > 0 else (
            "LOSS" if float(claim) < 0 else "FLAT")
        row["exit"] = row.get("exit") or ""
    else:
        try:
            e = float(row.get("entry"))
            x = float(exit_px)
            pct = ((x - e) / e * 100.0 if str(row.get("direction")).lower() != "short"
                   else (e - x) / e * 100.0)
            row["exit"] = f"{x:g}"
            row["return_pct"] = f"{pct:+.0f}"
            row["result"] = "WIN" if pct > 0 else ("LOSS" if pct < 0 else "FLAT")
        except (TypeError, ValueError):
            row["return_pct"] = ""
            row["result"] = "UNSCOREABLE"
    if note:
        row["note"] = note
    return row


def track(norm, text, msg_id):
    """THE PIPE HOOK (D143): score one message against the scorecard.
    Order: target-done -> exit (price/claim/note) %-status claim -> new
    entry. Idempotent by msg_id; never raises into the pipe."""
    text = text or ""
    msg_id = str(msg_id)
    rows = _load()
    if any(str(r.get("msg_id")) == msg_id for r in rows):
        return None
    inst = _underlying_of(norm) or _inst_from_text(text)

    # 1. '1st target done' -> close at the nth stored target
    m = re.search(r"\b(\d)(?:st|nd|rd|th)\s+target\s+done", text, re.I)
    if m:
        row = _find_pending(rows, inst)
        if row:
            n = int(m.group(1))
            try:
                tg = [float(x) for x in str(row.get("targets") or "").split("|") if x]
            except (TypeError, ValueError):
                tg = []
            if 1 <= n <= len(tg):
                _close_row(row, exit_px=tg[n - 1], note=f"T{n} hit")
            else:
                row["note"] = (row.get("note") or "") + \
                    f"; target {n} done, no level recorded"
            _save(rows)
            return row

    # 2. a full exit: price first, then his %-claim, else note + stay pending
    if _exit_shaped(text):
        row = _find_pending(rows, inst)
        if row is None:
            return None            # an exit report with nothing open = no row
        px = _exit_price(text)
        if px is not None:
            _close_row(row, exit_px=px, note="exit price from his message")
        else:
            cl = _claim(text, norm)
            if cl is not None:
                _close_row(row, claim=cl, note="return claimed in his exit msg")
            else:
                row["note"] = (f"exit called {_now()[:16]} — no price posted "
                               f"(stays pending per rule)")
        _save(rows)
        return row

    # 3. a bare %-status claim ('80% up') closes the matching pending —
    # with NOTHING open it falls through: 'SPY 747C 20% up' as a first
    # message is a NEW signal row (his unscoreable example), not a claim.
    if _pct_status(text):
        cl = _claim(text, norm)
        row = _find_pending(rows, inst)
        if cl is not None and row is not None:
            _close_row(row, claim=cl, note="return claimed ('% up')")
            _save(rows)
            return row

    # 4. an entry-shaped message opens a row (exit blank — filled later)
    entry_shaped = False
    try:
        import james_pipe as jp
        if jp.entry_language(text) or jp.has_tradeable_content(text):
            entry_shaped = True
        elif not text.strip() and (norm.get("spread") or norm.get("option")) \
                and str(norm.get("action") or "") == "entry":
            entry_shaped = True      # image-only ticket (his fills arrive as photos)
    except Exception:
        return None
    if not entry_shaped:
        # his no-price style: a strike + his own % ('SPY 747C "20%"') — a
        # signal with no premium anywhere -> UNSCOREABLE row, never dropped
        if re.search(r"\b\d{2,5}(?:\.\d+)?\s*[CP]\b", text, re.I) and \
                re.search(r"\d{1,3}\s*%", text):
            entry_shaped = True
    if not entry_shaped or not inst:
        return None
    entry = norm.get("entry")
    if entry is None:
        entry = _txt_entry(text)
    entry_s = f"{float(entry):g}" if entry not in (None, "") else ""
    direction = str(norm.get("direction") or "").lower()
    if direction not in ("long", "short"):
        # 'collect $440' / 'sell ... n buy ...' is a credit (short) structure
        direction = ("short" if re.search(r"\bcollect|\bsell\b", text, re.I)
                     else "long")
    targets = "|".join(str(x) for x in (norm.get("targets") or []) if x)
    rows.append({
        "ts": _now(), "msg_id": msg_id,
        "signal": _signal_name(norm, text),
        "underlying": inst,
        "expiry": str(norm.get("expiry") or _exp_from_text(text)),
        "direction": direction,
        "entry": entry_s, "targets": targets, "exit": "", "return_pct": "",
        "result": ("PENDING" if entry_s else "UNSCOREABLE"),
        "note": ("no prices in the signal" if not entry_s else ""),
    })
    _save(rows)
    return rows[-1]


def add(signal, underlying, entry, direction, expiry, targets="",
        msg_id="", note=""):
    """Manual seed for signals that arrived before the hook existed."""
    rows = _load()
    if msg_id and any(str(r.get("msg_id")) == str(msg_id) for r in rows):
        return None
    row = {"ts": _now(), "msg_id": str(msg_id), "signal": signal,
           "underlying": str(underlying).upper(), "expiry": str(expiry),
           "direction": direction, "entry": str(entry), "targets": targets,
           "exit": "", "return_pct": "",
           "result": ("PENDING" if str(entry) else "UNSCOREABLE"),
           "note": note}
    rows.append(row)
    _save(rows)
    return row


def _cell(r):
    e = r.get("entry") or ""
    x = r.get("exit") or ""
    if r.get("result") == "UNSCOREABLE":
        return "no prices", "—", "🚫 UNSCOREABLE"
    if r.get("result") == "PENDING":
        exp = _fmt_exp(r.get("expiry"))
        tail = f" (exp {exp})" if exp else ""
        note = r.get("note") or ""
        if "no price" in note:
            tail = " · exit called, no price"
        return (f"{e} → " if e else "—"), "—", f"⏳ PENDING{tail}"
    ret = r.get("return_pct") or ""
    if ret.startswith("+"):
        res = f"✅ WIN"
    elif ret.startswith("-"):
        res = f"❌ LOSS"
    else:
        res = "➖ FLAT"
    if r.get("note") and "T" == str(r.get("note"))[:1]:
        res += f" · {r['note']}"
    return (f"{e} → {x}" if e else "—"), (ret + "%" if ret else "—"), res


def report(rows=None):
    rows = _load() if rows is None else rows
    lines = ["📋 SIGNAL SCORECARD — his fills (record now, score later)", "",
             "| # | Signal | Entry → exit (his) | Return | Result |",
             "|---|--------|--------------------|--------|--------|"]
    n = 0
    pend = unsc = 0
    for r in rows:
        ret_col, ret_val, res = _cell(r)
        scored = r.get("result") in ("WIN", "LOSS", "FLAT")
        if scored:
            n += 1
            idx = str(n)
        else:
            idx = "—"
            if r.get("result") == "PENDING":
                pend += 1
            else:
                unsc += 1
        lines.append(f"| {idx} | {r.get('signal')} | {ret_col} | "
                     f"{ret_val} | {res} |")
    if not rows:
        lines.append("| — | (no signals yet) | | | |")
    lines += ["", f"scored {n} · pending {pend} · unscoreable {unsc}"]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)
    out = report()
    print(out)
    if a.push:
        import time as _t
        import james_pipe as jp
        st = {"alerts_today": 0, "alerts_date": jp.today(), "push_recent": {}}
        jp.push(out, st)
        _t.sleep(8)
        print("pushed to Saved Messages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
