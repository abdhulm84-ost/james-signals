"""One-off: stamp an ESTIMATED tier on closed trades that predate the field.
Estimates use structure/credit/width only (entry-time spot and DTE are gone), so
these rows carry tier_est=True and are described as estimates in the report."""
import json
import sys

import james_pipe as jp

path = jp.STATE
st = json.loads(path.read_text(encoding="utf-8"))
n = 0
for t in st.get("closed", {}).values():
    if t.get("tier") or str(t.get("id", "")).startswith("TEST"):
        continue
    legs = t.get("legs") or []
    ks = []
    for l in legs:
        if isinstance(l, (list, tuple)) and l and l[0] is not None:
            ks.append(float(l[0]))
    width = (max(ks) - min(ks)) if len(ks) >= 2 else 0
    q = None
    if width and t.get("entry"):
        try:
            q = float(t["entry"]) / width
        except (TypeError, ValueError):
            q = None
    defined = len(legs) >= 2
    t["tier"] = jp.tier_of(defined=defined, legs_n=len(legs),
                           short=bool(t.get("naked")),
                           lotto=bool(t.get("entry") and float(t["entry"]) <= 1.5),
                           quality=q, dte=1, itm=False)
    t["tier_est"] = True
    n += 1
path.write_text(json.dumps(st, indent=1), encoding="utf-8")
print(f"backfilled {n} closed trade(s) with estimated tiers")
