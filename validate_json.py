import json
import sys

with open('/c/Trading/james-signals/parsed_output.json', 'r') as f:
    data = json.load(f)

print("Valid JSON:")
print(json.dumps(data, indent=2))

# Verify all required keys present
CARD_KEYS = ("instrument", "direction", "action", "entry", "stop", "targets",
             "confidence", "horizon", "option", "trim", "spread", "rationale")
ACTIONS = ("entry", "exit", "update", "none")

errors = []
if data.get("direction") not in ("long", "short", "flat"):
    errors.append(f"bad direction: {data.get('direction')}")
if data.get("action") not in ACTIONS:
    errors.append(f"bad action: {data.get('action')}")
if not isinstance(data.get("targets"), list):
    errors.append("targets not a list")

if errors:
    print("ERRORS:", errors)
    sys.exit(1)
else:
    print("VALIDATION: OK")