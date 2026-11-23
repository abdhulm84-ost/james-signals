import os
import glob
import time

all_files = []

# Search in C:\Trading\james-signals\photos\
for root, dirs, files in os.walk(r"C:\Trading\james-signals\photos"):
    for f in files:
        if f.lower().endswith(('.png', '.jpg', '.jpeg')):
            path = os.path.join(root, f)
            try:
                all_files.append((path, os.path.getsize(path), os.path.getmtime(path)))
            except:
                pass

# Search in C:\Users\passm\AppData\Local\Temp\
for root, dirs, files in os.walk(r"C:\Users\passm\AppData\Local\Temp"):
    for f in files:
        if f.lower().endswith(('.png', '.jpg', '.jpeg')):
            path = os.path.join(root, f)
            try:
                all_files.append((path, os.path.getsize(path), os.path.getmtime(path)))
            except:
                pass

all_files.sort(key=lambda x: x[2], reverse=True)

print("Most recent files overall:")
for p, s, mt in all_files[:10]:
    print(f"Path: {p}, Size: {s}, Mtime: {mt}, Formatted time: {time.ctime(mt)}")
