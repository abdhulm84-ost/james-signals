import subprocess
import sys

models = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-pro-latest",
    "gemini-2.5-flash",
    "gemini-pro"
]

with open("gemini_tests.log", "w", encoding="utf-8") as f_log:
    for m in models:
        msg = f"Testing model: {m}...\n"
        print(msg, end="", flush=True)
        f_log.write(msg)
        cmd = [
            "C:\\Users\\passm\\AppData\\Local\\hermes\\bin\\hermes.cmd", "chat",
            "-m", f"google/{m}",
            "-q", "hello",
            "--provider", "gemini",
            "--oneshot"
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=30)
            rc_msg = f"  RC: {r.returncode}\n"
            print(rc_msg, end="", flush=True)
            f_log.write(rc_msg)
            if "404" in r.stdout or "404" in r.stderr:
                res_msg = "  Result: 404 (Not Found)\n"
            elif "429" in r.stdout or "429" in r.stderr:
                res_msg = "  Result: 429 (Quota Exceeded)\n"
            else:
                res_msg = f"  STDOUT: {r.stdout[:150]}\n  STDERR: {r.stderr[:150]}\n"
            print(res_msg, end="", flush=True)
            f_log.write(res_msg)
        except subprocess.TimeoutExpired:
            timeout_msg = "  Result: TIMEOUT\n"
            print(timeout_msg, end="", flush=True)
            f_log.write(timeout_msg)
        f_log.flush()
print("Done!")
