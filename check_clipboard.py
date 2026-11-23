import os
import glob
import datetime

temp_dir = r"C:\Users\passm\AppData\Local\Temp"
pattern = os.path.join(temp_dir, "codex-clipboard-*")
files = glob.glob(pattern)

print("Recent codex-clipboard files:")
for f in files:
    size = os.path.getsize(f)
    mtime = os.path.getmtime(f)
    dt = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
    print(f"{f}: {size} bytes, modified {dt}")
