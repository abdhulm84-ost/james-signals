import os
import glob
import time

# Let's search inside C:\Trading for any jpg/png files modified in the last 24 hours.
now = time.time()
one_day = 24 * 3600

print("Files modified in the last 24 hours in C:\\Trading:")
for root, dirs, files in os.walk("C:\\Trading"):
    for file in files:
        if file.lower().endswith(('.jpg', '.png', '.jpeg')):
            path = os.path.join(root, file)
            try:
                mtime = os.path.getmtime(path)
                if now - mtime < one_day:
                    print(path, os.path.getsize(path), mtime)
            except Exception as e:
                pass

print("\nFiles modified in the last 24 hours in Temp folder:")
for root, dirs, files in os.walk(r"C:\Users\passm\AppData\Local\Temp"):
    for file in files:
        if file.lower().endswith(('.jpg', '.png', '.jpeg')):
            path = os.path.join(root, file)
            try:
                mtime = os.path.getmtime(path)
                if now - mtime < one_day:
                    print(path, os.path.getsize(path), mtime)
            except Exception as e:
                pass
