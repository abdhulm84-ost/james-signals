import os
import subprocess

# Read C:\Trading\.env
env_vars = {}
with open(r"C:\Trading\.env", "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            parts = line.split("=", 1)
            if len(parts) == 2:
                env_vars[parts[0]] = parts[1]

# Put ANTHROPIC_API_KEY in the environment
os.environ["ANTHROPIC_API_KEY"] = env_vars.get("ANTHROPIC_API_KEY", "")

# Run hermes chat command
cmd = [
    "C:\\Users\\passm\\AppData\\Local\\hermes\\bin\\hermes.cmd", "chat",
    "-m", "anthropic/claude-3-5-sonnet-20241022",
    "-q", "respond with only the word 'success'",
    "--provider", "anthropic",
    "--oneshot"
]

print("Running hermes with anthropic...")
r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
print("RC:", r.returncode)
print("STDOUT:", r.stdout)
print("STDERR:", r.stderr)
