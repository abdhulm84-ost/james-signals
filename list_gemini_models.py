import os
import urllib.request
import json

# Read C:\Trading\.env
env_vars = {}
with open(r"C:\Trading\.env", "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            parts = line.split("=", 1)
            if len(parts) == 2:
                env_vars[parts[0]] = parts[1]

key = env_vars.get("GOOGLE_API_KEY", "")
print("GOOGLE_API_KEY present:", bool(key))

url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
try:
    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read().decode())
        print("Supported Models:")
        for m in data.get("models", []):
            name = m.get("name", "")
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" in methods:
                print(f"  {name} ( {m.get('displayName', '')} )")
except Exception as e:
    print("Error:", e)
