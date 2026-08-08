from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from .models import utcnow


class NinjaVillageGovernment(Base):
    __tablename__ = "naruto_v3_village_governments"

    village: Mapped[str] = mapped_column(String(24), primary_key=True)
    kage_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    council_member_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    treasury: Mapped[int] = mapped_column(Integer, default=0)
    tax_rate: Mapped[int] = mapped_column(Integer, default=0)
    trust: Mapped[int] = mapped_column(Integer, default=50)
    ideology: Mapped[str] = mapped_column(String(24), default="balanced")
    project_key: Mapped[str | None] = mapped_column(String(32))
    project_progress: Mapped[int] = mapped_column(Integer, default=0)
    project_target: Mapped[int] = mapped_column(Integer, default=0)
    upgrades: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NinjaVillageVote(Base):
    __tablename__ = "naruto_v3_village_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    village: Mapped[str] = mapped_column(String(24), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_by: Mapped[int] = mapped_column(BigInteger, index=True)
    subject_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    candidates: Mapped[list[int]] = mapped_column(JSON, default=list)
    votes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NinjaCriminalOrg(Base):
    __tablename__ = "naruto_v3_criminal_orgs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    leader_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    treasury: Mapped[int] = mapped_column(Integer, default=0)
    secrecy: Mapped[int] = mapped_column(Integer, default=100)
    heat: Mapped[int] = mapped_column(Integer, default=0, index=True)
    base_region: Mapped[str] = mapped_column(String(64), default="unknown")
    upgrades: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NinjaCriminalMember(Base):
    __tablename__ = "naruto_v3_criminal_members"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_naruto_v3_criminal_member"),
        UniqueConstraint("user_id", name="uq_naruto_v3_criminal_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(24), default="initiate")
    contribution: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NinjaCriminalInvite(Base):
    __tablename__ = "naruto_v3_criminal_invites"
    __table_args__ = (UniqueConstraint("org_id", "target_user_id", name="uq_naruto_v3_criminal_invite"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, index=True)
    inviter_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NinjaBijuuState(Base):
    __tablename__ = "naruto_v3_bijuu_states"

    bijuu_key: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="free", index=True)
    region: Mapped[str] = mapped_column(String(64), default="unknown")
    host_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    candidate_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    hp: Mapped[int] = mapped_column(Integer, default=100000)
    max_hp: Mapped[int] = mapped_column(Integer, default=100000)
    approvals: Mapped[list[int]] = mapped_column(JSON, default=list)
    contributors: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NinjaWorldChronicle(Base):
    __tablename__ = "naruto_v3_world_chronicle"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    village: Mapped[str | None] = mapped_column(String(24), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
