import math
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import json

obj = {"a": float("nan"), "b": 1}
encoded = jsonable_encoder(obj)
print("jsonable_encoder output:", encoded)

try:
    resp = JSONResponse(content=encoded)
    print("JSONResponse output:", resp.body.decode('utf-8'))
except Exception as e:
    print("JSONResponse Exception:", e)

