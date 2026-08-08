from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any, Literal

from aiogram.types import LabeledPrice
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import (
    AdvertisingOrder,
    AdminActionLog,
    CaseOpening,
    EntityAccessGrant,
    Membership,
    StorePayment,
    SupportMessage,
    SupportTicket,
    SystemSetting,
    User,
    UserWallet,
    WalletTransaction,
)
from app.security import TelegramUser, current_telegram_user
from app.services import as_utc, upsert_user, utcnow
from app.store_rules import (
    AD_LABELS,
    AD_RATES,
    ANICOIN_PER_STAR,
    PREMIUM_CASE_COST,
    PREMIUM_CASE_WEIGHTS,
    advertising_price,
    coin_stars,
)

router = APIRouter(prefix="/api")
settings = get_settings()


class CoinInvoiceRequest(BaseModel):
    amount: int = Field(ge=100, le=100_000_000)
    custom: bool = False


class AdvertisingOrderCreate(BaseModel):
    placement: Literal["channel", "bot", "rewarded"]
    audience_count: int = Field(ge=1, le=10_000_000)
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=15, max_length=1200)
    url: str | None = Field(default=None, max_length=500)


class AdvertisingModerationRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=1000)


class AdvertisingSettingsRequest(BaseModel):
    channel_live_users: int = Field(ge=0, le=100_000_000)


class SupportMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class SupportStatusRequest(BaseModel):
    status: Literal["open", "in_progress", "closed"]


def ensure_bot_admin(user: TelegramUser) -> None:
    if user.id not in settings.admin_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Раздел доступен только владельцу AniGuard")


async def _admin_log(
    session: AsyncSession,
    *,
    admin_id: int,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AdminActionLog(
            admin_id=admin_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )


async def _wallet(session: AsyncSession, user_id: int) -> UserWallet:
    wallet = await session.get(UserWallet, user_id)
    if wallet is None:
        wallet = UserWallet(user_id=user_id, balance=0)
        session.add(wallet)
        await session.flush()
    return wallet


async def _advertising_values(session: AsyncSession) -> dict[str, int]:
    week_ago = utcnow() - timedelta(days=7)
    bot_live = int(
        await session.scalar(
            select(func.count(func.distinct(Membership.user_id))).where(Membership.last_seen_at >= week_ago)
        )
        or 0
    )
    row = await session.get(SystemSetting, "advertising")
    stored = row.value if row and isinstance(row.value, dict) else {}
    channel_live = int(stored.get("channel_live_users", bot_live))
    return {
        "channel": max(0, channel_live),
        "bot": max(0, bot_live),
        "rewarded": max(0, bot_live),
    }


def _serialize_ad(row: AdvertisingOrder) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "placement": row.placement,
        "placement_label": AD_LABELS.get(row.placement, row.placement),
        "audience_count": row.audience_count,
        "stars_per_user": row.stars_per_user,
        "total_stars": row.total_stars,
        "title": row.title,
        "text": row.text,
        "url": row.url,
        "status": row.status,
        "moderation_note": row.moderation_note,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "paid_at": row.paid_at.isoformat() if row.paid_at else None,
        "created_at": row.created_at.isoformat(),
    }


def _serialize_support_message(row: SupportMessage) -> dict[str, Any]:
    return {
        "id": row.id,
        "ticket_id": row.ticket_id,
        "sender_type": row.sender_type,
        "sender_id": row.sender_id,
        "text": row.text,
        "created_at": row.created_at.isoformat(),
    }


async def _active_ticket(session: AsyncSession, user_id: int, *, create: bool) -> SupportTicket | None:
    ticket = await session.scalar(
        select(SupportTicket)
        .where(SupportTicket.user_id == user_id, SupportTicket.status != "closed")
        .order_by(SupportTicket.id.desc())
        .limit(1)
    )
    if ticket is None and create:
        ticket = SupportTicket(user_id=user_id, status="open")
        session.add(ticket)
        await session.flush()
    return ticket


