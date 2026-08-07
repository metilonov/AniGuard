from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import router as api_router
from app.store import router as store_router
from app.bot import start_polling, stop_bot
from app.config import get_settings
from app.db import init_db
from app.monitoring import resource_monitor


settings = get_settings()
STATIC_DIR = Path(__file__).resolve().parent / "static"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    bot_task = asyncio.create_task(start_polling(), name="telegram-bot-polling")
    resource_task = await resource_monitor.start()
    app.state.bot_task = bot_task
    app.state.resource_task = resource_task
    try:
        yield
    finally:
        bot_task.cancel()
        await asyncio.gather(bot_task, return_exceptions=True)
        await resource_monitor.stop()
        try:
            await stop_bot()
        except Exception:
            pass


app = FastAPI(title="AniGuard", version="2.0.0", lifespan=lifespan)
app.include_router(api_router)
app.include_router(store_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/panel", status_code=307)


@app.get("/panel", include_in_schema=False)
@app.get("/panel/", include_in_schema=False)
@app.get("/shop", include_in_schema=False)
@app.get("/shop/", include_in_schema=False)
@app.get("/account", include_in_schema=False)
@app.get("/account/", include_in_schema=False)
@app.get("/group", include_in_schema=False)
@app.get("/group/", include_in_schema=False)
async def mini_app() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers=NO_CACHE_HEADERS,
    )


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def admin_panel() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "admin.html",
        headers=NO_CACHE_HEADERS,
    )
