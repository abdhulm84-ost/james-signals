import os
from PIL import Image

f = r"C:\Trading\james-signals\test_temp_web.png"
print("Exists:", os.path.exists(f))
if os.path.exists(f):
    print("Size:", os.path.getsize(f))
    with Image.open(f) as img:
        print("Format:", img.format)
        print("Size (W, H):", img.size)
