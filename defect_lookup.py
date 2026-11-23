#!/usr/bin/env python3
"""defect_lookup.py — has this symptom come up before?

  python defect_lookup.py --msg 3900        # what defect did this message expose?
  python defect_lookup.py --word credited   # search symptom/fix text
  python defect_lookup.py --list            # all 32 rows, one line each

Read-only. Prints the row and the guard test to run.
"""
import argparse, os, re, subprocess, sys

REG = "DEFECT_REGISTER.md"


def rows():
    out = []
    for line in open(REG, encoding="utf-8"):
        if not line.startswith("| D"):
            continue
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        out.append({"id": c[0], "trigger": c[1], "defect": c[2],
                    "symptom": c[3], "fix": c[4], "test": c[5]})
    return out


def msg_text(mid):
    """The triggering message itself. D21/D22 msgs were dropped before ingest
    so they are absent from signals.csv — resolve them from the raw channel
    capture instead. Never backfill the ledger: it is the record of what the
    pipeline actually did, and inserting rows it never processed falsifies it."""
    import csv as _csv
    try:
        for r in _csv.DictReader(open("signals.csv", encoding="utf-8")):
            if r.get("msg_id") == mid and r.get("raw"):
                return r["raw"], "signals.csv"
    except Exception:
        pass
    try:
        for line in open("spx_window_raw.txt", encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 4 and parts[0] == mid:
                src = "spx_window_raw.txt" + ("" if parts[2] == "txt" else " (image)")
                return (parts[3] or "(image only)"), src
    except Exception:
        pass
    return None, None


def show(r):
    print(f"{r['id']}  (trigger: {r['trigger']})")
    print(f"  defect : {r['defect']}")
    print(f"  symptom: {r['symptom']}")
    print(f"  fix    : {r['fix']}")
    print(f"  test   : {r['test']}")
    print()


def postmortem(did, rs):
    """Resurfacing post-mortem. The guard test decides the diagnosis:
    test FAILS  -> the fix REGRESSED; restore it.
    test PASSES -> a DIFFERENT PATH bypasses it; fix that path and extend the
                   test. Never weaken the test."""
    hit = next((r for r in rs if r["id"].lower() == did.lower()), None)
    if not hit:
        print(f"no defect row {did}")
        return 1
    tests = re.findall(r"test_[a-z_]+\.py", hit["test"])
    import subprocess
    results = []
    for t in tests:
        rc = subprocess.run([sys.executable, t], capture_output=True).returncode
        results.append((t, "PASS" if rc == 0 else "FAIL"))
    allpass = all(r == "PASS" for _, r in results)
    verdict = ("A DIFFERENT PATH bypasses it — fix that path and EXTEND the test"
               if allpass else
               "the fix REGRESSED — restore it, do not weaken the test")
    import datetime
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [f"# Post-mortem {hit['id']} — resurfaced {stamp}", "",
           f"trigger      : {hit['trigger']}",
           f"defect       : {hit['defect']}",
           f"symptom      : {hit['symptom']}",
           f"original fix : {hit['fix']}",
           f"guard tests  : {', '.join(t for t, _ in results) or 'none'}",
           f"test verdict : " + "; ".join(f"{t}={r}" for t, r in results),
           f"diagnosis    : {verdict}", "",
           "## Why it happened (fill in)",
           "- path that bypassed the fix:",
           "- why the test did not catch it:", "",
           "## Permanent fix (fill in)", "- code change:", "- test added:", "",]
    os.makedirs("postmortems", exist_ok=True)
    fn = f"postmortems/{hit['id'].lower()}_{stamp.replace(' ','_').replace(':','')}.md"
    open(fn, "w", encoding="utf-8").write(chr(10).join(out))
    print(chr(10).join(out))
    print(f"written: {fn}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--msg")
    ap.add_argument("--word")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--run", action="store_true", help="run the matched tests")
    ap.add_argument("--postmortem", metavar="ID",
                    help="write a resurfacing post-mortem for a defect row")
    a = ap.parse_args()
    rs = rows()
    if a.postmortem:
        return postmortem(a.postmortem, rs)
    if a.list:
        for r in rs:
            print(f"{r['id']}  {r['trigger']:12s} {r['defect'][:70]}")
        return 0
    hits = []
    if a.msg:
        hits = [r for r in rs if a.msg in r["trigger"]]
        txt, src = msg_text(a.msg)
        if txt:
            print(f"[{a.msg}] {txt[:160]}")
            print(f"   source: {src}")
            if src.startswith("spx_window_raw"):
                print("   NOTE: never reached the pipeline (listener drop) — "
                      "the defect IS the absence, see D21/D22")
            print()
    elif a.word:
        w = a.word.lower()
        hits = [r for r in rs if w in (r["defect"] + r["symptom"] + r["fix"]).lower()]
    if not hits:
        print("no matching defect row")
        return 1
    for r in hits:
        show(r)
    if a.run:
        tests = sorted({t for r in hits for t in re.findall(r"test_[a-z_]+\.py", r["test"])})
        bad = [t for t in tests if subprocess.run([sys.executable, t]).returncode]
        print("tests:", tests)
        print("FAILING:", bad or "none — the fix is intact; look for another path")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
