import os
import glob
from PIL import Image

temp_dir = r"C:\Users\passm\AppData\Local\Temp"
files_sorted = sorted(glob.glob(os.path.join(temp_dir, "*")), key=os.path.getmtime, reverse=True)
for f in files_sorted[:15]:
    size = os.path.getsize(f)
    print(f"{f}: {size} bytes")
    try:
        with Image.open(f) as img:
            print(f"  -> Valid Image: {img.format}, size: {img.size}")
    except Exception:
        pass