def _pick_case_reward() -> dict[str, Any]:
    roll = secrets.randbelow(1_000_000)
    cumulative = 0
    for reward_type, weight, value, label in PREMIUM_CASE_WEIGHTS:
        cumulative += weight
        if roll < cumulative:
            if reward_type == "coins":
                amount = 500 + secrets.randbelow(5_501)
                return {
                    "reward_type": "coins",
                    "reward_value": amount,
                    "reward_label": f"{amount:,} AniCoin".replace(",", " "),
                }
            return {
                "reward_type": "premium",
                "reward_value": value,
                "reward_label": label,
            }
    raise RuntimeError("Некорректная таблица наград")


async def _grant_user_premium_seconds(
    session: AsyncSession,
    *,
    user_id: int,
    seconds: int,
    plan: str,
) -> None:
    grant = await session.scalar(
        select(EntityAccessGrant).where(
            EntityAccessGrant.entity_type == "user",
            EntityAccessGrant.entity_id == user_id,
        )
    )
    now = utcnow()
    if grant is None:
        grant = EntityAccessGrant(
            entity_type="user",
            entity_id=user_id,
            premium_until=now + timedelta(seconds=seconds),
            premium_plan=plan,
            is_lifetime=False,
            granted_by=0,
            note="Награда Premium кейса",
        )
        session.add(grant)
    elif not grant.is_lifetime:
        current = as_utc(grant.premium_until)
        start = current if current and current > now else now
        grant.premium_until = start + timedelta(seconds=seconds)
        grant.premium_plan = plan
        grant.note = "Награда Premium кейса"


