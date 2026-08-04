from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from aiogram.types import LabeledPrice
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services as services_module
from app.db import get_session
from app.models import StorePayment
from app.pricing import (
    ACCOUNT_PREMIUM_PLANS,
    GROUP_PREMIUM_PLANS,
    PremiumPlan,
    get_plan,
)
from app.security import TelegramUser, current_telegram_user
from app.services import (
    entity_premium_details,
    set_entity_premium,
    upsert_user,
    utcnow,
)


router = APIRouter(prefix="/api")
ACCOUNT_PAYMENT_CHAT_ID = 0


class PremiumInvoiceRequest(BaseModel):
    plan_code: str


@dataclass(slots=True)
class AccountPremiumReceipt:
    """Object compatible with the existing successful-payment message."""

    title: str
    premium_until: datetime


def _plan_payload(plan: PremiumPlan) -> dict[str, Any]:
    return {
        "code": plan.code,
        "title": plan.title,
        "days": plan.days,
        "months": plan.months,
        "stars": plan.stars,
        "badge": plan.badge,
        "description": plan.description,
        "scope": plan.scope,
        "discount_percent": plan.discount_percent,
    }


@router.get("/premium/purchase-plans")
async def premium_purchase_plans(
    user: TelegramUser = Depends(current_telegram_user),
) -> dict[str, Any]:
    del user
    return {
        "account": [_plan_payload(plan) for plan in ACCOUNT_PREMIUM_PLANS.values()],
        "group": [_plan_payload(plan) for plan in GROUP_PREMIUM_PLANS.values()],
        "inheritance": {
            "enabled": True,
            "description": (
                "Premium аккаунта автоматически действует во всех беседах, "
                "где владелец аккаунта является создателем."
            ),
        },
    }


