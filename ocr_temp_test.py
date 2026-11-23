from PIL import Image
import pytesseract

try:
    text = pytesseract.image_to_string(Image.open('C:/Trading/james-signals/photos/4130.jpg'))
    print('SUCCESS_OCR_START')
    print(text)
    print('SUCCESS_OCR_END')
except Exception as e:
    print('ERROR:', e)
