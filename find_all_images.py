import os
import glob

temp_dir = r"C:\Users\passm\AppData\Local\Temp"
print("Searching Temp for jpg/png:")
for f in glob.glob(os.path.join(temp_dir, "*.jpg")):
    print(f, os.path.getsize(f))
for f in glob.glob(os.path.join(temp_dir, "*.png")):
    print(f, os.path.getsize(f))

print("\nSearching current dir for jpg/png:")
for f in glob.glob("*.jpg"):
    print(f, os.path.getsize(f))
for f in glob.glob("*.png"):
    print(f, os.path.getsize(f))
