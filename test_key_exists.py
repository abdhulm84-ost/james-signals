import os

# Read C:\Trading\.env
env_vars = {}
with open(r"C:\Trading\.env", "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            parts = line.split("=", 1)
            if len(parts) == 2:
                env_vars[parts[0]] = parts[1]

key = env_vars.get("ANTHROPIC_API_KEY", "")
print("Key length:", len(key))
print("Key starts with:", key[:5] if key else "None")
