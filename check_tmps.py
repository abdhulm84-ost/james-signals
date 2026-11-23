import os
import glob
import datetime

temp_dir = r"C:\Users\passm\AppData\Local\Temp"
pattern = os.path.join(temp_dir, "tmp*")
files = glob.glob(pattern)

print("All temp files starting with tmp:")
for f in files:
    size = os.path.getsize(f)
    mtime = os.path.getmtime(f)
    dt = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
    if mtime > (datetime.datetime.now() - datetime.timedelta(minutes=15)).timestamp():
        print(f"{f}: {size} bytes, modified {dt}")
