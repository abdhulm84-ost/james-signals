import os
import glob

photos_dir = r"C:\Trading\james-signals\photos"
print("Photos matching 3551 or similar:")
for f in glob.glob(os.path.join(photos_dir, "*3551*")):
    print(f, os.path.getsize(f))
for f in glob.glob(os.path.join(photos_dir, "*3552*")):
    print(f, os.path.getsize(f))
for f in glob.glob(os.path.join(photos_dir, "*3593*")):
    print(f, os.path.getsize(f))

# Let's see the files around that number
print("\nFiles in photos:")
for f in sorted(glob.glob(os.path.join(photos_dir, "*"))):
    name = os.path.basename(f)
    if "3551" in name or "3552" in name or "3593" in name or "355" in name:
        print(name, os.path.getsize(f))
