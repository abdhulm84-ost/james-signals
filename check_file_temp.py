import os
import mimetypes

path = r"C:\Users\passm\AppData\Local\Temp\tmplzp90pdt.jpg"
print("Exists:", os.path.exists(path))
if os.path.exists(path):
    print("Size:", os.path.getsize(path))
    with open(path, "rb") as f:
        header = f.read(32)
        print("Header:", header)
