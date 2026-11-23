import os

paths = [
    r"C:\Users\passm\.env",
    r"C:\Users\passm\AppData\Local\hermes\.env",
    r"C:\Users\passm\AppData\Local\hermes\profiles\default\.env"
]

for p in paths:
    print(f"Path {p}: exists = {os.path.exists(p)}")
    if os.path.exists(p):
        print(f"Keys:")
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split("=", 1)
                    print(f"  {parts[0]}: [SET]" if len(parts) > 1 else f"  {line}: [NO_VALUE]")
