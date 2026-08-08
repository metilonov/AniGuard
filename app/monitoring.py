from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete

from app.config import get_settings
from app.db import SessionFactory
from app.models import ResourceSample

settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)



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


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    raw = _read_text(path)
    if raw is None or raw == "" or raw == "max":
        return None
    try:
        return int(raw.split()[0])
    except (ValueError, IndexError):
        return None



def _directory_size_bytes(root: Path) -> int:
    """Return the size of one bot's project directory without following links."""
    try:
        if root.is_file():
            return root.stat().st_size
        if not root.exists():
            return 0
    except OSError:
        return 0

    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


@dataclass(slots=True)
class ResourceState:
    provider: str = "process"
    status: str = "starting"
    cpu_percent: float = 0.0
    cpu_limit: float = 4.0
    cpu_source: str = "python-process"
    memory_usage_mb: float = 0.0
    memory_limit_mb: float = 2048.0
    memory_percent: float = 0.0
    memory_source: str = "python-process"
    disk_usage_mb: float = 0.0
    disk_limit_mb: float = 15360.0
    disk_percent: float = 0.0
    disk_scope: str = "project-directory"
    uptime_seconds: int = 0
    uptime: str = "0 сек."
    updated_at: str = ""
    latency_ms: float = 0.0
    agent_url: str | None = None  # kept for response compatibility; always None in v22
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResourceMonitor:
    """Collect resource usage for the AniGuard container only.

    Priority:
      1. Linux cgroup metrics for the current AniGuard container.
      2. The current Python process as the last fallback.

    No requests are sent to BotHost agent hosts. The browser reads these metrics
    from AniGuard's own protected ``/api/admin/live`` endpoint through the
    project's public domain. CPU/RAM refresh every second; project-directory
    disk usage is cached because walking every file once per second is wasteful.
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
        self._last_process_cpu_monotonic: float | None = None
        self._last_cgroup_cpu_seconds: float | None = None
        self._last_cgroup_cpu_monotonic: float | None = None
        self._task: asyncio.Task[Any] | None = None
        self._stop = asyncio.Event()
        self._last_persist = 0.0
        self._last_prune = 0.0
        self._disk_cache_mb = 0.0
        self._last_disk_scan = 0.0

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
        """Collect local container metrics and expose them through AniGuard API."""
        started = time.perf_counter()
        local = self._collect_local()
        local.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        local.agent_url = None
        local.error = None
        return local

    def _collect_local(self) -> ResourceState:
        uptime_seconds = int(time.monotonic() - self._start_monotonic)
        memory_mb, memory_limit_mb, memory_source = self._read_container_memory()
        cpu_percent, cpu_source = self._read_container_cpu_percent()
        disk_usage_mb = self._project_disk_usage_mb()
        disk_limit_mb = max(1.0, float(settings.bothost_disk_limit_gb) * 1024)
        memory_limit_mb = max(1.0, memory_limit_mb)
        provider = "cgroup" if memory_source.startswith("cgroup") or cpu_source.startswith("cgroup") else "process"
        return ResourceState(
            provider=provider,
            status="online",
            cpu_percent=round(cpu_percent, 2),
            cpu_limit=float(settings.bothost_cpu_limit),
            cpu_source=cpu_source,
            memory_usage_mb=round(memory_mb, 2),
            memory_limit_mb=round(memory_limit_mb, 2),
            memory_percent=round(memory_mb / memory_limit_mb * 100, 2),
            memory_source=memory_source,
            disk_usage_mb=round(disk_usage_mb, 2),
            disk_limit_mb=disk_limit_mb,
            disk_percent=round(disk_usage_mb / disk_limit_mb * 100, 2),
            disk_scope="project-directory",
            uptime_seconds=uptime_seconds,
            uptime=format_uptime(uptime_seconds),
            updated_at=utcnow().isoformat(),
        )

    @staticmethod
    def _self_cgroup_entries() -> list[tuple[set[str], str]]:
        raw = _read_text(Path("/proc/self/cgroup")) or ""
        entries: list[tuple[set[str], str]] = []
        for line in raw.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            controllers = {item for item in parts[1].split(",") if item}
            entries.append((controllers, parts[2].lstrip("/")))
        return entries

    def _cgroup_paths(self, filename: str, controller: str | None = None) -> list[Path]:
        roots = [Path("/sys/fs/cgroup")]
        if controller:
            roots.insert(0, Path("/sys/fs/cgroup") / controller)
        candidates: list[Path] = []
        for root in roots:
            candidates.append(root / filename)
        for controllers, relative in self._self_cgroup_entries():
            if controller and controllers and controller not in controllers:
                continue
            for root in roots:
                if relative:
                    candidates.append(root / relative / filename)
        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    def _first_text(self, filename: str, controller: str | None = None) -> tuple[str, Path] | None:
        for path in self._cgroup_paths(filename, controller):
            raw = _read_text(path)
            if raw is not None:
                return raw, path
        return None

    def _read_container_memory(self) -> tuple[float, float, str]:
        configured_limit_mb = max(1.0, float(settings.bothost_ram_limit_mb))

        current_v2 = self._first_text("memory.current")
        if current_v2:
            try:
                usage_bytes = int(current_v2[0])
            except ValueError:
                usage_bytes = 0
            max_v2 = self._first_text("memory.max")
            limit_bytes: int | None = None
            if max_v2 and max_v2[0] != "max":
                try:
                    limit_bytes = int(max_v2[0])
                except ValueError:
                    limit_bytes = None
            limit_mb = self._valid_memory_limit_mb(limit_bytes, configured_limit_mb)
            return usage_bytes / (1024 * 1024), limit_mb, "cgroup-v2"

        current_v1 = self._first_text("memory.usage_in_bytes", "memory")
        if current_v1:
            try:
                usage_bytes = int(current_v1[0])
            except ValueError:
                usage_bytes = 0
            max_v1 = self._first_text("memory.limit_in_bytes", "memory")
            try:
                limit_bytes = int(max_v1[0]) if max_v1 else None
            except ValueError:
                limit_bytes = None
            limit_mb = self._valid_memory_limit_mb(limit_bytes, configured_limit_mb)
            return usage_bytes / (1024 * 1024), limit_mb, "cgroup-v1"

        return self._read_rss_mb(), configured_limit_mb, "python-process"

    @staticmethod
    def _valid_memory_limit_mb(limit_bytes: int | None, configured_limit_mb: float) -> float:
        # Ignore host-wide/unlimited sentinel values. A realistic container limit
        # below 1 PiB is accepted; otherwise use the configured tariff limit.
        if limit_bytes and 0 < limit_bytes < 1024**5:
            return max(1.0, limit_bytes / (1024 * 1024))
        return configured_limit_mb

    def _read_cgroup_cpu_usage_seconds(self) -> tuple[float, str] | None:
        stat_v2 = self._first_text("cpu.stat")
        if stat_v2:
            values: dict[str, int] = {}
            for line in stat_v2[0].splitlines():
                parts = line.split()
                if len(parts) == 2:
                    try:
                        values[parts[0]] = int(parts[1])
                    except ValueError:
                        continue
            if "usage_usec" in values:
                return values["usage_usec"] / 1_000_000, "cgroup-v2"
            if "usage_nsec" in values:
                return values["usage_nsec"] / 1_000_000_000, "cgroup-v2"

        usage_v1 = self._first_text("cpuacct.usage", "cpuacct")
        if usage_v1:
            try:
                return int(usage_v1[0]) / 1_000_000_000, "cgroup-v1"
            except ValueError:
                pass
        return None

    def _cgroup_cpu_capacity(self) -> float:
        configured = max(0.01, float(settings.bothost_cpu_limit))
        cpu_max = self._first_text("cpu.max")
        if cpu_max:
            parts = cpu_max[0].split()
            if len(parts) >= 2 and parts[0] != "max":
                try:
                    quota = float(parts[0])
                    period = float(parts[1])
                    if quota > 0 and period > 0:
                        return max(0.01, quota / period)
                except ValueError:
                    pass

        quota_v1 = self._first_text("cpu.cfs_quota_us", "cpu")
        period_v1 = self._first_text("cpu.cfs_period_us", "cpu")
        if quota_v1 and period_v1:
            try:
                quota = float(quota_v1[0])
                period = float(period_v1[0])
                if quota > 0 and period > 0:
                    return max(0.01, quota / period)
            except ValueError:
                pass
        return configured

    def _read_container_cpu_percent(self) -> tuple[float, str]:
        cgroup = self._read_cgroup_cpu_usage_seconds()
        now = time.monotonic()
        if cgroup:
            usage_seconds, source = cgroup
            if self._last_cgroup_cpu_seconds is None or self._last_cgroup_cpu_monotonic is None:
                self._last_cgroup_cpu_seconds = usage_seconds
                self._last_cgroup_cpu_monotonic = now
                return 0.0, source
            usage_delta = max(0.0, usage_seconds - self._last_cgroup_cpu_seconds)
            wall_delta = max(0.001, now - self._last_cgroup_cpu_monotonic)
            self._last_cgroup_cpu_seconds = usage_seconds
            self._last_cgroup_cpu_monotonic = now
            capacity = self._cgroup_cpu_capacity()
            return max(0.0, usage_delta / wall_delta / capacity * 100), source
        return self._read_process_cpu_percent(), "python-process"

    @staticmethod
    def _read_rss_mb() -> float:
        try:
            for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    kb = float(line.split()[1])
                    return kb / 1024
        except (OSError, ValueError, IndexError):
            pass
        return 0.0

    def _read_process_cpu_percent(self) -> float:
        try:
            fields = Path("/proc/self/stat").read_text(encoding="utf-8").split()
            ticks = int(fields[13]) + int(fields[14])
            now = time.monotonic()
            if self._last_process_ticks is None or self._last_process_cpu_monotonic is None:
                self._last_process_ticks = ticks
                self._last_process_cpu_monotonic = now
                return 0.0
            tick_rate = float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
            process_seconds = (ticks - self._last_process_ticks) / tick_rate
            wall_seconds = max(0.001, now - self._last_process_cpu_monotonic)
            self._last_process_ticks = ticks
            self._last_process_cpu_monotonic = now
            capacity = max(0.01, float(settings.bothost_cpu_limit))
            return max(0.0, process_seconds / wall_seconds / capacity * 100)
        except (OSError, ValueError, IndexError, KeyError):
            return 0.0

    def _project_disk_usage_mb(self) -> float:
        now = time.monotonic()
        if now - self._last_disk_scan < settings.bothost_disk_scan_interval_seconds and self._last_disk_scan > 0:
            return self._disk_cache_mb
        root = Path(settings.bothost_project_dir)
        self._disk_cache_mb = _directory_size_bytes(root) / (1024 * 1024)
        self._last_disk_scan = now
        return self._disk_cache_mb

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
