"""test_hermes_hang.py — a hanging hermes must not freeze the pipeline (D57).

`cmd /c hermes.cmd` spawns grandchildren that inherit the stdout/stderr PIPES;
on timeout the kill cannot reap them and subprocess.run hangs forever — both
11-16 startups froze exactly here (no heartbeats, catch-up never done). The
call now uses file-backed output so the timeout is reliable. The regression
test is bounded: if the hang returns, the thread stays alive and the check
FAILS instead of stalling the suite.
Run: python test_hermes_hang.py   (exit 0 = green)
"""
import pathlib
import tempfile
import threading
import time

import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# a fake hermes that spawns a HANDLE-HOLDING grandchild and never answers —
# exactly the shape that froze the pipe (ping holds stdout/stderr)
d = pathlib.Path(tempfile.mkdtemp())
fake = d / "hermes_hang.cmd"
fake.write_text("@echo off\r\nstart /b ping -n 30 127.0.0.1\r\nping -n 30 127.0.0.1\r\n",
                encoding="utf-8")

jp.CFG = dict(jp.CFG)
jp.CFG.pop("local_brain_url", None)   # D63: the local route carries a 400s
jp.CFG["hermes_bin"] = str(fake)      # budget — the hang test is about the
jp.CFG["hermes_timeout"] = 3          # cloud routes at the shared timeout
jp.CFG["hermes_model"] = "m1"
jp.CFG["hermes_fallback_models"] = ["m2"]

res = {}


def _call():
    t0 = time.time()
    res["card"] = jp.hermes_parse("Sell SPX 7735C n Buy 7745C n collect $550 for today")
    res["dt"] = time.time() - t0


th = threading.Thread(target=_call, daemon=True)
th.start()
th.join(30)
check("hanging hermes returns within the timeout bound (no pipeline freeze)",
      not th.is_alive(), "thread still stuck after 30s")
if not th.is_alive():
    check("and yields no card (retry path takes over)",
          res.get("card") is None, str(res.get("card")))
    check("and the call is bounded by ~2 model timeouts",
          res.get("dt", 99) < 25, f"{res.get('dt'):.1f}s")

# D59: gemini models must route to the gemini key pool, not the nous credits
check("D59: gemini model routes to the gemini provider",
      jp._provider_for("google/gemini-3.5-flash") == "gemini",
      jp._provider_for("google/gemini-3.5-flash"))
check("D59: the primary model keeps the configured provider",
      jp._provider_for(jp.CFG["hermes_model"]) == jp.CFG["hermes_provider"],
      jp._provider_for(jp.CFG["hermes_model"]))

# D60: the route chain must LEAD with nous, fall to gemini, END at the local
# brain (free, unlimited) — and never lose a route to a config slip.
argv_log = d / "argv.log"
echo_bin = d / "hermes_echo.cmd"
echo_bin.write_text("@echo off\r\necho %* >> \"" + str(argv_log).replace("\\", "\\\\") + "\"\r\n",
                    encoding="utf-8")
jp.CFG = dict(jp.CFG)
jp.CFG["hermes_bin"] = str(echo_bin)
jp.CFG["hermes_timeout"] = 10
jp.CFG["hermes_model"] = "primary-model"
jp.CFG["hermes_fallback_models"] = ["google/gemini-3.5-flash"]
jp.CFG["local_brain_url"] = "http://127.0.0.1:1234/v1"
jp.CFG["local_brain_model"] = "qwen/qwen3.5-9b"
jp.hermes_parse("Sell SPX 7735C n Buy 7745C n collect $550 for today")
argv = argv_log.read_text(encoding="utf-8") if argv_log.exists() else ""
# D66/D70: no LOCAL model may ever be invoked. The prohibition is a LOCAL
# endpoint (127.0.0.1 / localhost) — a cloud custom provider (Alibaba Token
# Plan) is the sanctioned primary since D70.
check("D66: NO local-model endpoint is ever called (user directive)",
      "127.0.0.1" not in argv and "localhost" not in argv,
      "local models crashed the machine; never use them here again")
check("D60: gemini fallback still routed to its key pool",
      "--provider gemini" in argv and "google/gemini-3.5-flash" in argv)
check("D70: the configured primary leads the chain",
      ("--provider nous" in argv and "primary-model" in argv)
      or ("--provider custom" in argv and jp.CFG["hermes_model"] in argv),
      argv[:200])

# D64: a dead provider sinks its whole family — stepfun rides nous and must be
# SKIPPED once nous has failed, instead of burning a 180s timeout per parse.
argv_log.write_text("", encoding="utf-8")
jp.CFG["hermes_fallback_models"] = ["google/gemini-3.5-flash",
                                    "stepfun/step-3.7-flash:free"]
jp.hermes_parse("Sell SPX 7735C n Buy 7745C n collect $550 for today")
argv2 = argv_log.read_text(encoding="utf-8") if argv_log.exists() else ""
check("D64: dead nous sinks stepfun (same provider family)",
      "stepfun" not in argv2 and "--provider gemini" in argv2, argv2[-200:])
check("D66: the chain never reaches a local endpoint",
      "127.0.0.1" not in argv2 and "localhost" not in argv2, argv2[-200:])

# D71: the fallback chain must span TWO INDEPENDENT credit pools — Alibaba
# (custom, qwen) and Nous (paid Plus). One pool running dry must never starve
# the other; that is the whole point of a fallback.
# use the REAL config, not the stub CFG the hang section above replaced
import json as _json, pathlib as _pl
_real = _json.loads(_pl.Path("config.json").read_text(encoding="utf-8"))
_saved = jp.CFG
jp.CFG = _real
_chain = [_real["hermes_model"]] + list(_real.get("hermes_fallback_models") or [])
_provs = {jp._provider_for(m) for m in _chain}
jp.CFG = _saved
check("D71: the chain spans independent pools (custom + nous)",
      {"custom", "nous"} <= _provs, str(_provs))
check("D71: qwen models route to the Alibaba pool",
      jp._provider_for("qwen3.8-flash") == "custom", str(jp._provider_for("qwen3.8-flash")))
check("D71: the nous fallback routes to nous, not the alibaba pool",
      jp._provider_for("tencent/hy4-flash") == "nous", str(jp._provider_for("tencent/hy4-flash")))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
