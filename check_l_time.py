import os, time
p = r"C:\Users\passm\AppData\Local\Temp\tmplzp90pdt.jpg"
if os.path.exists(p):
    print("Mtime:", os.path.getmtime(p), "Formatted:", time.ctime(os.path.getmtime(p)))
else:
    print("Does not exist")