@router.get("/store/wallet")
async def store_wallet(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await upsert_user(session, user)
    wallet = await _wallet(session, user.id)
    await session.commit()
    return {"balance": int(wallet.balance), "rate": {"coins": ANICOIN_PER_STAR, "stars": 1}}


@router.post("/store/coins/invoice")
async def create_coin_invoice(
    payload: CoinInvoiceRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    amount = int(payload.amount)
    try:
        stars = coin_stars(amount, custom=payload.custom)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await upsert_user(session, user)
    payment = StorePayment(
        user_id=user.id,
        kind="coins",
        coins=amount,
        stars=stars,
        invoice_payload=f"pending:{secrets.token_hex(8)}",
        status="created",
    )
    session.add(payment)
    await session.flush()
    nonce = secrets.token_hex(5)
    invoice_payload = f"agc:{user.id}:{amount}:{payment.id}:{nonce}"
    payment.invoice_payload = invoice_payload
    from app.bot import bot

    invoice_url = await bot.create_invoice_link(
        title="AniGuard AniCoin",
        description=f"Пополнение баланса на {amount:,} AniCoin".replace(",", " "),
        payload=invoice_payload,
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount:,} AniCoin".replace(",", " "), amount=stars)],
        provider_token="",
    )
    await session.commit()
    return {"invoice_url": invoice_url, "invoice_payload": invoice_payload, "coins": amount, "stars": stars}


@router.post("/store/cases/premium/open")
async def open_premium_case(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    request_id = (idempotency_key or secrets.token_hex(16))[:64]
    existing = await session.scalar(select(CaseOpening).where(CaseOpening.request_id == request_id))
    if existing:
        wallet = await _wallet(session, user.id)
        return {
            "opening_id": existing.id,
            "request_id": existing.request_id,
            "cost": existing.cost,
            "reward_type": existing.reward_type,
            "reward_value": existing.reward_value,
            "reward_label": existing.reward_label,
            "balance": wallet.balance,
            "replayed": True,
        }

    await upsert_user(session, user)
    wallet = await _wallet(session, user.id)
    if wallet.balance < PREMIUM_CASE_COST:
        raise HTTPException(status_code=400, detail="Недостаточно AniCoin для открытия Premium кейса")

    reward = _pick_case_reward()
    wallet.balance -= PREMIUM_CASE_COST
    if reward["reward_type"] == "coins":
        wallet.balance += int(reward["reward_value"])
    else:
        await _grant_user_premium_seconds(
            session,
            user_id=user.id,
            seconds=int(reward["reward_value"]),
            plan="premium_case",
        )

    opening = CaseOpening(
        request_id=request_id,
        user_id=user.id,
        case_code="premium_case",
        cost=PREMIUM_CASE_COST,
        reward_type=reward["reward_type"],
        reward_value=int(reward["reward_value"]),
        reward_label=reward["reward_label"],
    )
    session.add(opening)
    session.add(
        WalletTransaction(
            user_id=user.id,
            kind="case_open",
            amount=-PREMIUM_CASE_COST,
            balance_after=wallet.balance,
            reference=request_id,
            details={"case": "premium_case", **reward},
        )
    )
    await session.flush()
    await session.commit()
    return {
        "opening_id": opening.id,
        "request_id": request_id,
        "cost": PREMIUM_CASE_COST,
        **reward,
        "balance": int(wallet.balance),
        "replayed": False,
    }


@router.get("/store/cases/history")
async def case_history(
    limit: int = Query(default=50, ge=1, le=200),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(CaseOpening)
            .where(CaseOpening.user_id == user.id)
            .order_by(CaseOpening.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": row.id,
            "case_code": row.case_code,
            "cost": row.cost,
            "reward_type": row.reward_type,
            "reward_value": row.reward_value,
            "reward_label": row.reward_label,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/store/audience")
async def advertising_audience(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    values = await _advertising_values(session)
    return {
        "live": values,
        "rates": AD_RATES,
        "labels": AD_LABELS,
    }


@router.get("/store/ads")
async def list_ad_orders(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(AdvertisingOrder)
            .where(AdvertisingOrder.user_id == user.id)
            .order_by(AdvertisingOrder.id.desc())
        )
    ).all()
    return [_serialize_ad(row) for row in rows]


@router.post("/store/ads", status_code=201)
async def create_ad_order(
    payload: AdvertisingOrderCreate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await upsert_user(session, user)
    values = await _advertising_values(session)
    available = values[payload.placement]
    if payload.audience_count > available:
        raise HTTPException(
            status_code=400,
            detail=f"Для выбранного формата доступно {available} живых пользователей за неделю",
        )
    rate = AD_RATES[payload.placement]
    total_stars = advertising_price(payload.placement, payload.audience_count)
    row = AdvertisingOrder(
        user_id=user.id,
        placement=payload.placement,
        audience_count=payload.audience_count,
        stars_per_user=rate,
        total_stars=total_stars,
        title=payload.title.strip(),
        text=payload.text.strip(),
        url=payload.url.strip() if payload.url else None,
        status="pending",
        moderation_note="Заказ отправлен админу на модерацию",
    )
    session.add(row)
    await session.flush()
    await session.commit()
    return _serialize_ad(row)


@router.post("/store/ads/{order_id}/invoice")
async def create_ad_invoice(
    order_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await session.get(AdvertisingOrder, order_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Рекламный заказ не найден")
    if row.status != "approved":
        raise HTTPException(status_code=400, detail="Оплата доступна только после одобрения рекламы")
    existing = await session.scalar(
        select(StorePayment).where(
            StorePayment.kind == "advertising",
            StorePayment.reference_id == row.id,
            StorePayment.status == "created",
        )
    )
    if existing is None:
        existing = StorePayment(
            user_id=user.id,
            kind="advertising",
            reference_id=row.id,
            coins=0,
            stars=row.total_stars,
            invoice_payload=f"pending:{secrets.token_hex(8)}",
            status="created",
        )
        session.add(existing)
        await session.flush()
        existing.invoice_payload = f"aga:{user.id}:{row.id}:{existing.id}:{secrets.token_hex(5)}"
    from app.bot import bot

    invoice_url = await bot.create_invoice_link(
        title=f"Реклама AniGuard #{row.id}",
        description=f"{AD_LABELS[row.placement]} · {row.audience_count} пользователей",
        payload=existing.invoice_payload,
        currency="XTR",
        prices=[LabeledPrice(label="Рекламное размещение", amount=row.total_stars)],
        provider_token="",
    )
    await session.commit()
    return {"invoice_url": invoice_url, "stars": row.total_stars, "order_id": row.id}


@router.get("/support/thread")
async def support_thread(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ticket = await _active_ticket(session, user.id, create=False)
    if ticket is None:
        return {"ticket": None, "messages": []}
    messages = (
        await session.scalars(
            select(SupportMessage)
            .where(SupportMessage.ticket_id == ticket.id)
            .order_by(SupportMessage.id.asc())
        )
    ).all()
    return {
        "ticket": {
            "id": ticket.id,
            "status": ticket.status,
            "subject": ticket.subject,
            "created_at": ticket.created_at.isoformat(),
            "updated_at": ticket.updated_at.isoformat(),
        },
        "messages": [_serialize_support_message(row) for row in messages],
    }


@router.post("/support/messages", status_code=201)
async def send_support_message(
    payload: SupportMessageRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await upsert_user(session, user)
    ticket = await _active_ticket(session, user.id, create=True)
    assert ticket is not None
    ticket.status = "open"
    ticket.updated_at = utcnow()
    message = SupportMessage(
        ticket_id=ticket.id,
        sender_type="user",
        sender_id=user.id,
        text=payload.text.strip(),
    )
    session.add(message)
    await session.flush()
    await session.commit()
    return _serialize_support_message(message)


@router.get("/admin/store/overview")
async def admin_store_overview(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    pending_ads = int(
        await session.scalar(
            select(func.count()).select_from(AdvertisingOrder).where(AdvertisingOrder.status == "pending")
        )
        or 0
    )
    open_tickets = int(
        await session.scalar(
            select(func.count()).select_from(SupportTicket).where(SupportTicket.status != "closed")
        )
        or 0
    )
    case_count = int(await session.scalar(select(func.count()).select_from(CaseOpening)) or 0)
    coin_sales = int(
        await session.scalar(
            select(func.coalesce(func.sum(StorePayment.stars), 0)).where(
                StorePayment.kind == "coins", StorePayment.status == "paid"
            )
        )
        or 0
    )
    ad_sales = int(
        await session.scalar(
            select(func.coalesce(func.sum(StorePayment.stars), 0)).where(
                StorePayment.kind == "advertising", StorePayment.status == "paid"
            )
        )
        or 0
    )
    return {
        "pending_ads": pending_ads,
        "open_tickets": open_tickets,
        "case_openings": case_count,
        "coin_sales_stars": coin_sales,
        "ad_sales_stars": ad_sales,
        "audience": await _advertising_values(session),
    }


@router.get("/admin/store/ads")
async def admin_ad_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=1000),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    ensure_bot_admin(user)
    query = select(AdvertisingOrder).order_by(AdvertisingOrder.id.desc()).limit(limit)
    if status_filter:
        query = query.where(AdvertisingOrder.status == status_filter)
    rows = (await session.scalars(query)).all()
    return [_serialize_ad(row) for row in rows]


@router.post("/admin/store/ads/{order_id}/moderate")
async def admin_moderate_ad(
    order_id: int,
    payload: AdvertisingModerationRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    row = await session.get(AdvertisingOrder, order_id)
    if not row:
        raise HTTPException(status_code=404, detail="Рекламный заказ не найден")
    if row.status == "paid":
        raise HTTPException(status_code=400, detail="Оплаченный заказ нельзя отклонить")
    row.status = "approved" if payload.decision == "approve" else "rejected"
    row.moderation_note = payload.note.strip() or (
        "Реклама одобрена. Оплатите заказ в Mini App."
        if payload.decision == "approve"
        else "Реклама не прошла модерацию."
    )
    row.reviewed_by = user.id
    row.reviewed_at = utcnow()
    await _admin_log(
        session,
        admin_id=user.id,
        action=f"advertising_{row.status}",
        entity_type="user",
        entity_id=row.user_id,
        details={"order_id": row.id, "note": row.moderation_note},
    )
    await session.commit()
    try:
        from app.bot import bot

        await bot.send_message(
            row.user_id,
            (
                f"Рекламный заказ #{row.id} одобрен. Откройте магазин AniGuard для оплаты."
                if row.status == "approved"
                else f"Рекламный заказ #{row.id} отклонён. Причина: {row.moderation_note}"
            ),
        )
    except Exception:
        pass
    return _serialize_ad(row)


@router.get("/admin/store/cases")
async def admin_case_history(
    limit: int = Query(default=200, ge=1, le=1000),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    ensure_bot_admin(user)
    rows = (await session.scalars(select(CaseOpening).order_by(CaseOpening.id.desc()).limit(limit))).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "case_code": row.case_code,
            "cost": row.cost,
            "reward_type": row.reward_type,
            "reward_value": row.reward_value,
            "reward_label": row.reward_label,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/admin/store/wallet-transactions")
async def admin_wallet_transactions(
    limit: int = Query(default=300, ge=1, le=2000),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    ensure_bot_admin(user)
    rows = (
        await session.scalars(select(WalletTransaction).order_by(WalletTransaction.id.desc()).limit(limit))
    ).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "kind": row.kind,
            "amount": row.amount,
            "balance_after": row.balance_after,
            "stars": row.stars,
            "reference": row.reference,
            "details": row.details,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/admin/support/tickets")
async def admin_support_tickets(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=1000),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    ensure_bot_admin(user)
    query = select(SupportTicket).order_by(SupportTicket.updated_at.desc()).limit(limit)
    if status_filter:
        query = query.where(SupportTicket.status == status_filter)
    tickets = (await session.scalars(query)).all()
    result: list[dict[str, Any]] = []
    for ticket in tickets:
        db_user = await session.get(User, ticket.user_id)
        messages = (
            await session.scalars(
                select(SupportMessage)
                .where(SupportMessage.ticket_id == ticket.id)
                .order_by(SupportMessage.id.asc())
            )
        ).all()
        result.append(
            {
                "id": ticket.id,
                "user_id": ticket.user_id,
                "user_name": " ".join(filter(None, [db_user.first_name, db_user.last_name])) if db_user else str(ticket.user_id),
                "username": db_user.username if db_user else None,
                "status": ticket.status,
                "subject": ticket.subject,
                "created_at": ticket.created_at.isoformat(),
                "updated_at": ticket.updated_at.isoformat(),
                "messages": [_serialize_support_message(row) for row in messages],
            }
        )
    return result


@router.post("/admin/support/tickets/{ticket_id}/reply", status_code=201)
async def admin_support_reply(
    ticket_id: int,
    payload: SupportMessageRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    ticket = await session.get(SupportTicket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    ticket.status = "in_progress"
    ticket.updated_at = utcnow()
    message = SupportMessage(
        ticket_id=ticket.id,
        sender_type="support",
        sender_id=user.id,
        text=payload.text.strip(),
    )
    session.add(message)
    await _admin_log(
        session,
        admin_id=user.id,
        action="support_reply",
        entity_type="user",
        entity_id=ticket.user_id,
        details={"ticket_id": ticket.id},
    )
    await session.flush()
    await session.commit()
    try:
        from app.bot import bot

        await bot.send_message(ticket.user_id, f"Ответ AniGuard Support:\n\n{message.text}")
    except Exception:
        pass
    return _serialize_support_message(message)


@router.post("/admin/support/tickets/{ticket_id}/status")
async def admin_support_status(
    ticket_id: int,
    payload: SupportStatusRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    ticket = await session.get(SupportTicket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    ticket.status = payload.status
    ticket.updated_at = utcnow()
    await _admin_log(
        session,
        admin_id=user.id,
        action="support_status",
        entity_type="user",
        entity_id=ticket.user_id,
        details={"ticket_id": ticket.id, "status": ticket.status},
    )
    await session.commit()
    return {"ticket_id": ticket.id, "status": ticket.status}


@router.post("/admin/store/advertising-settings")
async def admin_advertising_settings(
    payload: AdvertisingSettingsRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    row = await session.get(SystemSetting, "advertising")
    values = {"channel_live_users": payload.channel_live_users}
    if row is None:
        row = SystemSetting(key="advertising", value=values, updated_by=user.id)
        session.add(row)
    else:
        row.value = values
        row.updated_by = user.id
    await _admin_log(session, admin_id=user.id, action="advertising_settings", details=values)
    await session.commit()
    return {"settings": values}


def parse_store_payment_payload(payload: str) -> dict[str, int | str]:
    parts = payload.split(":")
    if len(parts) != 5 or parts[0] not in {"agc", "aga"}:
        raise ValueError("Invalid store invoice payload")
    return {
        "prefix": parts[0],
        "user_id": int(parts[1]),
        "value": int(parts[2]),
        "payment_id": int(parts[3]),
    }


async def validate_store_payment(
    session: AsyncSession,
    *,
    payload: str,
    user_id: int,
    total_amount: int,
    currency: str,
) -> StorePayment:
    parsed = parse_store_payment_payload(payload)
    if currency != "XTR" or parsed["user_id"] != user_id:
        raise ValueError("Параметры платежа не совпадают")
    payment = await session.get(StorePayment, int(parsed["payment_id"]))
    if not payment or payment.invoice_payload != payload or payment.user_id != user_id:
        raise ValueError("Счёт не найден")
    if payment.status == "paid":
        return payment
    if payment.stars != total_amount:
        raise ValueError("Сумма платежа не совпадает")
    if payment.kind == "advertising":
        order = await session.get(AdvertisingOrder, payment.reference_id)
        if not order or order.status != "approved":
            raise ValueError("Рекламный заказ не одобрен")
    return payment


async def complete_store_payment(
    session: AsyncSession,
    *,
    payload: str,
    user_id: int,
    total_amount: int,
    currency: str,
    charge_id: str,
) -> dict[str, Any]:
    existing_charge = await session.scalar(
        select(StorePayment).where(StorePayment.telegram_payment_charge_id == charge_id)
    )
    if existing_charge:
        return {"kind": existing_charge.kind, "payment_id": existing_charge.id, "already_paid": True}
    payment = await validate_store_payment(
        session,
        payload=payload,
        user_id=user_id,
        total_amount=total_amount,
        currency=currency,
    )
    if payment.status == "paid":
        return {"kind": payment.kind, "payment_id": payment.id, "already_paid": True}
    payment.status = "paid"
    payment.telegram_payment_charge_id = charge_id
    payment.paid_at = utcnow()
    result: dict[str, Any] = {"kind": payment.kind, "payment_id": payment.id, "already_paid": False}
    if payment.kind == "coins":
        wallet = await _wallet(session, user_id)
        wallet.balance += payment.coins
        session.add(
            WalletTransaction(
                user_id=user_id,
                kind="coin_purchase",
                amount=payment.coins,
                balance_after=wallet.balance,
                stars=payment.stars,
                reference=payment.invoice_payload,
                details={"payment_id": payment.id},
            )
        )
        result.update({"coins": payment.coins, "balance": wallet.balance})
    elif payment.kind == "advertising":
        order = await session.get(AdvertisingOrder, payment.reference_id)
        if not order:
            raise ValueError("Рекламный заказ не найден")
        order.status = "paid"
        order.paid_at = utcnow()
        order.moderation_note = "Оплата получена. Реклама ожидает запуска."
        result.update({"order_id": order.id, "stars": order.total_stars})
    await session.flush()
    return result
