import os
import shutil
from PIL import Image

temp_dir = r"C:\Users\passm\AppData\Local\Temp"
scratch_dir = r"C:\Trading\james-signals\__pycache__" # use pycache as a scratch spot

files = [
    "0807930f-6ea1-4966-92a2-fe9cc524c0c5.tmp",
    "11fc5aec-c1bf-4599-8851-83aa15230855.tmp",
    "3d33126d-f29c-44af-9831-ec5a0a253f49.tmp",
    "6983bf45-46ed-45c0-a47a-7d3954703641.tmp",
    "87688bf2-b551-4800-8604-6e3a06fcaa08.tmp"
]

for name in files:
    src = os.path.join(temp_dir, name)
    dst = os.path.join(scratch_dir, name + ".png") # try png
    print(f"Trying to copy {name}...")
    try:
        shutil.copyfile(src, dst)
        print(f"  Copied to {dst}")
        # try opening
        with Image.open(dst) as img:
            print(f"  -> Success: Image format {img.format}, size {img.size}")
    except Exception as e:
        print(f"  Failed: {e}")
