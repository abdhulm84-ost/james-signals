import subprocess

models = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-001",
    "gemini-1.5-flash-002",
    "gemini-1.5-flash-latest",
    "gemini-1.5-pro-001",
    "gemini-1.5-pro-002",
    "gemini-1.5-pro-latest",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-pro",
    "gemini-pro-vision"
]

for m in models:
    print(f"Testing model: {m}...")
    cmd = [
        "C:\\Users\\passm\\AppData\\Local\\hermes\\bin\\hermes.cmd", "chat",
        "-m", f"google/{m}",
        "-q", "hello",
        "--provider", "gemini",
        "--oneshot"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    print("  RC:", r.returncode)
    if "404" in r.stdout or "404" in r.stderr:
        print("  Result: 404 (Not Found)")
    elif "429" in r.stdout or "429" in r.stderr:
        print("  Result: 429 (Quota Exceeded)")
    else:
        print("  STDOUT:", r.stdout[:300].replace("\n", " "))
        print("  STDERR:", r.stderr[:300].replace("\n", " "))
