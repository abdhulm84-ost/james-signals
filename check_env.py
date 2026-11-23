import os
for k, v in os.environ.items():
    if any(x in k.lower() for x in ['api', 'key', 'token', 'secret']):
        print(f"{k}: SET")
