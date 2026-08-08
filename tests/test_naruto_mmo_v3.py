from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.naruto_game.models import NinjaProfile, utcnow
from app.naruto_game.v3 import (
    BIJUU,
    CRIMINAL_ROLES,
    IDEOLOGIES,
    PROJECTS,
    bijuu_text,
    election_nominate,
    election_open,
    election_status,
    election_vote,
    ensure_bijuu,
    ensure_governments,
    government_text,
    project_contribute,
    project_start,
)
from app.naruto_game.v3_models import NinjaBijuuState, NinjaVillageGovernment, NinjaVillageVote


def _profile(user_id: int, name: str, *, village: str = "konoha", level: int = 50, reputation: int = 1000, ryo: int = 500_000) -> NinjaProfile:
    return NinjaProfile(
        user_id=user_id,
        name=name,
        village=village,
        bloodline="none",
        primary_element="fire",
        ninja_rank="jonin",
        level=level,
        reputation=reputation,
        ryo=ryo,
        energy=100,
        hp=100,
        max_hp=100,
        chakra=100,
        max_chakra=100,
        morality={},
        flags={},
        achievements=[],
        titles=[],
        relations={},
        summons=[],
        biju={"key": None, "trust": 0, "chakra": 0},
        home={},
        injuries=[],
        counters={},
    )


def test_v3_catalogs_and_tables_are_registered() -> None:
    assert len(BIJUU) == 9
    assert {"hospital", "walls", "academy", "market", "intel", "lab"} <= set(PROJECTS)
    assert "military" in IDEOLOGIES and "scientific" in IDEOLOGIES
    assert CRIMINAL_ROLES["leader"].startswith("👑")
    tables = set(Base.metadata.tables)
    assert "naruto_v3_village_governments" in tables
    assert "naruto_v3_criminal_orgs" in tables
    assert "naruto_v3_bijuu_states" in tables
    assert "naruto_v3_world_chronicle" in tables


def test_v3_government_election_project_and_bijuu_flow() -> None:
    pytest.importorskip("aiosqlite")

    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with Session() as session:
            a = _profile(101, "Ren")
            b = _profile(202, "Kaito")
            session.add_all([a, b])
            await session.flush()

            await ensure_governments(session)
            await ensure_bijuu(session)
            assert len((await session.scalars(select(NinjaVillageGovernment))).all()) >= 5
            assert len((await session.scalars(select(NinjaBijuuState))).all()) == 9

            text = await election_open(session, 101)
            assert "Выборы" in text
            await election_nominate(session, 101)
            await election_nominate(session, 202)
            await election_vote(session, 101, 101)
            await election_vote(session, 202, 101)
            vote = await session.scalar(select(NinjaVillageVote).where(NinjaVillageVote.status == "open"))
            assert vote is not None
            vote.closes_at = utcnow() - timedelta(seconds=1)
            result = await election_status(session, 101)
            assert "Новый Каге" in result

            gov = await session.get(NinjaVillageGovernment, "konoha")
            assert gov is not None and gov.kage_user_id == 101
            await project_start(session, 101, "hospital")
            target = gov.project_target
            a.ryo = max(a.ryo, target + 1000)
            done = await project_contribute(session, 101, target)
            assert "завершён" in done
            assert int(dict(gov.upgrades).get("hospital", 0)) == 1

            gov_text = await government_text(session, 101)
            assert "правительство MMO V3" in gov_text
            beast_text = await bijuu_text(session, 101)
            assert "Шукаку" in beast_text and "Курама" in beast_text

        await engine.dispose()

    asyncio.run(scenario())
