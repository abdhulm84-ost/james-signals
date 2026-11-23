import os
import glob

temp_dir = r"C:\Users\passm\AppData\Local\Temp"
pattern = os.path.join(temp_dir, "*")
files = glob.glob(pattern)

# filter for image files that are not 0 or 1 byte
valid_files = []
for f in files:
    if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
        size = os.path.getsize(f)
        if size > 10:
            valid_files.append((f, size, os.path.getmtime(f)))

valid_files.sort(key=lambda x: x[2], reverse=True)

print("Recent valid images:")
for f, size, mtime in valid_files[:15]:
    import datetime
    dt = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
    print(f"{f}: {size} bytes, modified {dt}")
