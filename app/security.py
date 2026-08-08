import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, status

from app.config import get_settings


@dataclass(slots=True)
class TelegramUser:
    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


def validate_init_data(init_data: str, bot_token: str, max_age: int = 3600) -> TelegramUser:
    if not init_data:
        raise ValueError("Empty Telegram initData")

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", None)
    if not received_hash:
        raise ValueError("initData hash is missing")

    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("Invalid Telegram initData signature")

    auth_date = int(values.get("auth_date", "0"))
    now = int(time.time())
    if auth_date <= 0 or now - auth_date > max_age or auth_date > now + 30:
        raise ValueError("Telegram initData has expired")

    user_raw = values.get("user")
    if not user_raw:
        raise ValueError("Telegram user is missing")
    user = json.loads(user_raw)
    return TelegramUser(
        id=int(user["id"]),
        first_name=user.get("first_name", "User"),
        last_name=user.get("last_name"),
        username=user.get("username"),
        language_code=user.get("language_code"),
    )


async def current_telegram_user(
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
    x_dev_user_id: int | None = Header(default=None, alias="X-Dev-User-Id"),
) -> TelegramUser:
    settings = get_settings()
    if settings.dev_mode and x_dev_user_id:
        return TelegramUser(id=x_dev_user_id, first_name="Dev", username="dev")
    try:
        return validate_init_data(
            x_telegram_init_data or "",
            settings.bot_token,
            settings.init_data_max_age,
        )
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
