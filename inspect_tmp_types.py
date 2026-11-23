import os
import glob

temp_dir = r"C:\Users\passm\AppData\Local\Temp"
pattern = os.path.join(temp_dir, "*.tmp")
files = glob.glob(pattern)

print("Recent large .tmp files:")
for f in sorted(files, key=os.path.getmtime, reverse=True)[:5]:
    size = os.path.getsize(f)
    mtime = os.path.getmtime(f)
    import datetime
    dt = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
    print(f"{f}: {size} bytes, modified {dt}")
    # read first few bytes
    try:
        with open(f, 'rb') as file:
            header = file.read(20)
            print("  Header (hex):", header.hex())
            print("  Header (ASCII):", ''.join(chr(b) if 32 <= b < 127 else '.' for b in header))
    except Exception as e:
        print("  Error reading:", e)
