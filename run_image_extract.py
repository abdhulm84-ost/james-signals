import json
from pathlib import Path
from james_images import extract_image_fields

with open("config.json", "r", encoding="utf-8") as f:
    cfg = json.load(f)

photo_path = r"C:\Trading\james-signals\photos\4130.jpg"
print(f"Checking if photo {photo_path} exists: {Path(photo_path).exists()}")
if Path(photo_path).exists():
    print(f"Size: {Path(photo_path).stat().st_size} bytes")

res = extract_image_fields(photo_path, cfg)
print("RESULT_START")
print(json.dumps(res, indent=2))
print("RESULT_END")
