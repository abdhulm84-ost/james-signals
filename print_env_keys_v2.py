import os
print("ENVIRONMENT KEYS:")
for k, v in os.environ.items():
    if any(x in k.upper() for x in ["KEY", "SECRET", "TOKEN", "API", "PASS"]):
        print(f"  {k}: {'[SET]' if v else '[EMPTY]'}")

for env_path in [r"C:\Trading\james-signals\.env", r"C:\Trading\.env"]:
    print(f"\nChecking {env_path}: exists = {os.path.exists(env_path)}")
    if os.path.exists(env_path):
        print(f"FILE KEYS FROM {env_path}:")
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split("=", 1)
                    print(f"  {parts[0]}: [SET]" if len(parts) > 1 else f"  {line}: [NO_VALUE]")
