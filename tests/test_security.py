import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from app.security import validate_init_data


def build_init_data(token: str) -> str:
    values = {
        "auth_date": str(int(time.time())),
        "query_id": "test-query",
        "user": json.dumps({"id": 42, "first_name": "Naruto", "username": "naruto"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_valid_init_data():
    token = "123:TEST"
    user = validate_init_data(build_init_data(token), token)
    assert user.id == 42
    assert user.username == "naruto"
