p = "james_pipe.py"
s = open(p, encoding="utf-8").read()

helper = '''def text_bracket(text):
    """A conditional ENTRY bracket from a levels view:
    'bearish below 28710 / bullish above 28880' + an S/R ladder.
    Returns {"up": x, "down": y, "sr": {..}} or None."""
    t = text or ""
    up = re.search(r"bullish\s+above\s*:?\s*\$?(\d{3,5}(?:\.\d+)?)", t, re.I)
    dn = re.search(r"bearish\s+below\s*:?\s*\$?(\d{3,5}(?:\.\d+)?)", t, re.I)
    if not (up or dn):
        return None
    sr = {}
    for m in re.finditer(r"\b([SR][1-3])\s*:\s*\$?(\d{3,5}(?:\.\d+)?)", t, re.I):
        sr[m.group(1).upper()] = float(m.group(2))
    return {"up": float(up.group(1)) if up else None,
            "down": float(dn.group(1)) if dn else None, "sr": sr}


def arm_bracket(norm, msg_id, text, st):
    """Store the plan; a crossing in manage_armed() opens the trade."""
    bp = text_bracket(text)
    if not bp or not (bp["up"] or bp["down"]):
        return None
    inst = norm.get("instrument") or ""
    if not inst:
        return None
    aid = f"AR{msg_id}"
    plan = {"id": aid, "msg_id": msg_id, "instrument": inst,
            "up": bp["up"], "down": bp["down"], "sr": bp["sr"],
            "session": us_session_for_today().isoformat(), "opened": now()}
    st.setdefault("armed", {})[aid] = plan
    ledger_row({"event": "ARMED_BRACKET", "msg_id": msg_id, "instrument": inst,
                "targets": "|".join(f"{k}={v:g}" for k, v in bp["sr"].items()),
                "reason": f"conditional: long >= {bp['up']}, short <= {bp['down']}",
                "raw": (text or "")[:80]})
    push("🎯 BRACKET ARMED (msg %s): %s — long above %s / short below %s "
         "(levels %s). Fires once on a cross this session."
         % (msg_id, inst, bp["up"], bp["down"],
            ", ".join(f"{k}:{v:g}" for k, v in bp["sr"].items()) or "—"), st)
    return plan


def manage_armed(st):
    """Fire armed brackets on a level cross, expire them at the session end."""
    armed = st.get("armed") or {}
    if not armed:
        return
    sess = us_session_for_today().isoformat()
    for aid, pl in list(armed.items()):
        if pl.get("session") != sess or open_hours_between(pl.get("opened")) > 6:
            armed.pop(aid, None)
            ledger_row({"event": "BRACKET_EXPIRED", "msg_id": pl.get("msg_id"),
                        "instrument": pl.get("instrument"),
                        "reason": "session ended / 6 market hours passed"})
            continue
        try:
            px = _under_quote(_canon_underlying(pl["instrument"]) or pl["instrument"])
        except Exception:
            px = None
        if not px:
            continue
        px = float(px)
        side = None
        if pl.get("up") and px >= pl["up"]:
            side = "long"
        elif pl.get("down") and px <= pl["down"]:
            side = "short"
        if not side:
            continue
        ups = sorted(v for v in pl["sr"].values() if v > px)
        dns = sorted((v for v in pl["sr"].values() if v < px), reverse=True)
        stop = (dns[0] if dns else None) if side == "long" else (ups[0] if ups else None)
        targets = (ups[:2] if side == "long" else dns[:2])
        card = {"instrument": pl["instrument"], "direction": side,
                "entry": px, "stop": stop, "targets": targets,
                "confidence": 55, "horizon": "intraday",
                "rationale": f"armed bracket from msg {pl.get('msg_id')}",
                "spread": None, "option": None, "action": "entry"}
        armed.pop(aid, None)                       # one shot per plan
        try:
            open_trade_bb(card, str(pl.get("msg_id")), st)
        except Exception as e:
            log(f"bracket {aid} fire failed: {type(e).__name__}: {e}")
        save_state(st)


'''
anchor = "def manage_once(st):"
assert s.count(anchor) == 1
s = s.replace(anchor, helper + anchor, 1)

old = '''def manage_once(st):
    """Mark open trades to market; exits on stop/target; pending fill checks."""'''
new = '''def manage_once(st):
    """Mark open trades to market; exits on stop/target; pending fill checks."""
    try:
        manage_armed(st)
    except Exception as e:
        log(f"armed pass failed: {type(e).__name__}: {e}")'''
assert s.count(old) == 1
s = s.replace(old, new, 1)

# arm from the MAP/view branch
old2 = '''    trig = text_trigger(text)'''
new2 = '''    if text_bracket(text):
        arm_bracket(card, msg_id, text, st)
    trig = text_trigger(text)'''
assert s.count(old2) == 1
s = s.replace(old2, new2, 1)

open(p, "w", encoding="utf-8").write(s)
print("bracket wired")