@router.get("/premium/account/status")
async def account_premium_status(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await upsert_user(session, user)
    details = await entity_premium_details(session, "user", user.id)
    await session.commit()
    return {
        "active": bool(details["active"]),
        "until": details["until"],
        "plan": details["plan"],
        "lifetime": bool(details["lifetime"]),
        "scope": "account",
    }


@router.post("/premium/account/invoice")
async def account_premium_invoice(
    payload: PremiumInvoiceRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        plan = get_plan(payload.plan_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Неизвестный тариф Premium") from exc

    if plan.scope != "account":
        raise HTTPException(status_code=400, detail="Выбран не тариф аккаунта")

    await upsert_user(session, user)
    await session.commit()

    # Импорт выполняется только при запросе, после полной загрузки app.bot.
    from app.bot import bot

    nonce = secrets.token_hex(5)
    invoice_payload = (
        f"agp:{ACCOUNT_PAYMENT_CHAT_ID}:{user.id}:{plan.code}:{nonce}"
    )
    invoice_url = await bot.create_invoice_link(
        title=f"AniGuard Premium аккаунта — {plan.title}",
        description=plan.description,
        payload=invoice_payload,
        currency="XTR",
        prices=[
            LabeledPrice(
                label=f"Premium аккаунта: {plan.months} мес.",
                amount=plan.stars,
            )
        ],
        provider_token="",
    )
    return {
        "invoice_url": invoice_url,
        "payload": invoice_payload,
        "scope": "account",
        "plan": _plan_payload(plan),
    }


# ---------------------------------------------------------------------------
# Совместимость с действующим обработчиком Telegram Stars в app.bot.
# Счёт аккаунта использует существующий формат agp, но chat_id=0.
# Важно: app.main импортирует этот модуль ДО app.api/app.bot.
# ---------------------------------------------------------------------------

RequireChatAdmin = Callable[[Any, int, int], Awaitable[None]]
GrantPremium = Callable[..., Awaitable[Any]]
CreateInvoiceLink = Callable[..., Awaitable[tuple[str, str]]]


if not hasattr(services_module, "_premium_account_original_require_chat_admin"):
    services_module._premium_account_original_require_chat_admin = (  # type: ignore[attr-defined]
        services_module.require_chat_admin
    )
    services_module._premium_account_original_grant_premium = (  # type: ignore[attr-defined]
        services_module.grant_premium
    )
    services_module._premium_account_original_create_invoice_link = (  # type: ignore[attr-defined]
        services_module.create_invoice_link
    )


_original_require_chat_admin: RequireChatAdmin = (
    services_module._premium_account_original_require_chat_admin  # type: ignore[attr-defined]
)
_original_grant_premium: GrantPremium = (
    services_module._premium_account_original_grant_premium  # type: ignore[attr-defined]
)
_original_create_invoice_link: CreateInvoiceLink = (
    services_module._premium_account_original_create_invoice_link  # type: ignore[attr-defined]
)


async def require_chat_admin_with_account(
    bot: Any,
    chat_id: int,
    user_id: int,
) -> None:
    if int(chat_id) == ACCOUNT_PAYMENT_CHAT_ID:
        if int(user_id) <= 0:
            raise PermissionError("Некорректный пользователь Premium")
        return
    await _original_require_chat_admin(bot, chat_id, user_id)


async def create_invoice_link_with_scope(
    bot: Any,
    *,
    user_id: int,
    chat_id: int,
    plan_code: str,
) -> tuple[str, str]:
    plan = get_plan(plan_code)
    if int(chat_id) == ACCOUNT_PAYMENT_CHAT_ID:
        if plan.scope != "account":
            raise ValueError("Для аккаунта нужен тариф аккаунта")
    elif plan.scope != "group":
        raise ValueError("Для беседы нужен тариф группы")

    return await _original_create_invoice_link(
        bot,
        user_id=user_id,
        chat_id=chat_id,
        plan_code=plan.code,
    )


async def _existing_account_receipt(
    session: AsyncSession,
    user_id: int,
) -> AccountPremiumReceipt:
    details = await entity_premium_details(session, "user", user_id)
    premium_until = details.get("until")
    if not isinstance(premium_until, datetime):
        raise ValueError("Платёж уже обработан, но срок Premium не найден")
    return AccountPremiumReceipt(
        title="Premium аккаунта",
        premium_until=premium_until,
    )


async def grant_premium_with_account(
    session: AsyncSession,
    *,
    user_id: int,
    chat_id: int,
    plan_code: str,
    stars: int,
    payload: str,
    charge_id: str,
) -> Any:
    plan = get_plan(plan_code)

    if int(chat_id) != ACCOUNT_PAYMENT_CHAT_ID:
        if plan.scope != "group":
            raise ValueError("Тариф аккаунта нельзя активировать для одной беседы")
        return await _original_grant_premium(
            session,
            user_id=user_id,
            chat_id=chat_id,
            plan_code=plan.code,
            stars=stars,
            payload=payload,
            charge_id=charge_id,
        )

    if plan.scope != "account":
        raise ValueError("Для аккаунта выбран неверный тариф")
    if plan.stars != int(stars):
        raise ValueError("Сумма платежа не совпадает с тарифом аккаунта")

    existing = await session.scalar(
        select(StorePayment).where(
            or_(
                StorePayment.telegram_payment_charge_id == charge_id,
                StorePayment.invoice_payload == payload,
            )
        )
    )
    if existing and existing.status == "paid":
        return await _existing_account_receipt(session, user_id)

    # Пользователь создаётся при формировании счёта, но повторный upsert
    # делает обработчик устойчивым к ручным/старым ссылкам на оплату.
    payment_user = SimpleNamespace(
        id=user_id,
        username=None,
        first_name="User",
        last_name=None,
    )
    await upsert_user(session, payment_user)

    grant = await set_entity_premium(
        session,
        entity_type="user",
        entity_id=user_id,
        days=plan.days,
        admin_id=user_id,
        permanent=False,
        plan=plan.code,
        note="Покупка Premium аккаунта через Telegram Stars",
    )

    if existing is None:
        existing = StorePayment(
            user_id=user_id,
            kind="premium_user",
            reference_id=None,
            coins=plan.days,
            stars=stars,
            invoice_payload=payload,
            telegram_payment_charge_id=charge_id,
            status="paid",
            paid_at=utcnow(),
        )
        session.add(existing)
    else:
        existing.kind = "premium_user"
        existing.coins = plan.days
        existing.stars = stars
        existing.telegram_payment_charge_id = charge_id
        existing.status = "paid"
        existing.paid_at = utcnow()

    await session.flush()
    premium_until = grant.premium_until
    if not isinstance(premium_until, datetime):
        raise ValueError("Не удалось определить срок Premium аккаунта")

    return AccountPremiumReceipt(
        title="Premium аккаунта",
        premium_until=premium_until,
    )


services_module.require_chat_admin = require_chat_admin_with_account
services_module.create_invoice_link = create_invoice_link_with_scope
services_module.grant_premium = grant_premium_with_account
