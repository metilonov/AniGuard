from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import aiohttp

from app.config import get_settings


@dataclass(slots=True)
class RateSnapshot:
    usd_byn: float
    star_usd: float
    updated_at: float
    source: str

    @property
    def star_byn(self) -> float:
        return self.usd_byn * self.star_usd


class ExchangeRateService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._snapshot = RateSnapshot(
            usd_byn=float(self.settings.usd_byn_fallback),
            star_usd=float(self.settings.telegram_star_usd_rate),
            updated_at=0.0,
            source="fallback",
        )
        self._lock = asyncio.Lock()

    async def snapshot(self) -> RateSnapshot:
        now = time.time()
        if now - self._snapshot.updated_at < self.settings.fx_refresh_seconds:
            return self._snapshot
        async with self._lock:
            now = time.time()
            if now - self._snapshot.updated_at < self.settings.fx_refresh_seconds:
                return self._snapshot
            try:
                timeout = aiohttp.ClientTimeout(total=3)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(self.settings.nbrb_usd_rate_url) as response:
                        payload = await response.json(content_type=None)
                        if response.status != 200:
                            raise RuntimeError(f"NBRB HTTP {response.status}")
                        scale = float(payload.get("Cur_Scale") or 1)
                        rate = float(payload["Cur_OfficialRate"]) / scale
                self._snapshot = RateSnapshot(
                    usd_byn=rate,
                    star_usd=float(self.settings.telegram_star_usd_rate),
                    updated_at=now,
                    source="nbrb",
                )
            except Exception:
                # Keep the last successful rate. On the very first request use
                # the explicit environment fallback rather than breaking admin.
                if self._snapshot.updated_at == 0:
                    self._snapshot = RateSnapshot(
                        usd_byn=float(self.settings.usd_byn_fallback),
                        star_usd=float(self.settings.telegram_star_usd_rate),
                        updated_at=now,
                        source="fallback",
                    )
            return self._snapshot

    async def stars_to_byn(self, stars: int | float) -> tuple[float, RateSnapshot]:
        snapshot = await self.snapshot()
        return round(float(stars) * snapshot.star_byn, 2), snapshot


exchange_rates = ExchangeRateService()
