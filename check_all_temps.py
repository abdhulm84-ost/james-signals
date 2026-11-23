import os, glob, time

temp_dir = r"C:\Users\passm\AppData\Local\Temp"
now = time.time()
one_day = 24 * 3600

files = glob.glob(os.path.join(temp_dir, "*"))
recent_files = []
for f in files:
    try:
        mtime = os.path.getmtime(f)
        if now - mtime < one_day:
            recent_files.append((f, os.path.getsize(f), mtime))
    except:
        pass

recent_files.sort(key=lambda x: x[2], reverse=True)
print("Files modified in the last 24 hours in Temp:")
for f, s, mt in recent_files[:40]:
    print(f, s, time.ctime(mt))
