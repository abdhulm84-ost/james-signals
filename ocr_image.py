#!/usr/bin/env python
import sys
sys.stdout.reconfigure(encoding='utf-8')

from PIL import Image
import pytesseract

img = Image.open('C:/Trading/james-signals/photos/4007.jpg')
text = pytesseract.image_to_string(img)
print(text)