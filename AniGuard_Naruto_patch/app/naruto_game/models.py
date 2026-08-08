from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NinjaProfile(Base):
    __tablename__ = "naruto_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    village: Mapped[str] = mapped_column(String(24), index=True)
    bloodline: Mapped[str] = mapped_column(String(32), default="none", index=True)
    primary_element: Mapped[str] = mapped_column(String(16))
    secondary_element: Mapped[str | None] = mapped_column(String(16))
    ninja_rank: Mapped[str] = mapped_column(String(24), default="academy", index=True)
    level: Mapped[int] = mapped_column(Integer, default=1, index=True)
    xp: Mapped[int] = mapped_column(Integer, default=0)

    ryo: Mapped[int] = mapped_column(Integer, default=500)
    chakra_crystals: Mapped[int] = mapped_column(Integer, default=0)
    village_points: Mapped[int] = mapped_column(Integer, default=0)
    arena_tokens: Mapped[int] = mapped_column(Integer, default=0)
    raid_seals: Mapped[int] = mapped_column(Integer, default=0)

    hp: Mapped[int] = mapped_column(Integer, default=100)
    max_hp: Mapped[int] = mapped_column(Integer, default=100)
    chakra: Mapped[int] = mapped_column(Integer, default=100)
    max_chakra: Mapped[int] = mapped_column(Integer, default=100)
    ninjutsu: Mapped[int] = mapped_column(Integer, default=12)
    taijutsu: Mapped[int] = mapped_column(Integer, default=12)
    genjutsu: Mapped[int] = mapped_column(Integer, default=10)
    defense: Mapped[int] = mapped_column(Integer, default=10)
    speed: Mapped[int] = mapped_column(Integer, default=11)
    accuracy: Mapped[int] = mapped_column(Integer, default=10)
    chakra_control: Mapped[int] = mapped_column(Integer, default=10)
    genjutsu_resist: Mapped[int] = mapped_column(Integer, default=10)
    crit_chance: Mapped[float] = mapped_column(Float, default=0.05)
    reputation: Mapped[int] = mapped_column(Integer, default=0, index=True)

    energy: Mapped[int] = mapped_column(Integer, default=100)
    energy_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_daily_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_raid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    missions_completed: Mapped[int] = mapped_column(Integer, default=0)
    pvp_wins: Mapped[int] = mapped_column(Integer, default=0)
    pvp_losses: Mapped[int] = mapped_column(Integer, default=0)
    arena_rating: Mapped[int] = mapped_column(Integer, default=1000, index=True)
    story_chapter: Mapped[int] = mapped_column(Integer, default=1)

    nukenin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    wanted_reward: Mapped[int] = mapped_column(Integer, default=0, index=True)
    mentor: Mapped[str | None] = mapped_column(String(32))
    profession: Mapped[str | None] = mapped_column(String(32))
    active_title: Mapped[str | None] = mapped_column(String(64))

    morality: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    flags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    achievements: Mapped[list[str]] = mapped_column(JSON, default=list)
    titles: Mapped[list[str]] = mapped_column(JSON, default=list)
    relations: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    summons: Mapped[list[str]] = mapped_column(JSON, default=list)
    biju: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    home: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    injuries: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    counters: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NinjaTechnique(Base):
    __tablename__ = "naruto_techniques"
    __table_args__ = (UniqueConstraint("user_id", "technique_key", name="uq_naruto_technique"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    technique_key: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    mastery: Mapped[int] = mapped_column(Integer, default=0)
    equipped: Mapped[bool] = mapped_column(Boolean, default=True)
    learned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NinjaItem(Base):
    __tablename__ = "naruto_items"
    __table_args__ = (UniqueConstraint("user_id", "item_key", name="uq_naruto_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    item_key: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    upgrade_level: Mapped[int] = mapped_column(Integer, default=0)
    equipped_slot: Mapped[str | None] = mapped_column(String(32))
    quality: Mapped[str] = mapped_column(String(24), default="normal")
    extra_stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    crafted_by: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NinjaCard(Base):
    __tablename__ = "naruto_cards"
    __table_args__ = (UniqueConstraint("user_id", "card_key", name="uq_naruto_card"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    card_key: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    stars: Mapped[int] = mapped_column(Integer, default=1)
    fragments: Mapped[int] = mapped_column(Integer, default=0)
    copies: Mapped[int] = mapped_column(Integer, default=1)
    obtained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NinjaBattle(Base):
    __tablename__ = "naruto_battles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    battle_type: Mapped[str] = mapped_column(String(24), default="pve")
    opponent_key: Mapped[str] = mapped_column(String(64))
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NinjaClan(Base):
    __tablename__ = "naruto_clans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    leader_id: Mapped[int] = mapped_column(BigInteger, index=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    treasury: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[int] = mapped_column(Integer, default=1000, index=True)
    village: Mapped[str | None] = mapped_column(String(24))
    description: Mapped[str] = mapped_column(Text, default="")
    upgrades: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NinjaClanMember(Base):
    __tablename__ = "naruto_clan_members"
    __table_args__ = (UniqueConstraint("user_id", name="uq_naruto_clan_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    clan_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(24), default="shinobi")
    contribution: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NinjaAuction(Base):
    __tablename__ = "naruto_auctions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_id: Mapped[int] = mapped_column(BigInteger, index=True)
    item_key: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    price: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    buyer_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NinjaWorldState(Base):
    __tablename__ = "naruto_world_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NinjaEventLog(Base):
    __tablename__ = "naruto_event_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class NinjaTrade(Base):
    __tablename__ = "naruto_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiator_id: Mapped[int] = mapped_column(BigInteger, index=True)
    partner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    give_ryo: Mapped[int] = mapped_column(Integer, default=0)
    take_ryo: Mapped[int] = mapped_column(Integer, default=0)
    give_items: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    take_items: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NinjaPlayerContract(Base):
    __tablename__ = "naruto_player_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    creator_id: Mapped[int] = mapped_column(BigInteger, index=True)
    assignee_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    contract_type: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[int] = mapped_column(Integer, default=1)
    reward_ryo: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NinjaCaravan(Base):
    __tablename__ = "naruto_caravans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    origin: Mapped[str] = mapped_column(String(32))
    destination: Mapped[str] = mapped_column(String(32))
    cargo: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    cargo_value: Mapped[int] = mapped_column(Integer, default=0)
    insured: Mapped[bool] = mapped_column(Boolean, default=False)
    escort_user_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default="traveling", index=True)
    outcome: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    arrives_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NinjaCustomTechnique(Base):
    __tablename__ = "naruto_custom_techniques"
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_naruto_custom_technique"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    slug: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(64))
    element: Mapped[str | None] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(24), default="ninjutsu")
    chakra_cost: Mapped[int] = mapped_column(Integer)
    power: Mapped[int] = mapped_column(Integer)
    accuracy: Mapped[float] = mapped_column(Float, default=0.90)
    cooldown: Mapped[int] = mapped_column(Integer, default=2)
    level: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NinjaDynasty(Base):
    __tablename__ = "naruto_dynasties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    founder_id: Mapped[int] = mapped_column(BigInteger, index=True)
    prestige: Mapped[int] = mapped_column(Integer, default=0)
    members: Mapped[list[int]] = mapped_column(JSON, default=list)
    legacy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NinjaBond(Base):
    __tablename__ = "naruto_bonds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    partner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    shared_home_level: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NinjaSquad(Base):
    __tablename__ = "naruto_squads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    leader_id: Mapped[int] = mapped_column(BigInteger, index=True)
    members: Mapped[list[int]] = mapped_column(JSON, default=list)
    purpose: Mapped[str] = mapped_column(String(32), default="missions")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
