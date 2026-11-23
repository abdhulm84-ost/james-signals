import os
import glob

temp_dir = r"C:\Users\passm\AppData\Local\Temp"
pattern = os.path.join(temp_dir, "tmp*")
files = glob.glob(pattern)
files = [f for f in files if os.path.isfile(f)]
files.sort(key=lambda x: os.path.getmtime(x), reverse=True)

print("Top 20 temp files sorted by modification time:")
for f in files[:20]:
    print(f, os.path.getsize(f), os.path.getmtime(f))
