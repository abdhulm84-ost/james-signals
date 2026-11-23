#!/usr/bin/env python3
"""test_drift_guard.py — the DRIFT AUDIT (2026-11-19).

The D110/D112/D113 chain was ONE disease: a rule lived in two or three copies,
and each fix patched only one copy. This test is the pre-dent machinery — it
PROVES there is no second copy, instead of hoping there isn't.

It strips comments + docstrings from every .py module, then greps the remaining
CODE for the known rule-patterns. Any match OUTSIDE the rule's canonical home
is a drift and fails the suite. Canonical homes and test files are whitelisted.

If someone reintroduces a stale verb list / credit regex / ticker map, the
suite turns red at the next run — the drift is caught BEFORE it causes a dent.

Canonical homes:
  * verb lexicon + credit regex + strike parse -> signal_intent.py
  * ticker canonicalisation               -> ticker_taxonomy.py
Run: python test_drift_guard.py   (exit 0 = no drift)
"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


def _strip_comments_and_docstrings(src):
    """Remove # comments and triple-quoted strings so a rule quoted in prose is
    not mistaken for a code copy. Keeps code lines verbatim."""
    out = []
    in_triple = None
    for line in src.splitlines():
        if in_triple:
            if in_triple in line:
                in_triple = None
            continue
        m = re.search(r'("""|\'\'\')', line)
        if m:
            in_triple = m.group(1)
            line = line[:m.start()]
        line = re.sub(r"#.*$", "", line)
        if line.strip():
            out.append(line)
    return "\n".join(out)


# (name, regex fingerprint, canonical home module)
RULES = [
    ("verb_lexicon", re.compile(r"\b(?:sell\|sold\|write\|wrote\|selling\|short\|shorting)\b"), "signal_intent.py"),
    ("verb_lexicon", re.compile(r"\b(?:buy\|bought\|buying\|long\|grab\|enter\|picking\s+up)\b"), "signal_intent.py"),
    ("credit_regex", re.compile(r"c\+o\+l\[l\]\?e\{1,2\}c\+t"), "signal_intent.py"),
    ("credit_regex", re.compile(r"\bcoleect\b"), "signal_intent.py"),
    ("ticker_map", re.compile(r'"SPXW"\s*:\s*"SPX"'), "ticker_taxonomy.py"),
    ("ticker_map", re.compile(r'"NDX100"\s*:\s*"NDX"'), "ticker_taxonomy.py"),
    ("ticker_map", re.compile(r'"RUSSELL"\s*:\s*"RUT"'), "ticker_taxonomy.py"),
]

QUERY_ONLY = {"prior_shape.py"}


def _scan():
    hits = []
    for f in sorted(HERE.glob("*.py")):
        if f.name.startswith("test_") or f.name in QUERY_ONLY:
            continue
        code = _strip_comments_and_docstrings(f.read_text(encoding="utf-8", errors="replace"))
        for rname, rx, canonical in RULES:
            if f.name == canonical:
                continue
            if rx.search(code):
                hits.append((f.name, rname))
    return hits


hits = _scan()
check("no rule pattern duplicated outside its canonical home",
      not hits,
      " | ".join(f"{f}:{r}" for f, r in hits))

# -- the canonical homes must actually hold the rule ----------------------
try:
    import signal_intent as si
    check("signal_intent holds the verb lexicon",
          hasattr(si, "SELL_WORDS") and hasattr(si, "BUY_WORDS")
          and hasattr(si, "COLLECT_WORDS") and hasattr(si, "PAY_WORDS"))
    check("signal_intent holds parse_credit",
          callable(getattr(si, "parse_credit", None)))
    import ticker_taxonomy as tx
    check("ticker_taxonomy holds the index map",
          hasattr(tx, "INDEX_ROOTS") and "SPXW" in tx.INDEX_ROOTS)
except ImportError as e:
    check("canonical modules importable", False, str(e))

# -- consumers delegate, never re-implement -------------------------------
try:
    src_jp = (HERE / "james_pipe.py").read_text(encoding="utf-8", errors="replace")
    src_mt = (HERE / "message_tag.py").read_text(encoding="utf-8", errors="replace")
    code_jp = _strip_comments_and_docstrings(src_jp)
    code_mt = _strip_comments_and_docstrings(src_mt)
    check("text_is_naked_short has no shadow verb list (hard dep)",
          "sells = len(re.findall" not in code_jp)
    check("message_tag.classify has no shadow verb list (hard dep)",
          "sell|sold" not in code_mt)
    check("_canon_underlying has no inline ticker map (hard dep)",
          '"SPXW": "SPX"' not in code_jp)
    check("text_credit delegates to signal_intent.parse_credit",
          "parse_credit" in code_jp)
except ImportError:
    pass

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
