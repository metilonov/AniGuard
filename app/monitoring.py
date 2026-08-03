from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
from sqlalchemy import delete

from app.config import get_settings
from app.db import SessionFactory
from app.models import ResourceSample

settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_memory_mb(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        # Agent responses may use bytes for numeric fields.
        return number / (1024 * 1024) if number > 1024 * 1024 else number
    raw = str(value).strip().replace(",", ".")
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*([KMGT]?B)?", raw, re.I)
    if not match:
        return 0.0
    number = float(match.group(1))
    unit = (match.group(2) or "MB").upper()
    factor = {"KB": 1 / 1024, "MB": 1, "GB": 1024, "TB": 1024 * 1024, "B": 1 / (1024 * 1024)}.get(unit, 1)
    return number * factor


def _parse_uptime_seconds(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    raw = str(value).strip().lower()
    total = 0
    patterns = [
        (r"(\d+)\s*(?:д|дн|day)", 86400),
        (r"(\d+)\s*(?:ч|час|hour|h)", 3600),
        (r"(\d+)\s*(?:м|мин|minute|min)", 60),
        (r"(\d+)\s*(?:с|сек|second|sec)", 1),
    ]
    for pattern, factor in patterns:
        match = re.search(pattern, raw)
        if match:
            total += int(match.group(1)) * factor
    if total:
        return total
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return 0


def format_uptime(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} дн.")
    if hours or days:
        parts.append(f"{hours} ч.")
    if minutes or hours or days:
        parts.append(f"{minutes} мин.")
    if not parts:
        parts.append(f"{seconds} сек.")
    return " ".join(parts)


@dataclass(slots=True)
class ResourceState:
    provider: str = "local"
    status: str = "starting"
    cpu_percent: float = 0.0
    cpu_limit: float = 4.0
    memory_usage_mb: float = 0.0
    memory_limit_mb: float = 2048.0
    memory_percent: float = 0.0
    disk_usage_mb: float = 0.0
    disk_limit_mb: float = 15360.0
    disk_percent: float = 0.0
    uptime_seconds: int = 0
    uptime: str = "0 сек."
    updated_at: str = ""
    latency_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResourceMonitor:
    """One-second resource collector with a lightweight in-memory snapshot.

    The admin browser polls `/api/admin/live` once per second. The collector is
    also refreshed once per second in the background, while database samples are
    persisted less frequently to prevent tens of thousands of rows per day.
    """

    def __init__(self) -> None:
        self.state = ResourceState(
            cpu_limit=float(settings.bothost_cpu_limit),
            memory_limit_mb=float(settings.bothost_ram_limit_mb),
            disk_limit_mb=float(settings.bothost_disk_limit_gb) * 1024,
        )
        self._lock = asyncio.Lock()
        self._start_monotonic = time.monotonic()
        self._last_process_ticks: int | None = None
        self._last_cpu_monotonic: float | None = None
        self._task: asyncio.Task[Any] | None = None
        self._stop = asyncio.Event()
        self._last_persist = 0.0
        self._last_prune = 0.0

    async def start(self) -> asyncio.Task[Any]:
        if self._task and not self._task.done():
            return self._task
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="resource-monitor-1s")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return self.state.to_dict()

    async def refresh_now(self) -> dict[str, Any]:
        state = await self._collect()
        async with self._lock:
            self.state = state
        return state.to_dict()

    async def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                state = await self._collect()
                async with self._lock:
                    self.state = state
                now = time.monotonic()
                if now - self._last_persist >= settings.resource_persist_interval_seconds:
                    await self._persist(state)
                    self._last_persist = now
                if now - self._last_prune >= 3600:
                    await self._prune()
                    self._last_prune = now
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self._lock:
                    self.state.status = "degraded"
                    self.state.error = str(exc)
                    self.state.updated_at = utcnow().isoformat()
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.05, settings.resource_poll_interval_seconds - elapsed))

    async def _collect(self) -> ResourceState:
        local = self._collect_local()
        if not settings.bothost_bot_id:
            local.error = "BOT_ID не найден; показаны локальные метрики контейнера"
            return local
        started = time.perf_counter()
        url = f"{settings.bothost_agent_url.rstrip('/')}/api/bots/{settings.bothost_bot_id}/stats"
        timeout = aiohttp.ClientTimeout(total=0.85)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers={"X-Bot-ID": settings.bothost_bot_id}) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 400:
                        raise RuntimeError(str(payload.get("detail") or payload.get("error") or f"HTTP {response.status}"))
            stats = payload.get("stats") if isinstance(payload, dict) else None
            if not isinstance(stats, dict):
                stats = payload if isinstance(payload, dict) else {}
            cpu = float(stats.get("cpu_percent", stats.get("cpu", local.cpu_percent)) or 0)
            memory_mb = _parse_memory_mb(stats.get("memory_usage", stats.get("memory_usage_mb", stats.get("memory", local.memory_usage_mb))))
            memory_percent = float(stats.get("memory_percent", 0) or 0)
            if memory_percent <= 0 and settings.bothost_ram_limit_mb:
                memory_percent = memory_mb / settings.bothost_ram_limit_mb * 100
            uptime_seconds = _parse_uptime_seconds(stats.get("uptime_seconds", stats.get("uptime"))) or local.uptime_seconds
            local.provider = "bothost"
            local.status = "online" if payload.get("ok", True) else "degraded"
            local.cpu_percent = round(max(0.0, cpu), 2)
            local.memory_usage_mb = round(max(0.0, memory_mb), 2)
            local.memory_percent = round(max(0.0, memory_percent), 2)
            local.uptime_seconds = uptime_seconds
            local.uptime = format_uptime(uptime_seconds)
            local.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            local.error = None
            return local
        except Exception as exc:
            local.provider = "local-fallback"
            local.status = "degraded"
            local.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            local.error = f"BotHost API недоступен: {exc}"
            return local

    def _collect_local(self) -> ResourceState:
        uptime_seconds = int(time.monotonic() - self._start_monotonic)
        memory_mb = self._read_rss_mb()
        cpu_percent = self._read_cpu_percent()
        disk_root = Path(settings.data_dir) if Path(settings.data_dir).exists() else Path("/")
        try:
            total, used, _free = shutil.disk_usage(disk_root)
            disk_usage_mb = used / (1024 * 1024)
        except OSError:
            disk_usage_mb = 0.0
        disk_limit_mb = max(1.0, float(settings.bothost_disk_limit_gb) * 1024)
        memory_limit_mb = max(1.0, float(settings.bothost_ram_limit_mb))
        return ResourceState(
            provider="local",
            status="online",
            cpu_percent=round(cpu_percent, 2),
            cpu_limit=float(settings.bothost_cpu_limit),
            memory_usage_mb=round(memory_mb, 2),
            memory_limit_mb=memory_limit_mb,
            memory_percent=round(memory_mb / memory_limit_mb * 100, 2),
            disk_usage_mb=round(disk_usage_mb, 2),
            disk_limit_mb=disk_limit_mb,
            disk_percent=round(disk_usage_mb / disk_limit_mb * 100, 2),
            uptime_seconds=uptime_seconds,
            uptime=format_uptime(uptime_seconds),
            updated_at=utcnow().isoformat(),
        )

    @staticmethod
    def _read_rss_mb() -> float:
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    kb = float(line.split()[1])
                    return kb / 1024
        except (OSError, ValueError, IndexError):
            pass
        return 0.0

    def _read_cpu_percent(self) -> float:
        try:
            fields = Path("/proc/self/stat").read_text().split()
            ticks = int(fields[13]) + int(fields[14])
            now = time.monotonic()
            if self._last_process_ticks is None or self._last_cpu_monotonic is None:
                self._last_process_ticks = ticks
                self._last_cpu_monotonic = now
                return 0.0
            tick_rate = float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
            process_seconds = (ticks - self._last_process_ticks) / tick_rate
            wall_seconds = max(0.001, now - self._last_cpu_monotonic)
            self._last_process_ticks = ticks
            self._last_cpu_monotonic = now
            # Percentage of a single core, matching common container metrics.
            return max(0.0, process_seconds / wall_seconds * 100)
        except (OSError, ValueError, IndexError, KeyError):
            return 0.0

    async def _persist(self, state: ResourceState) -> None:
        async with SessionFactory() as session:
            session.add(ResourceSample(
                provider=state.provider,
                status=state.status,
                cpu_percent=state.cpu_percent,
                memory_usage_mb=state.memory_usage_mb,
                memory_percent=state.memory_percent,
                disk_usage_mb=state.disk_usage_mb,
                disk_percent=state.disk_percent,
                uptime_seconds=state.uptime_seconds,
                error=state.error,
            ))
            await session.commit()

    async def _prune(self) -> None:
        cutoff = utcnow() - timedelta(days=max(1, settings.resource_history_days))
        async with SessionFactory() as session:
            await session.execute(delete(ResourceSample).where(ResourceSample.collected_at < cutoff))
            await session.commit()


resource_monitor = ResourceMonitor()
